"""Turn raw request metrics into health-monitor KPIs, activity stats, and sizing recommendations.

Pure functions over :class:`RequestMetric` rows, so the numbers are unit-testable and do not
depend on the storage backend.

Attribution: each request's time splits into SQL warehouse time, Lakebase time, and the rest
("app time": Python work plus Databricks REST API calls). Recommendations look at where the
time of *slow* requests went, so they point at the resource that is actually the bottleneck.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.app.core.activities import activity_for
from backend.app.models.performance import (
    ActivityStats,
    LatencyBucket,
    PerformanceKpis,
    PerformanceSummary,
    RecommendationSeverity,
    RecommendationTarget,
    RequestMetric,
    ScalingRecommendation,
    SlowRequest,
)

MIN_SAMPLE = 20
MIN_ACTIVITY_SAMPLE = 3
DOMINANT_SHARE = 0.5
SLOW_RATE_WARNING = 0.10
PEAK_CONCURRENCY_WARNING = 8
RECOMMENDATION_LIMIT = 10
_SEVERITY_ORDER = {
    RecommendationSeverity.CRITICAL: 0,
    RecommendationSeverity.WARNING: 1,
    RecommendationSeverity.INFO: 2,
}


@dataclass(frozen=True)
class Attribution:
    """Milliseconds split for one request or a group of requests."""

    total: float
    warehouse: float
    lakebase: float
    app: float


def split(metric: RequestMetric) -> Attribution:
    """Split one request's total time across warehouse, Lakebase, and app time."""
    return Attribution(
        total=metric.duration_ms,
        warehouse=min(metric.warehouse_ms, metric.duration_ms),
        lakebase=min(metric.lakebase_ms, max(0.0, metric.duration_ms - metric.warehouse_ms)),
        app=metric.app_ms,
    )


def expected_ms(metric: RequestMetric) -> float:
    """Expected time for the request's activity, from the catalog."""
    return activity_for(metric.method, metric.route).expected_ms


def is_slow(metric: RequestMetric) -> bool:
    """Whether the request took longer than its activity's expected time."""
    return metric.duration_ms > expected_ms(metric)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty list.

    Args:
        values: Sample values.
        pct: Percentile between 0 and 100.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(pct / 100 * len(ordered) + 0.5 - 1e-9))
    return ordered[min(rank, len(ordered)) - 1]


def peak_concurrency(metrics: list[RequestMetric]) -> tuple[int, dict[datetime, int]]:
    """Maximum requests in flight at once, overall and per hour (UTC hour the peak started in).

    In flight means ``[recorded_at - duration, recorded_at]``.
    """
    events: list[tuple[datetime, int]] = []
    for metric in metrics:
        end = metric.recorded_at
        start = end - timedelta(milliseconds=metric.duration_ms)
        events.append((start, 1))
        events.append((end, -1))
    # Process ends before starts at the same instant so back-to-back requests do not overlap.
    events.sort(key=lambda e: (e[0], e[1]))
    current = 0
    overall = 0
    by_hour: dict[datetime, int] = defaultdict(int)
    for when, delta in events:
        current += delta
        if delta > 0:
            overall = max(overall, current)
            hour = when.replace(minute=0, second=0, microsecond=0)
            by_hour[hour] = max(by_hour[hour], current)
    return overall, dict(by_hour)


def _share(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole > 0 else None


def build_kpis(metrics: list[RequestMetric], peak: int) -> PerformanceKpis:
    """Headline numbers across the window."""
    if not metrics:
        return PerformanceKpis(peak_concurrency=peak)
    slow = [m for m in metrics if is_slow(m)]
    slow_split = [split(m) for m in slow]
    total_slow_ms = sum(a.total for a in slow_split)
    return PerformanceKpis(
        total_requests=len(metrics),
        slow_requests=len(slow),
        error_requests=sum(1 for m in metrics if m.status_code >= 500),
        slow_rate=round(len(slow) / len(metrics), 4),
        p95_ms=round(percentile([m.duration_ms for m in metrics], 95), 1),
        warehouse_share=_share(sum(a.warehouse for a in slow_split), total_slow_ms),
        lakebase_share=_share(sum(a.lakebase for a in slow_split), total_slow_ms),
        app_share=_share(sum(a.app for a in slow_split), total_slow_ms),
        peak_concurrency=peak,
    )


def build_activities(metrics: list[RequestMetric]) -> list[ActivityStats]:
    """Per-route latency statistics, slowest p95 first."""
    groups: dict[tuple[str, str], list[RequestMetric]] = defaultdict(list)
    for metric in metrics:
        groups[(metric.method, metric.route)].append(metric)
    rows: list[ActivityStats] = []
    for (method, route), items in groups.items():
        durations = [m.duration_ms for m in items]
        splits = [split(m) for m in items]
        count = len(items)
        rows.append(
            ActivityStats(
                activity=items[0].activity,
                method=method,
                route=route,
                expected_ms=expected_ms(items[0]),
                count=count,
                error_count=sum(1 for m in items if m.status_code >= 500),
                slow_count=sum(1 for m in items if is_slow(m)),
                avg_ms=round(sum(durations) / count, 1),
                p50_ms=round(percentile(durations, 50), 1),
                p95_ms=round(percentile(durations, 95), 1),
                max_ms=round(max(durations), 1),
                avg_warehouse_ms=round(sum(a.warehouse for a in splits) / count, 1),
                avg_lakebase_ms=round(sum(a.lakebase for a in splits) / count, 1),
                avg_app_ms=round(sum(a.app for a in splits) / count, 1),
            )
        )
    rows.sort(key=lambda r: (r.p95_ms, r.count), reverse=True)
    return rows


def build_hourly(metrics: list[RequestMetric], peaks: dict[datetime, int]) -> list[LatencyBucket]:
    """Hour-by-hour roll-up, oldest first."""
    buckets: dict[datetime, list[RequestMetric]] = defaultdict(list)
    for metric in metrics:
        hour = metric.recorded_at.replace(minute=0, second=0, microsecond=0)
        buckets[hour].append(metric)
    rows: list[LatencyBucket] = []
    for hour in sorted(buckets):
        items = buckets[hour]
        splits = [split(m) for m in items]
        count = len(items)
        rows.append(
            LatencyBucket(
                hour=hour,
                count=count,
                slow_count=sum(1 for m in items if is_slow(m)),
                p95_ms=round(percentile([m.duration_ms for m in items], 95), 1),
                avg_warehouse_ms=round(sum(a.warehouse for a in splits) / count, 1),
                avg_lakebase_ms=round(sum(a.lakebase for a in splits) / count, 1),
                avg_app_ms=round(sum(a.app for a in splits) / count, 1),
                peak_concurrency=peaks.get(hour, 0),
            )
        )
    return rows


def build_recommendations(metrics: list[RequestMetric], peak: int) -> list[ScalingRecommendation]:
    """Sizing suggestions from where slow requests spent their time.

    Args:
        metrics: Requests in the window.
        peak: Peak concurrency in the window.

    Returns:
        Recommendations, most severe first, capped at ``RECOMMENDATION_LIMIT``.
    """
    if len(metrics) < MIN_SAMPLE:
        return [
            ScalingRecommendation(
                target=RecommendationTarget.NONE,
                severity=RecommendationSeverity.INFO,
                title="Not enough traffic for sizing advice yet",
                detail=f"{len(metrics)} requests in the window; need at least {MIN_SAMPLE}.",
            )
        ]

    recs: list[ScalingRecommendation] = []
    slow = [m for m in metrics if is_slow(m)]
    if not slow:
        recs.append(
            ScalingRecommendation(
                target=RecommendationTarget.NONE,
                severity=RecommendationSeverity.INFO,
                title="Nothing slower than expected in this window",
                detail=f"All {len(metrics)} requests finished within their activity's expected time.",
            )
        )

    by_activity: dict[str, list[RequestMetric]] = defaultdict(list)
    for metric in slow:
        by_activity[metric.activity].append(metric)
    for name, items in by_activity.items():
        splits = [split(m) for m in items]
        whole = sum(a.total for a in splits)
        shares = {
            RecommendationTarget.WAREHOUSE: sum(a.warehouse for a in splits) / whole,
            RecommendationTarget.LAKEBASE: sum(a.lakebase for a in splits) / whole,
            RecommendationTarget.APP: sum(a.app for a in splits) / whole,
        }
        target, share = max(shares.items(), key=lambda kv: kv[1])
        pct = round(share * 100)
        avg_slow = round(whole / len(items))
        severity = (
            RecommendationSeverity.WARNING
            if len(items) >= MIN_ACTIVITY_SAMPLE
            else RecommendationSeverity.INFO
        )
        if share < DOMINANT_SHARE:
            recs.append(
                ScalingRecommendation(
                    target=RecommendationTarget.APP,
                    severity=RecommendationSeverity.INFO,
                    title=f"{name}: time is spread across components",
                    detail=(
                        f"{len(items)} slow runs averaging {avg_slow} ms. No single component "
                        f"dominates (warehouse {round(shares[RecommendationTarget.WAREHOUSE] * 100)}%, "
                        f"Lakebase {round(shares[RecommendationTarget.LAKEBASE] * 100)}%, "
                        f"app {round(shares[RecommendationTarget.APP] * 100)}%)."
                    ),
                )
            )
            continue
        advice = {
            RecommendationTarget.WAREHOUSE: "Raise the SQL warehouse size or max clusters.",
            RecommendationTarget.LAKEBASE: "Raise the Lakebase autoscaling max CU.",
            RecommendationTarget.APP: "Raise app compute size, or reduce REST calls per request.",
        }[target]
        recs.append(
            ScalingRecommendation(
                target=target,
                severity=severity,
                title=f"{name}: {target.value} time dominates",
                detail=(
                    f"{pct}% of slow time in {len(items)} runs (average {avg_slow} ms) went to the "
                    f"{target.value}. {advice}"
                ),
            )
        )

    if peak >= PEAK_CONCURRENCY_WARNING:
        recs.append(
            ScalingRecommendation(
                target=RecommendationTarget.APP,
                severity=RecommendationSeverity.WARNING,
                title="High concurrency on the app",
                detail=f"Up to {peak} requests were in flight at once. Consider a larger app compute size.",
            )
        )
    slow_rate = len(slow) / len(metrics)
    if slow_rate > SLOW_RATE_WARNING:
        recs.append(
            ScalingRecommendation(
                target=RecommendationTarget.NONE,
                severity=RecommendationSeverity.WARNING,
                title=f"{round(slow_rate * 100)}% of requests are slower than expected",
                detail="Review the activity table below to see which activities drive the slow rate.",
            )
        )

    recs.sort(key=lambda r: _SEVERITY_ORDER[r.severity])
    return recs[:RECOMMENDATION_LIMIT]


def build_summary(metrics: list[RequestMetric], *, now: datetime, hours: int) -> PerformanceSummary:
    """Assemble the full health-monitor payload for the window.

    Args:
        metrics: Requests in the window (any order).
        now: Reference time for ``generated_at``.
        hours: Window length in hours.
    """
    ordered = sorted(metrics, key=lambda m: m.recorded_at)
    peak, peaks = peak_concurrency(ordered)
    return PerformanceSummary(
        hours=hours,
        generated_at=now,
        kpis=build_kpis(ordered, peak),
        activities=build_activities(ordered),
        hourly=build_hourly(ordered, peaks),
        recommendations=build_recommendations(ordered, peak),
    )


def slow_requests(metrics: list[RequestMetric]) -> list[SlowRequest]:
    """Slow requests, newest first."""
    rows = []
    for metric in sorted(metrics, key=lambda m: m.recorded_at, reverse=True):
        if not is_slow(metric):
            continue
        parts = split(metric)
        rows.append(
            SlowRequest(
                recorded_at=metric.recorded_at,
                activity=metric.activity,
                method=metric.method,
                route=metric.route,
                status_code=metric.status_code,
                duration_ms=metric.duration_ms,
                expected_ms=expected_ms(metric),
                warehouse_ms=round(parts.warehouse, 1),
                lakebase_ms=round(parts.lakebase, 1),
                app_ms=round(parts.app, 1),
                user_name=metric.user_name,
            )
        )
    return rows

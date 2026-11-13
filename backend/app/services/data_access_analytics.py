"""Aggregate stored data-access sync runs into dashboard analytics.

Pure functions over :class:`DataAccessRun` records, so every storage backend (Lakebase,
Delta, memory) produces the same numbers. Counts come from each run's full ``summary``;
the plan lists on a run are truncated samples and are not used for totals.

The Lakebase SQL views in ``lakebase_views.py`` expose the same run data for direct SQL use.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from backend.app.models.data_access import (
    DataAccessAnalytics,
    DataAccessAnalyticsDay,
    DataAccessAnalyticsFailure,
    DataAccessAnalyticsFingerprint,
    DataAccessAnalyticsKpis,
    DataAccessRun,
    SyncRunStatus,
)

DEFAULT_ANALYTICS_DAYS = 30
MAX_ANALYTICS_DAYS = 30
RECENT_FAILURE_LIMIT = 10
TOP_FINGERPRINT_LIMIT = 10


def clamp_days(days: int | None) -> int:
    """Clamp the requested window to the retention-supported range.

    Args:
        days: Requested window in days, or None for the default.

    Returns:
        A value between 1 and ``MAX_ANALYTICS_DAYS``.
    """
    if days is None:
        return DEFAULT_ANALYTICS_DAYS
    return min(max(1, days), MAX_ANALYTICS_DAYS)


def build_analytics(
    runs: list[DataAccessRun], *, now: datetime | None = None, days: int | None = None
) -> DataAccessAnalytics:
    """Summarize sync runs in the window into KPIs, a daily series, failures, and fingerprints.

    Args:
        runs: Stored runs (any order, any retention).
        now: Reference time; defaults to the current UTC time.
        days: Window length in days (clamped).

    Returns:
        Analytics payload ready for the API.
    """
    now = now or datetime.now(UTC)
    window = clamp_days(days)
    cutoff = now - timedelta(days=window)
    in_window = sorted(
        (r for r in runs if r.started_at >= cutoff),
        key=lambda r: r.started_at,
    )
    finished = [r for r in in_window if r.status in (SyncRunStatus.SUCCESS, SyncRunStatus.FAILED)]
    successes = [r for r in finished if r.status == SyncRunStatus.SUCCESS]
    failures = [r for r in finished if r.status == SyncRunStatus.FAILED]

    durations = [r.duration_seconds for r in finished if r.duration_seconds is not None]
    kpis = DataAccessAnalyticsKpis(
        total_runs=len(in_window),
        success_runs=len(successes),
        failed_runs=len(failures),
        running_runs=sum(1 for r in in_window if r.status == SyncRunStatus.RUNNING),
        success_rate=round(len(successes) / len(finished), 4) if finished else None,
        avg_duration_seconds=round(sum(durations) / len(durations), 1) if durations else None,
        last_success_at=successes[-1].started_at if successes else None,
        last_failure_at=failures[-1].started_at if failures else None,
        distinct_fingerprints=len({r.fingerprint for r in successes if r.fingerprint}),
        fingerprint_changes=_fingerprint_changes(successes),
    )

    return DataAccessAnalytics(
        days=window,
        generated_at=now,
        kpis=kpis,
        daily=_daily(in_window),
        failures=[
            DataAccessAnalyticsFailure(
                run_id=r.run_id,
                mode=r.mode,
                started_at=r.started_at,
                triggered_by=r.triggered_by,
                error=r.error,
            )
            for r in reversed(failures[-RECENT_FAILURE_LIMIT:])
        ],
        fingerprints=_fingerprints(successes),
    )


def _daily(runs: list[DataAccessRun]) -> list[DataAccessAnalyticsDay]:
    """Group runs by UTC day and mode, summing the full plan summaries."""
    buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "runs": 0,
            "success": 0,
            "failed": 0,
            "grants_added": 0,
            "grants_revoked": 0,
            "members_added": 0,
            "members_removed": 0,
        }
    )
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run in runs:
        day = run.started_at.astimezone(UTC).date().isoformat()
        key = (day, run.mode)
        bucket = buckets[key]
        bucket["runs"] += 1
        if run.status == SyncRunStatus.SUCCESS:
            bucket["success"] += 1
        elif run.status == SyncRunStatus.FAILED:
            bucket["failed"] += 1
        if run.status in (SyncRunStatus.SUCCESS, SyncRunStatus.FAILED) and run.duration_seconds is not None:
            durations[key].append(run.duration_seconds)
        summary = run.summary
        if summary is not None and run.status == SyncRunStatus.SUCCESS:
            bucket["grants_added"] += summary.grants_to_add
            bucket["grants_revoked"] += summary.grants_to_revoke
            bucket["members_added"] += summary.members_to_add
            bucket["members_removed"] += summary.members_to_remove
    return [
        DataAccessAnalyticsDay(
            day=day,
            mode=mode,
            avg_duration_seconds=(
                round(sum(durations[(day, mode)]) / len(durations[(day, mode)]), 1)
                if durations[(day, mode)]
                else None
            ),
            **values,
        )
        for (day, mode), values in sorted(buckets.items())
    ]


def _fingerprint_changes(successes: list[DataAccessRun]) -> int:
    """Count changes in desired-state fingerprint between consecutive successes per mode."""
    changes = 0
    last_by_mode: dict[str, str | None] = {}
    for run in successes:
        previous = last_by_mode.get(run.mode)
        if previous is not None and run.fingerprint != previous:
            changes += 1
        last_by_mode[run.mode] = run.fingerprint
    return changes


def _fingerprints(successes: list[DataAccessRun]) -> list[DataAccessAnalyticsFingerprint]:
    """Most frequent desired-state fingerprints with first/last seen times."""
    counts: Counter[str] = Counter()
    first: dict[str, datetime] = {}
    last: dict[str, datetime] = {}
    for run in successes:
        if not run.fingerprint:
            continue
        counts[run.fingerprint] += 1
        first.setdefault(run.fingerprint, run.started_at)
        last[run.fingerprint] = run.started_at
    return [
        DataAccessAnalyticsFingerprint(
            fingerprint=fp,
            runs=count,
            first_seen=first[fp],
            last_seen=last[fp],
        )
        for fp, count in counts.most_common(TOP_FINGERPRINT_LIMIT)
    ]

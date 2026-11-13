from __future__ import annotations

import asyncio
import contextvars
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core import perf
from backend.app.core.activities import DEFAULT_EXPECTED_MS, activity_for, catalog_size
from backend.app.core.config import AppConfig
from backend.app.core.container import ServiceContainer
from backend.app.core.perf import MetricsRecorder, RequestTimingMiddleware, RequestTimings
from backend.app.main import create_app
from backend.app.models.performance import RecommendationSeverity, RecommendationTarget, RequestMetric
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services import performance_analytics as analytics
from backend.app.services.performance import PerformanceService
from backend.tests.conftest import headers_for

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
ME = "/api/me"  # catalog: 1000 ms expected
APPROVE = "/api/identity/access-requests/{request_id}/approve"  # catalog: 5000 ms expected


def _metric(
    *,
    recorded_at: datetime = NOW,
    method: str = "GET",
    route: str = ME,
    duration_ms: float = 100.0,
    warehouse_ms: float = 0.0,
    lakebase_ms: float = 0.0,
    status_code: int = 200,
    user_name: str | None = None,
) -> RequestMetric:
    return RequestMetric(
        recorded_at=recorded_at,
        method=method,
        route=route,
        activity=activity_for(method, route).name,
        status_code=status_code,
        duration_ms=duration_ms,
        warehouse_ms=warehouse_ms,
        warehouse_calls=1 if warehouse_ms else 0,
        lakebase_ms=lakebase_ms,
        lakebase_calls=1 if lakebase_ms else 0,
        user_name=user_name,
    )


# --------------------------------------------------------------------------- activities


def test_activity_for_known_route_uses_catalog() -> None:
    activity = activity_for("post", APPROVE)
    assert activity.name == "Approve permission request"
    assert activity.expected_ms == 5000


def test_activity_for_unknown_route_gets_generic_name() -> None:
    activity = activity_for("GET", "/api/not-in-catalog")
    assert activity.name == "GET /api/not-in-catalog"
    assert activity.expected_ms == DEFAULT_EXPECTED_MS
    assert catalog_size() > 50


def test_every_api_route_has_a_named_activity(config: AppConfig, container: ServiceContainer) -> None:
    app = create_app(config, container)
    unnamed: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path == "/api/health":
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            if activity_for(method, path).name == f"{method} {path}":
                unnamed.append(f"{method} {path}")
    assert unnamed == []


# --------------------------------------------------------------------------- request timing


def test_track_outside_request_is_a_no_op() -> None:
    def run() -> None:
        with perf.track("lakebase"):
            pass

    contextvars.copy_context().run(run)


def test_track_attributes_time_to_dependency_inside_request() -> None:
    def run() -> RequestTimings:
        timings = perf.start_request()
        with perf.track("warehouse"):
            time.sleep(0.005)
        with perf.track("lakebase"):
            pass
        return timings

    timings = contextvars.copy_context().run(run)
    assert timings.warehouse_calls == 1
    assert timings.warehouse_ms >= 4
    assert timings.lakebase_calls == 1


def test_request_timings_add_is_thread_safe_and_counts_calls() -> None:
    timings = RequestTimings()
    timings.add("warehouse", 2.5)
    timings.add("warehouse", 2.5)
    timings.add("lakebase", 1.0)
    assert (timings.warehouse_ms, timings.warehouse_calls) == (5.0, 2)
    assert (timings.lakebase_ms, timings.lakebase_calls) == (1.0, 1)


def test_request_metric_app_time_is_remainder() -> None:
    metric = _metric(duration_ms=100, warehouse_ms=30, lakebase_ms=20)
    assert metric.app_ms == 50
    assert _metric(duration_ms=10, warehouse_ms=30).app_ms == 0.0


# --------------------------------------------------------------------------- recorder


class _Sink:
    def __init__(self, fail: bool = False) -> None:
        self.batches: list[list[RequestMetric]] = []
        self.fail = fail

    def __call__(self, batch: list[RequestMetric]) -> None:
        if self.fail:
            raise RuntimeError("lakebase down")
        self.batches.append(batch)

    @property
    def total(self) -> int:
        return sum(len(batch) for batch in self.batches)


def test_recorder_flush_writes_queued_metrics_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perf, "FLUSH_BATCH_SIZE", 2)
    sink = _Sink()
    recorder = MetricsRecorder(sink=sink)
    for _ in range(5):
        recorder.record(_metric())
    assert recorder.flush() == 5
    assert [len(batch) for batch in sink.batches] == [2, 2, 1]
    assert recorder.flush() == 0


def test_recorder_drops_and_counts_when_queue_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perf, "QUEUE_MAX", 2)
    recorder = MetricsRecorder(sink=_Sink())
    for _ in range(3):
        recorder.record(_metric())
    assert recorder.dropped == 1


def test_recorder_flush_failure_is_logged_not_raised() -> None:
    recorder = MetricsRecorder(sink=_Sink(fail=True))
    recorder.record(_metric())
    assert recorder.flush() == 0


def test_recorder_background_thread_flushes_and_stops() -> None:
    sink = _Sink()
    recorder = MetricsRecorder(sink=sink, flush_interval=0.01)
    recorder.start()
    recorder.start()  # idempotent
    recorder.record(_metric())
    deadline = time.monotonic() + 5
    while sink.total == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    recorder.record(_metric())
    recorder.stop()
    assert sink.total == 2


def test_recorder_prunes_at_most_once_per_interval() -> None:
    cutoffs: list[datetime] = []
    recorder = MetricsRecorder(sink=_Sink(), pruner=cutoffs.append)
    recorder._maybe_prune()
    recorder._maybe_prune()
    assert len(cutoffs) == 1
    assert cutoffs[0] < datetime.now(UTC)


def test_recorder_prune_failure_is_logged_not_raised() -> None:
    def boom(_before: datetime) -> None:
        raise RuntimeError("nope")

    MetricsRecorder(sink=_Sink(), pruner=boom)._maybe_prune()


def test_recorder_without_pruner_never_prunes() -> None:
    MetricsRecorder(sink=_Sink(), pruner=None)._maybe_prune()


class _ASGIHarness:
    """Drives the timing middleware with a fake downstream app and captures what it sends."""

    def __init__(
        self,
        inner: Any,
        *,
        recorder: Any,
        template: str | None = ME,
    ) -> None:
        self.recorded: list[RequestMetric] = []
        self.sent: list[dict[str, Any]] = []
        self._recorder = recorder
        self._middleware = RequestTimingMiddleware(
            inner,
            recorder=lambda: self._recorder,
            route_template=lambda _scope: template,
        )

    def call(self, scope: dict[str, Any]) -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            self.sent.append(message)

        asyncio.run(self._middleware(scope, receive, send))


class _Collector:
    def __init__(self) -> None:
        self.metrics: list[RequestMetric] = []

    def record(self, metric: RequestMetric) -> None:
        self.metrics.append(metric)


def _api_scope(path: str = ME, method: str = "GET", **extra: Any) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"x-forwarded-email", b"analyst@example.com")],
        **extra,
    }


def _ok_app(status: int = 201, body_work: Any = None) -> Any:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if body_work is not None:
            await body_work()
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


def test_middleware_records_api_request_with_status_and_user() -> None:
    collector = _Collector()
    harness = _ASGIHarness(_ok_app(status=201), recorder=collector)
    harness.call(_api_scope())
    assert len(collector.metrics) == 1
    metric = collector.metrics[0]
    assert metric.status_code == 201
    assert metric.activity == "Load signed-in user"
    assert metric.route == ME
    assert metric.user_name == "analyst@example.com"
    assert metric.duration_ms >= 0
    assert len(harness.sent) == 2  # downstream messages still reach the client


def test_middleware_attributes_warehouse_time_from_downstream_work() -> None:
    collector = _Collector()

    async def query() -> None:
        with perf.track("warehouse"):
            await asyncio.sleep(0.005)

    harness = _ASGIHarness(_ok_app(body_work=query), recorder=collector)
    harness.call(_api_scope())
    metric = collector.metrics[0]
    assert metric.warehouse_calls == 1
    assert metric.warehouse_ms >= 4
    assert metric.app_ms <= metric.duration_ms


def test_middleware_skips_health_non_api_and_websocket_traffic() -> None:
    collector = _Collector()
    harness = _ASGIHarness(_ok_app(), recorder=collector)
    harness.call(_api_scope(path="/api/health"))
    harness.call(_api_scope(path="/assets/app.js"))
    harness.call({"type": "websocket", "path": "/api/ws", "headers": []})
    assert collector.metrics == []
    assert len(harness.sent) == 6  # three requests, two messages each, all forwarded


def test_middleware_records_500_and_reraises_when_downstream_raises() -> None:
    collector = _Collector()

    async def explode(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    harness = _ASGIHarness(explode, recorder=collector)
    with pytest.raises(RuntimeError, match="boom"):
        harness.call(_api_scope())
    assert collector.metrics[0].status_code == 500


def test_middleware_without_recorder_is_silent() -> None:
    harness = _ASGIHarness(_ok_app(), recorder=None)
    harness.call(_api_scope())
    assert len(harness.sent) == 2


def test_middleware_falls_back_to_raw_path_when_unmatched() -> None:
    collector = _Collector()
    harness = _ASGIHarness(_ok_app(), recorder=collector, template=None)
    harness.call(_api_scope(path="/api/unknown/123"))
    assert collector.metrics[0].route == "/api/unknown/123"
    assert collector.metrics[0].activity == "GET /api/unknown/123"


def test_middleware_never_fails_the_request_when_recording_fails() -> None:
    class Broken:
        def record(self, metric: RequestMetric) -> None:
            raise RuntimeError("queue exploded")

    harness = _ASGIHarness(_ok_app(), recorder=Broken())
    harness.call(_api_scope())
    assert len(harness.sent) == 2


def test_middleware_ignores_missing_or_empty_forwarded_email() -> None:
    collector = _Collector()
    harness = _ASGIHarness(_ok_app(), recorder=collector)
    harness.call({"type": "http", "method": "GET", "path": ME, "headers": []})
    empty_header = [(b"x-forwarded-email", b"")]
    harness.call({"type": "http", "method": "GET", "path": ME, "headers": empty_header})
    assert [m.user_name for m in collector.metrics] == [None, None]


# --------------------------------------------------------------------------- analytics


def test_percentile_nearest_rank() -> None:
    assert analytics.percentile([], 95) == 0.0
    assert analytics.percentile([10, 20, 30, 40], 50) == 20
    assert analytics.percentile(list(range(1, 101)), 95) == 95
    assert analytics.percentile([7], 99) == 7


def test_peak_concurrency_counts_overlaps_and_back_to_back_requests() -> None:
    start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    overlapping = [
        _metric(recorded_at=start + timedelta(seconds=10), duration_ms=10_000),
        _metric(recorded_at=start + timedelta(seconds=15), duration_ms=10_000),
    ]
    peak, by_hour = analytics.peak_concurrency(overlapping)
    assert peak == 2
    assert by_hour == {start.replace(second=0): 2}

    back_to_back = [
        _metric(recorded_at=start + timedelta(seconds=10), duration_ms=10_000),
        _metric(recorded_at=start + timedelta(seconds=20), duration_ms=10_000),
    ]
    assert analytics.peak_concurrency(back_to_back)[0] == 1
    assert analytics.peak_concurrency([]) == (0, {})


def test_is_slow_and_expected_ms_follow_the_catalog() -> None:
    assert analytics.expected_ms(_metric(route=ME)) == 1000
    assert analytics.is_slow(_metric(route=ME, duration_ms=1500))
    assert not analytics.is_slow(_metric(route=ME, duration_ms=500))
    assert analytics.expected_ms(_metric(route="/api/nope")) == DEFAULT_EXPECTED_MS


def test_split_caps_warehouse_and_lakebase_within_total() -> None:
    parts = analytics.split(_metric(duration_ms=100, warehouse_ms=80, lakebase_ms=80))
    assert parts.warehouse == 80
    assert parts.lakebase == 20
    assert parts.app == 0.0


def test_kpis_without_traffic_only_report_peak() -> None:
    kpis = analytics.build_kpis([], peak=3)
    assert kpis.total_requests == 0
    assert kpis.slow_rate is None
    assert kpis.peak_concurrency == 3


def test_summary_attributes_slow_time_and_flags_warehouse() -> None:
    fast = [_metric(duration_ms=100) for _ in range(20)]
    slow = [_metric(method="POST", route=APPROVE, duration_ms=9_000, warehouse_ms=8_000) for _ in range(5)]
    summary = analytics.build_summary(fast + slow, now=NOW, hours=24)

    assert summary.hours == 24
    assert summary.kpis.total_requests == 25
    assert summary.kpis.slow_requests == 5
    assert summary.kpis.slow_rate == 0.2
    assert summary.kpis.warehouse_share == round(40_000 / 45_000, 4)
    assert summary.kpis.lakebase_share == 0.0
    assert summary.kpis.app_share == round(5_000 / 45_000, 4)

    assert summary.activities[0].route == APPROVE  # slowest p95 first
    assert summary.activities[0].slow_count == 5
    assert summary.activities[0].expected_ms == 5000
    assert summary.activities[0].avg_warehouse_ms == 8000

    assert len(summary.hourly) == 1
    assert summary.hourly[0].count == 25
    assert summary.hourly[0].slow_count == 5

    titles = {rec.title: rec for rec in summary.recommendations}
    warehouse = titles["Approve permission request: warehouse time dominates"]
    assert warehouse.target == RecommendationTarget.WAREHOUSE
    assert warehouse.severity == RecommendationSeverity.WARNING
    assert "Raise the SQL warehouse" in warehouse.detail
    assert "20% of requests are slower than expected" in titles


def test_recommendation_when_not_enough_traffic() -> None:
    recs = analytics.build_recommendations([_metric() for _ in range(3)], peak=1)
    assert len(recs) == 1
    assert recs[0].target == RecommendationTarget.NONE
    assert recs[0].severity == RecommendationSeverity.INFO


def test_recommendation_when_everything_is_fast() -> None:
    recs = analytics.build_recommendations([_metric() for _ in range(25)], peak=1)
    assert [r.title for r in recs] == ["Nothing slower than expected in this window"]


def test_recommendation_points_at_lakebase_when_it_dominates() -> None:
    fast = [_metric() for _ in range(20)]
    slow = [_metric(duration_ms=3_000, lakebase_ms=2_500) for _ in range(3)]
    recs = analytics.build_recommendations(fast + slow, peak=1)
    lakebase = next(r for r in recs if r.target == RecommendationTarget.LAKEBASE)
    assert lakebase.title == "Load signed-in user: lakebase time dominates"
    assert lakebase.severity == RecommendationSeverity.WARNING
    assert "autoscaling max CU" in lakebase.detail


def test_recommendation_points_at_app_when_app_time_dominates() -> None:
    fast = [_metric() for _ in range(20)]
    slow = [_metric(duration_ms=3_000) for _ in range(3)]
    recs = analytics.build_recommendations(fast + slow, peak=1)
    app_rec = next(r for r in recs if r.title.endswith("app time dominates"))
    assert app_rec.target == RecommendationTarget.APP
    assert "compute size" in app_rec.detail


def test_recommendation_reports_spread_when_no_component_dominates() -> None:
    fast = [_metric() for _ in range(20)]
    slow = [_metric(duration_ms=3_000, warehouse_ms=1_000, lakebase_ms=1_000) for _ in range(3)]
    recs = analytics.build_recommendations(fast + slow, peak=1)
    spread = next(r for r in recs if "spread across components" in r.title)
    assert spread.severity == RecommendationSeverity.INFO
    assert "No single component dominates" in spread.detail


def test_single_slow_run_is_informational_not_a_warning() -> None:
    fast = [_metric() for _ in range(20)]
    slow = [_metric(duration_ms=3_000, warehouse_ms=2_900)]
    recs = analytics.build_recommendations(fast + slow, peak=1)
    warehouse = next(r for r in recs if r.target == RecommendationTarget.WAREHOUSE)
    assert warehouse.severity == RecommendationSeverity.INFO


def test_high_concurrency_is_a_warning_and_sorts_first() -> None:
    fast = [_metric() for _ in range(20)]
    recs = analytics.build_recommendations(fast, peak=analytics.PEAK_CONCURRENCY_WARNING)
    assert recs[0].title == "High concurrency on the app"
    assert recs[0].severity == RecommendationSeverity.WARNING
    assert recs[-1].severity == RecommendationSeverity.INFO


def test_slow_requests_newest_first_and_only_slow_ones() -> None:
    older = _metric(recorded_at=NOW - timedelta(minutes=5), duration_ms=2_000, user_name="a@x.com")
    newer = _metric(recorded_at=NOW - timedelta(minutes=1), duration_ms=3_000, warehouse_ms=2_000)
    fast = _metric(recorded_at=NOW, duration_ms=10)
    rows = analytics.slow_requests([older, fast, newer])
    assert [r.duration_ms for r in rows] == [3_000, 2_000]
    assert rows[0].warehouse_ms == 2_000
    assert rows[0].app_ms == 1_000
    assert rows[0].expected_ms == 1000
    assert rows[1].user_name == "a@x.com"


# --------------------------------------------------------------------------- service


class _FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.apps = SimpleNamespace(get=self._app_get)
        self.postgres = SimpleNamespace(get_endpoint=self._endpoint_get)
        self.warehouses = SimpleNamespace(get=self._warehouse_get)

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("workspace unreachable")

    def _app_get(self, name: str) -> SimpleNamespace:
        self._maybe_fail()
        return SimpleNamespace(compute_size="MEDIUM")

    def _endpoint_get(self, name: str) -> SimpleNamespace:
        self._maybe_fail()
        status = SimpleNamespace(
            autoscaling_limit_min_cu=1.0,
            autoscaling_limit_max_cu=4.0,
            current_state=SimpleNamespace(value="ACTIVE"),
        )
        return SimpleNamespace(status=status)

    def _warehouse_get(self, id: str) -> SimpleNamespace:  # noqa: A002
        self._maybe_fail()
        return SimpleNamespace(
            name="Governance WH",
            cluster_size="Medium",
            min_num_clusters=1,
            max_num_clusters=2,
            state=SimpleNamespace(value="RUNNING"),
            auto_stop_mins=10,
        )


class _FakeWorkspace:
    def __init__(self, client: _FakeClient, *, admin_configured: bool = True) -> None:
        self.admin_configured = admin_configured
        self._client = client

    def admin(self) -> _FakeClient:
        return self._client

    def app(self) -> _FakeClient:
        return self._client


def _service(
    config: AppConfig,
    *,
    client: _FakeClient | None = None,
    admin_configured: bool = True,
    lakebase_endpoint: str | None = "projects/p/branches/b/endpoints/primary",
    warehouse_id: str | None = "wh-1",
) -> tuple[PerformanceService, InMemoryStorageProvider]:
    storage = InMemoryStorageProvider()
    config = config.model_copy(update={"lakebase_endpoint": lakebase_endpoint, "warehouse_id": warehouse_id})
    workspace = _FakeWorkspace(client or _FakeClient(), admin_configured=admin_configured)
    return PerformanceService(storage, workspace, config), storage  # type: ignore[arg-type]


def test_clamp_hours_defaults_and_bounds() -> None:
    assert PerformanceService.clamp_hours(None) == 24
    assert PerformanceService.clamp_hours(0) == 1
    assert PerformanceService.clamp_hours(10_000) == 24 * 14


def test_summary_reads_only_the_requested_window(config: AppConfig) -> None:
    service, storage = _service(config)
    now = datetime.now(UTC)
    storage.performance.record_many(
        [
            _metric(recorded_at=now - timedelta(minutes=10)),
            _metric(recorded_at=now - timedelta(hours=3)),
        ]
    )
    assert service.summary(1).kpis.total_requests == 1
    assert service.summary(6).kpis.total_requests == 2
    assert service.summary(None).hours == 24


def test_slow_requests_are_paged_newest_first(config: AppConfig) -> None:
    service, storage = _service(config)
    now = datetime.now(UTC)
    storage.performance.record_many(
        [_metric(recorded_at=now - timedelta(minutes=i), duration_ms=2_000) for i in range(5)]
        + [_metric(recorded_at=now, duration_ms=10)]
    )
    first = service.slow_requests(page=1, page_size=2)
    assert first.total == 5
    assert len(first.items) == 2
    assert first.items[0].recorded_at > first.items[1].recorded_at
    last = service.slow_requests(page=3, page_size=2)
    assert len(last.items) == 1
    assert service.slow_requests(page=0, page_size=500).page == 1
    assert service.slow_requests(page=0, page_size=500).page_size == 100


def test_capacity_reports_app_lakebase_and_warehouse(config: AppConfig) -> None:
    service, _ = _service(config)
    report = service.capacity()
    by_target = {s.target: s for s in report.settings}
    assert by_target[RecommendationTarget.APP].size == "MEDIUM"
    assert by_target[RecommendationTarget.LAKEBASE].size == "1–4 CU"
    assert by_target[RecommendationTarget.LAKEBASE].autoscaling == "1 to 4 CU"
    assert by_target[RecommendationTarget.LAKEBASE].name == "primary"
    assert by_target[RecommendationTarget.WAREHOUSE].name == "Governance WH"
    assert by_target[RecommendationTarget.WAREHOUSE].autoscaling == "1–2 clusters"
    assert "RUNNING" in (by_target[RecommendationTarget.WAREHOUSE].detail or "")
    assert all(s.available for s in report.settings)


def test_capacity_uses_app_client_when_admin_not_configured(config: AppConfig) -> None:
    service, _ = _service(config, admin_configured=False)
    assert service.capacity().settings[0].size == "MEDIUM"


def test_capacity_failures_are_reported_per_resource(config: AppConfig) -> None:
    service, _ = _service(config, client=_FakeClient(fail=True))
    settings = service.capacity().settings
    assert [s.available for s in settings] == [False, False, False]
    assert all("workspace unreachable" in (s.detail or "") for s in settings)


def test_capacity_flags_unconfigured_lakebase_and_warehouse(config: AppConfig) -> None:
    service, _ = _service(config, lakebase_endpoint=None, warehouse_id=None)
    by_target = {s.target: s for s in service.capacity().settings}
    assert by_target[RecommendationTarget.LAKEBASE].available is False
    assert "LAKEBASE_ENDPOINT" in (by_target[RecommendationTarget.LAKEBASE].detail or "")
    assert by_target[RecommendationTarget.WAREHOUSE].available is False
    assert "DATABRICKS_WAREHOUSE_ID" in (by_target[RecommendationTarget.WAREHOUSE].detail or "")


def test_capacity_handles_missing_lakebase_limits(config: AppConfig) -> None:
    client = _FakeClient()
    client._endpoint_get = lambda name: SimpleNamespace(  # type: ignore[method-assign]
        status=SimpleNamespace(
            autoscaling_limit_min_cu=None, autoscaling_limit_max_cu=None, current_state=None
        )
    )
    client.postgres = SimpleNamespace(get_endpoint=client._endpoint_get)
    service, _ = _service(config, client=client)
    lakebase = next(s for s in service.capacity().settings if s.target == RecommendationTarget.LAKEBASE)
    assert lakebase.size is None
    assert lakebase.detail == "Autoscaling endpoint"


def test_capacity_handles_warehouse_without_cluster_range(config: AppConfig) -> None:
    client = _FakeClient()
    client.warehouses = SimpleNamespace(
        get=lambda id: SimpleNamespace(  # noqa: A002
            name=None,
            cluster_size=None,
            min_num_clusters=None,
            max_num_clusters=None,
            state=None,
            auto_stop_mins=0,
        )
    )
    service, _ = _service(config, client=client, warehouse_id="wh-9")
    warehouse = next(s for s in service.capacity().settings if s.target == RecommendationTarget.WAREHOUSE)
    assert warehouse.name == "wh-9"
    assert warehouse.autoscaling is None
    assert "unknown" in (warehouse.detail or "")


# --------------------------------------------------------------------------- routes


def test_performance_routes_return_payloads_for_admin(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    summary = client.get("/api/performance/summary?hours=2", headers=admin)
    assert summary.status_code == 200
    assert summary.json()["hours"] == 2

    slow = client.get("/api/performance/slow-requests?page=1&page_size=10", headers=admin)
    assert slow.status_code == 200
    assert slow.json()["pageSize"] == 10

    capacity = client.get("/api/performance/capacity", headers=admin)
    assert capacity.status_code == 200
    assert capacity.json()["settings"]


def test_performance_routes_forbidden_for_non_admin(client: TestClient) -> None:
    user = headers_for("analyst@example.com")
    assert client.get("/api/performance/summary", headers=user).status_code == 403
    assert client.get("/api/performance/slow-requests", headers=user).status_code == 403
    assert client.get("/api/performance/capacity", headers=user).status_code == 403


def test_performance_routes_validate_query_parameters(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    assert client.get("/api/performance/summary?hours=0", headers=admin).status_code == 422
    assert client.get("/api/performance/slow-requests?page_size=101", headers=admin).status_code == 422


def test_requests_through_the_app_are_recorded_for_the_monitor(
    client: TestClient, container: ServiceContainer
) -> None:
    admin = headers_for("admin@example.com")
    assert client.get("/api/me", headers=admin).status_code == 200
    container.metrics.flush()
    rows = container.storage.performance.list_since(datetime.now(UTC) - timedelta(minutes=5))
    me = [r for r in rows if r.route == "/api/me"]
    assert me
    assert me[-1].activity == "Load signed-in user"
    assert me[-1].user_name == "admin@example.com"
    assert me[-1].status_code == 200

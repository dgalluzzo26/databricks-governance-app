"""Tests for tracked tasks, request IDs, error capture, and the activity/performance history APIs."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.clients.sql import SqlExecutor
from backend.app.core import perf
from backend.app.core.errors import (
    InvalidRequestError,
    StatementTimeoutError,
    UpstreamError,
    WarehouseUnavailableError,
)
from backend.app.core.perf import RequestTimingMiddleware
from backend.app.core.request_id import HEADER, current_request_id, new_request_id, set_request_id
from backend.app.models.data_access import DataAccessPlanSummary, DataAccessRun, SyncRunStatus, SyncTrigger
from backend.app.models.performance import RequestMetric
from backend.app.models.task import StepStatus, TaskRecord, TaskStatus
from backend.app.repositories.base import STALE_TASK_MESSAGE, InMemoryTaskRepository, TaskRepository
from backend.app.services import job_trackers, tasks
from backend.app.services.job_trackers import follow_data_access_run, follow_job_run
from backend.app.services.tasks import TaskService, step, step_detail, task_summary
from backend.tests.conftest import headers_for

NOW = datetime.now(UTC)
ADMIN = "admin@example.com"
ANALYST = "analyst@example.com"


def _service() -> tuple[TaskService, InMemoryTaskRepository]:
    repo = InMemoryTaskRepository()
    return TaskService(lambda: repo), repo


def _wait_for(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def _record(task_id: str, *, actor: str = ADMIN, started: datetime = NOW, **extra: Any) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        kind="test.kind",
        title=f"Task {task_id}",
        actor=actor,
        started_at=started,
        updated_at=extra.pop("updated_at", started),
        **extra,
    )


# --------------------------------------------------------------------------- request ids


def test_new_request_id_keeps_safe_incoming_ids_and_replaces_others() -> None:
    assert new_request_id("abc-123_DEF.456") == "abc-123_DEF.456"
    generated = new_request_id("bad id with spaces")
    assert len(generated) == 16 and generated.isalnum()
    assert new_request_id(None) != new_request_id(None)
    assert len(new_request_id("short")) == 16


def test_set_and_read_current_request_id() -> None:
    set_request_id("req-abcdef12")
    try:
        assert current_request_id() == "req-abcdef12"
    finally:
        set_request_id(None)
    assert current_request_id() is None


def _run_middleware(inner: Any, scope: dict[str, Any]) -> tuple[list[RequestMetric], list[dict[str, Any]]]:
    recorded: list[RequestMetric] = []
    sent: list[dict[str, Any]] = []

    class Collector:
        def record(self, metric: RequestMetric) -> None:
            recorded.append(metric)

    collector = Collector()
    middleware = RequestTimingMiddleware(
        inner, recorder=lambda: collector, route_template=lambda s: s["path"]
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with contextlib.suppress(RuntimeError):
        asyncio.run(middleware(scope, receive, send))
    return recorded, sent


def _scope(path: str = "/api/me", headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {"type": "http", "method": "GET", "path": path, "headers": headers or []}


def test_middleware_adds_request_id_header_and_records_error() -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        perf.note_error("invalid_request", "first")
        perf.note_error("other", "second is ignored")
        await send({"type": "http.response.start", "status": 422, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    recorded, sent = _run_middleware(app, _scope(headers=[(b"x-request-id", b"client-req-0001")]))
    assert dict(sent[0]["headers"])[HEADER.encode()] == b"client-req-0001"
    metric = recorded[0]
    assert metric.request_id == "client-req-0001"
    assert (metric.error_code, metric.error_message) == ("invalid_request", "first")


def test_middleware_notes_unhandled_errors_as_internal() -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    recorded, _ = _run_middleware(app, _scope())
    assert recorded[0].status_code == 500
    assert recorded[0].error_code == "internal_error"
    assert recorded[0].request_id


def test_middleware_skips_activity_polling() -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})

    recorded, _ = _run_middleware(app, _scope("/api/activity"))
    assert recorded == []


def test_note_error_outside_a_request_is_a_no_op() -> None:
    perf.note_error("x", "y")


def test_error_envelope_and_header_carry_the_request_id(client: TestClient) -> None:
    resp = client.get("/api/activity/tasks/missing", headers=headers_for(ANALYST))
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "not_found"
    assert body["requestId"] == resp.headers[HEADER]


# --------------------------------------------------------------------------- task service


def test_track_records_steps_summary_and_success() -> None:
    service, repo = _service()
    with service.track("demo.run", "Demo", ADMIN, link="/demo") as handle:
        with step("First"):
            step_detail("half way")
        with step("Second"), step("Nested"):
            pass
        task_summary("all good")
    stored = repo.get(handle.task_id)
    assert stored is not None
    assert stored.status == TaskStatus.SUCCEEDED
    assert [(s.name, s.status, s.depth) for s in stored.steps] == [
        ("First", StepStatus.DONE, 0),
        ("Second", StepStatus.DONE, 0),
        ("Nested", StepStatus.DONE, 1),
    ]
    assert stored.steps[0].detail == "half way"
    assert stored.summary == "all good"
    assert stored.link == "/demo"
    assert stored.duration_seconds is not None


def test_track_failure_marks_task_and_step_failed_with_app_error_code() -> None:
    service, repo = _service()
    with (
        pytest.raises(InvalidRequestError),
        service.track("demo.fail", "Fails", ADMIN) as handle,
        step("Validate"),
    ):
        raise InvalidRequestError("bad input")
    stored = repo.get(handle.task_id)
    assert stored is not None
    assert stored.status == TaskStatus.FAILED
    assert (stored.error_code, stored.error_message) == ("invalid_request", "bad input")
    assert stored.steps[0].status == StepStatus.FAILED
    assert stored.steps[0].detail == "bad input"


def test_unexpected_errors_are_recorded_as_internal() -> None:
    service, repo = _service()
    with pytest.raises(KeyError), service.track("demo.crash", "Crash", ADMIN) as handle:
        raise KeyError("missing")
    stored = repo.get(handle.task_id)
    assert stored is not None and stored.error_code == "internal_error"
    assert "KeyError" in (stored.error_message or "")


def test_running_steps_take_the_task_outcome_when_it_completes() -> None:
    service, _ = _service()
    with service.track("demo.open", "Open step", ADMIN) as handle:
        handle.add_step("Never finished explicitly")
    assert handle.snapshot().steps[0].status == StepStatus.DONE


def test_nested_track_becomes_a_step_of_the_outer_task() -> None:
    service, repo = _service()
    with service.track("outer", "Outer", ADMIN) as outer, service.track("inner", "Inner", ADMIN) as inner:
        assert inner is outer
    assert len(repo.list_since(NOW - timedelta(minutes=1))) == 1
    stored = repo.get(outer.task_id)
    assert stored is not None and [s.name for s in stored.steps] == ["Inner"]


def test_step_helpers_outside_a_task_do_nothing() -> None:
    assert tasks.current_task() is None
    with step("Untracked"):
        step_detail("ignored")
        task_summary("ignored")

    @step("Decorated")
    def work() -> int:
        return 7

    assert work() == 7


def test_step_detail_saves_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    saves: list[TaskRecord] = []

    class Counting(InMemoryTaskRepository):
        def save(self, task: TaskRecord) -> None:
            saves.append(task)
            super().save(task)

    repo = Counting()
    service = TaskService(lambda: repo)
    with service.track("demo.detail", "Detail", ADMIN), step("Loop"):
        before = len(saves)
        for i in range(50):
            step_detail(f"{i}")
        assert len(saves) - before == 1


def test_set_detail_without_a_running_step_is_ignored() -> None:
    service, _ = _service()
    with service.track("demo.none", "No step", ADMIN) as handle:
        handle.set_detail("nothing running")
        assert handle.snapshot().steps == []


def test_storage_failures_never_break_the_tracked_work() -> None:
    class Broken(InMemoryTaskRepository):
        def save(self, task: TaskRecord) -> None:
            raise RuntimeError("db down")

    service = TaskService(lambda: Broken())
    with service.track("demo.broken", "Broken", ADMIN), step("Still runs"):
        value = 1
    assert value == 1


def test_start_background_success_and_failure() -> None:
    service, repo = _service()

    def ok(_: Any) -> None:
        with step("Work"):
            pass

    def bad(_: Any) -> None:
        raise UpstreamError("job failed")

    ok_id = service.start_background("bg.ok", "OK", ADMIN, ok)
    bad_id = service.start_background("bg.bad", "Bad", ADMIN, bad)
    _wait_for(lambda: all((t := repo.get(i)) and t.status != TaskStatus.RUNNING for i in (ok_id, bad_id)))
    assert repo.get(ok_id).status == TaskStatus.SUCCEEDED  # type: ignore[union-attr]
    failed = repo.get(bad_id)
    assert failed is not None and failed.error_code == "upstream_error"


def test_get_prefers_the_live_view_while_running() -> None:
    service, _ = _service()
    with service.track("demo.live", "Live", ADMIN) as handle:
        handle.set_summary("in flight")
        live = service.get(handle.task_id)
        assert live is not None and live.summary == "in flight"
    assert service.get("unknown") is None


def test_for_actor_is_case_insensitive_and_counts_running_and_failed() -> None:
    service, repo = _service()
    repo.save(_record("a", actor="Admin@Example.com", status=TaskStatus.FAILED))
    repo.save(_record("b", actor=ANALYST))
    repo.save(_record("c", actor=ADMIN, started=NOW - timedelta(days=60)))
    with service.track("demo.mine", "Mine", ADMIN):
        page = service.for_actor(ADMIN, limit=10)
    assert {t.task_id for t in page.items} >= {"a"}
    assert all(t.actor.lower() == ADMIN for t in page.items)
    assert page.running == 1 and page.failed == 1
    assert page.total == 2


def test_page_filters_by_status_and_paginates() -> None:
    service, repo = _service()
    for i in range(5):
        repo.save(_record(f"ok{i}", started=NOW - timedelta(minutes=i), status=TaskStatus.SUCCEEDED))
    repo.save(_record("bad", actor=ANALYST, status=TaskStatus.FAILED))
    page = service.page(hours=1, status="succeeded", page=2, page_size=2)
    assert page.total == 5 and len(page.items) == 2
    assert page.failed == 1 and page.running == 0
    assert [t.task_id for t in service.page(hours=1, status="failed").items] == ["bad"]


def test_tick_heartbeats_marks_stale_and_prunes() -> None:
    repo = InMemoryTaskRepository()
    service = TaskService(lambda: repo, stale_after_seconds=60)
    repo.save(_record("stale", status=TaskStatus.RUNNING, updated_at=NOW - timedelta(minutes=10)))
    repo.save(_record("old", started=NOW - timedelta(days=45), status=TaskStatus.SUCCEEDED))
    with service.track("demo.alive", "Alive", ADMIN) as handle:
        service.tick()
        alive = repo.get(handle.task_id)
        assert alive is not None and alive.status == TaskStatus.RUNNING
    stale = repo.get("stale")
    assert stale is not None and stale.status == TaskStatus.FAILED
    assert (stale.error_code, stale.error_message) == ("stale", STALE_TASK_MESSAGE)
    assert repo.get("old") is None


def test_tick_survives_repository_failures() -> None:
    class Broken(InMemoryTaskRepository):
        def mark_stale(self, before: datetime, at: datetime) -> int:
            raise RuntimeError("down")

        def prune(self, before: datetime) -> int:
            raise RuntimeError("down")

    TaskService(lambda: Broken()).tick()


def test_start_and_stop_heartbeat_thread() -> None:
    repo = InMemoryTaskRepository()
    service = TaskService(lambda: repo, heartbeat_seconds=0.01)
    service.start()
    service.start()
    time.sleep(0.05)
    service.stop()
    assert service._thread is None  # noqa: SLF001


def test_base_repository_is_abstract() -> None:
    with pytest.raises(TypeError):
        TaskRepository()  # type: ignore[abstract]


def test_storage_provider_exposes_a_task_store(container: Any) -> None:
    store = container.storage.tasks
    assert store is container.storage.tasks
    store.save(_record("p1"))
    assert store.get("p1") is not None


# --------------------------------------------------------------------------- job trackers


def _da_run(status: SyncRunStatus, **extra: Any) -> DataAccessRun:
    return DataAccessRun(
        run_id="run-1",
        status=status,
        trigger=SyncTrigger.USER,
        started_at=NOW,
        triggered_by=ADMIN,
        **extra,
    )


def test_follow_data_access_run_reports_summary_on_success() -> None:
    service, repo = _service()
    states = iter(
        [
            _da_run(SyncRunStatus.RUNNING),
            _da_run(
                SyncRunStatus.SUCCESS,
                summary=DataAccessPlanSummary(grants_to_add=3, grants_to_revoke=1, groups_to_create=2),
            ),
        ]
    )
    task_id = follow_data_access_run(
        service, lambda _rid: next(states), _da_run(SyncRunStatus.RUNNING), ADMIN, poll_seconds=0
    )
    _wait_for(lambda: (t := repo.get(task_id)) is not None and t.status != TaskStatus.RUNNING)
    stored = repo.get(task_id)
    assert stored is not None and stored.status == TaskStatus.SUCCEEDED
    assert stored.title == "Data-access sync (dry run)"
    assert stored.summary == "3 grants to add, 1 to revoke, 2 groups to create"
    assert [s.name for s in stored.steps] == [
        "Start the data-access sync Job",
        "Wait for the sync Job to finish",
    ]


def test_follow_data_access_run_failure_and_give_up() -> None:
    service, repo = _service()
    failed_id = follow_data_access_run(
        service,
        lambda _rid: _da_run(SyncRunStatus.FAILED, error="grant failed"),
        _da_run(SyncRunStatus.RUNNING, mode="apply"),
        ADMIN,
        poll_seconds=0,
    )
    stuck_id = follow_data_access_run(
        service,
        lambda _rid: _da_run(SyncRunStatus.RUNNING),
        _da_run(SyncRunStatus.RUNNING),
        ADMIN,
        poll_seconds=0,
        max_seconds=0,
    )
    _wait_for(
        lambda: all((t := repo.get(i)) and t.status != TaskStatus.RUNNING for i in (failed_id, stuck_id))
    )
    failed = repo.get(failed_id)
    assert failed is not None and failed.error_message == "grant failed"
    assert failed.title == "Data-access sync (apply)"
    stuck = repo.get(stuck_id)
    assert stuck is not None and stuck.error_code == "timeout"


class _FakeJobs:
    def __init__(self, runs: list[Any]) -> None:
        self._runs = iter(runs)

    def get_run(self, run_id: int) -> Any:
        return next(self._runs)


def _job_run(life_cycle: str, result: str | None = None, tasks_done: int = 0, message: str = "") -> Any:
    def state(lc: str) -> Any:
        return SimpleNamespace(life_cycle_state=SimpleNamespace(value=lc), result_state=None)

    job_tasks = [SimpleNamespace(state=state("TERMINATED")) for _ in range(tasks_done)]
    job_tasks.append(SimpleNamespace(state=state("RUNNING")))
    return SimpleNamespace(
        state=SimpleNamespace(
            life_cycle_state=SimpleNamespace(value=life_cycle),
            result_state=SimpleNamespace(value=result) if result else None,
            state_message=message,
        ),
        tasks=job_tasks,
    )


def test_follow_job_run_success_and_failure() -> None:
    service, repo = _service()
    ok_jobs = _FakeJobs(
        [_job_run("PENDING"), _job_run("RUNNING", tasks_done=1), _job_run("TERMINATED", "SUCCESS")]
    )
    bad_jobs = _FakeJobs([_job_run("INTERNAL_ERROR", "FAILED", message="cluster died")])
    ok_id = follow_job_run(
        service, lambda: ok_jobs, 42, kind="usage.rollup", title="Rollup", actor=ADMIN, poll_seconds=0
    )
    bad_id = follow_job_run(
        service, lambda: bad_jobs, 43, kind="usage.rollup", title="Rollup", actor=ADMIN, poll_seconds=0
    )
    _wait_for(lambda: all((t := repo.get(i)) and t.status != TaskStatus.RUNNING for i in (ok_id, bad_id)))
    ok = repo.get(ok_id)
    assert ok is not None and ok.status == TaskStatus.SUCCEEDED
    assert ok.summary is not None and ok.summary.startswith("Job run 42 succeeded")
    bad = repo.get(bad_id)
    assert bad is not None and bad.error_message == "Job run 43 finished failed: cluster died"


def test_follow_job_run_gives_up_after_max_seconds() -> None:
    service, repo = _service()
    jobs = _FakeJobs([_job_run("RUNNING")] * 5)
    task_id = follow_job_run(
        service, lambda: jobs, 7, kind="k", title="t", actor=ADMIN, poll_seconds=0, max_seconds=0
    )
    _wait_for(lambda: (t := repo.get(task_id)) is not None and t.status != TaskStatus.RUNNING)
    assert repo.get(task_id).error_code == "timeout"  # type: ignore[union-attr]


def test_enum_text_and_elapsed_helpers() -> None:
    assert job_trackers._enum_text(None) == ""  # noqa: SLF001
    assert job_trackers._enum_text("running") == "RUNNING"  # noqa: SLF001
    assert job_trackers._elapsed(time.monotonic() - 75) == "1m 15s"  # noqa: SLF001


# --------------------------------------------------------------------------- routes


def _metric(user: str, status: int, *, minutes_ago: int = 1, code: str | None = None) -> RequestMetric:
    return RequestMetric(
        recorded_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        method="GET",
        route="/api/me",
        activity="Load signed-in user",
        status_code=status,
        duration_ms=10.0,
        user_name=user,
        request_id=f"rid-{user[:3]}-{status}",
        error_code=code,
        error_message="failed" if code else None,
    )


def test_activity_feed_returns_only_the_callers_tasks_and_errors(client: TestClient, container: Any) -> None:
    container.storage.tasks.save(_record("mine", actor=ANALYST, status=TaskStatus.FAILED))
    container.storage.tasks.save(_record("theirs", actor=ADMIN))
    container.storage.performance.record_many(
        [
            _metric(ANALYST, 502, code="upstream_error"),
            _metric(ANALYST, 200),
            _metric(ADMIN, 500, code="internal_error"),
        ]
    )
    resp = client.get("/api/activity", headers=headers_for(ANALYST))
    assert resp.status_code == 200
    data = resp.json()
    assert [t["taskId"] for t in data["tasks"]["items"]] == ["mine"]
    assert data["tasks"]["failed"] == 1
    errors = data["errors"]["items"]
    assert [(e["statusCode"], e["errorCode"]) for e in errors] == [(502, "upstream_error")]
    assert data["errors"]["failed"] == 1


def test_activity_task_detail_hides_other_users_tasks(client: TestClient, container: Any) -> None:
    container.storage.tasks.save(_record("t-admin", actor=ADMIN))
    assert client.get("/api/activity/tasks/t-admin", headers=headers_for(ANALYST)).status_code == 404
    mine = client.get("/api/activity/tasks/t-admin", headers=headers_for(ADMIN))
    assert mine.status_code == 200 and mine.json()["taskId"] == "t-admin"


def test_tracked_route_records_a_failed_task(client: TestClient, container: Any) -> None:
    resp = client.post("/api/identity/sync-uc", headers=headers_for(ADMIN))
    assert resp.status_code == 422
    page = container.tasks.for_actor(ADMIN)
    task = next(t for t in page.items if t.kind == "identity.sync_uc")
    assert task.status == TaskStatus.FAILED
    assert task.error_code == "invalid_request"
    assert task.request_id == resp.headers[HEADER]
    assert task.link == "/identity"


def test_admin_task_and_request_history(client: TestClient, container: Any) -> None:
    container.storage.tasks.save(_record("t1", actor=ANALYST, status=TaskStatus.SUCCEEDED))
    container.storage.tasks.save(_record("t2", actor=ADMIN, status=TaskStatus.FAILED))
    container.storage.performance.record_many(
        [_metric(ANALYST, 200), _metric(ADMIN, 503, code="warehouse_unavailable")]
    )
    admin = headers_for(ADMIN)

    all_tasks = client.get("/api/performance/tasks?hours=24", headers=admin).json()
    assert {t["taskId"] for t in all_tasks["items"]} >= {"t1", "t2"}
    failed_tasks = client.get("/api/performance/tasks?status=failed", headers=admin).json()
    assert [t["taskId"] for t in failed_tasks["items"]] == ["t2"]

    failed = client.get("/api/performance/requests?status=failed", headers=admin).json()
    assert [r["errorCode"] for r in failed["items"]] == ["warehouse_unavailable"]
    assert failed["succeeded"] >= 1 and failed["failed"] >= 1
    ok = client.get("/api/performance/requests?status=succeeded&page_size=5", headers=admin).json()
    assert all(r["ok"] for r in ok["items"])


def test_admin_history_routes_are_admin_only_and_validated(client: TestClient) -> None:
    user = headers_for(ANALYST)
    assert client.get("/api/performance/tasks", headers=user).status_code == 403
    assert client.get("/api/performance/requests", headers=user).status_code == 403
    admin = headers_for(ADMIN)
    assert client.get("/api/performance/tasks?status=bogus", headers=admin).status_code == 422
    assert client.get("/api/performance/requests?status=bogus", headers=admin).status_code == 422


# --------------------------------------------------------------------------- warehouse diagnosis


def _executor(warehouse: Any) -> tuple[SqlExecutor, Any]:
    def get(id: str) -> Any:  # noqa: A002
        if isinstance(warehouse, Exception):
            raise warehouse
        return warehouse

    client = SimpleNamespace(warehouses=SimpleNamespace(get=get))
    return SqlExecutor(lambda: client, "wh-1", max_wait_seconds=90), client


def _warehouse(state: str, health: str | None) -> Any:
    return SimpleNamespace(
        name="governance-wh",
        state=SimpleNamespace(value=state),
        health=SimpleNamespace(status=SimpleNamespace(value=health)) if health else None,
    )


def test_timeout_on_a_starting_warehouse_is_reported_as_unavailable() -> None:
    executor, client = _executor(_warehouse("STARTING", "FAILED"))
    error = executor._timeout_error(client)  # noqa: SLF001
    assert isinstance(error, WarehouseUnavailableError)
    assert "governance-wh is starting, health FAILED" in error.message
    assert error.details["state"] == "STARTING"


def test_timeout_on_a_running_warehouse_is_a_statement_timeout() -> None:
    executor, client = _executor(_warehouse("RUNNING", "HEALTHY"))
    error = executor._timeout_error(client)  # noqa: SLF001
    assert type(error) is StatementTimeoutError
    assert "running warehouse governance-wh" in error.message


def test_timeout_diagnosis_falls_back_when_lookup_fails() -> None:
    executor, client = _executor(RuntimeError("no permission"))
    error = executor._timeout_error(client)  # noqa: SLF001
    assert type(error) is StatementTimeoutError
    assert error.details == {"warehouseId": "wh-1", "waitedSeconds": 90}


# --------------------------------------------------------------------------- usage overview


def test_failed_usage_overview_is_not_cached(container: Any) -> None:
    calls = {"n": 0}

    class FlakySql:
        def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise WarehouseUnavailableError("SQL warehouse governance-wh is starting")
            return []

    service = container.usage_analytics
    service._sql = FlakySql()  # noqa: SLF001
    service._cache.invalidate()  # noqa: SLF001
    first = service.overview(30)
    assert first.available is False
    assert first.error_code == "warehouse_unavailable"
    assert "starting" in (first.detail or "")
    second = service.overview(30)
    assert second.available is True

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models.data_access import (
    DataAccessPlanSummary,
    DataAccessRun,
    SyncRunStatus,
    SyncTrigger,
)
from backend.app.repositories import lakebase_views
from backend.app.services.data_access_analytics import (
    DEFAULT_ANALYTICS_DAYS,
    MAX_ANALYTICS_DAYS,
    build_analytics,
    clamp_days,
)
from backend.tests.conftest import headers_for

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _run(
    run_id: str,
    *,
    hours_ago: float,
    status: SyncRunStatus = SyncRunStatus.SUCCESS,
    mode: str = "dry_run",
    fingerprint: str | None = "aaaa",
    grants_add: int = 0,
    grants_revoke: int = 0,
    members_add: int = 0,
    members_remove: int = 0,
    duration_seconds: int = 10,
    error: str | None = None,
) -> DataAccessRun:
    started = NOW - timedelta(hours=hours_ago)
    finished = started + timedelta(seconds=duration_seconds) if status != SyncRunStatus.RUNNING else None
    summary = None
    if status != SyncRunStatus.RUNNING:
        summary = DataAccessPlanSummary(
            grants_to_add=grants_add,
            grants_to_revoke=grants_revoke,
            members_to_add=members_add,
            members_to_remove=members_remove,
        )
    return DataAccessRun(
        run_id=run_id,
        status=status,
        trigger=SyncTrigger.SCHEDULE,
        mode=mode,
        started_at=started,
        finished_at=finished,
        triggered_by="scheduler",
        error=error,
        summary=summary if status == SyncRunStatus.SUCCESS else None,
        fingerprint=fingerprint if status == SyncRunStatus.SUCCESS else None,
    )


def test_clamp_days_defaults_and_bounds() -> None:
    assert clamp_days(None) == DEFAULT_ANALYTICS_DAYS
    assert clamp_days(0) == 1
    assert clamp_days(999) == MAX_ANALYTICS_DAYS
    assert clamp_days(7) == 7


def test_build_analytics_empty_history() -> None:
    result = build_analytics([], now=NOW)
    assert result.kpis.total_runs == 0
    assert result.kpis.success_rate is None
    assert result.kpis.avg_duration_seconds is None
    assert result.daily == []
    assert result.failures == []
    assert result.fingerprints == []


def test_build_analytics_kpis_and_daily_rollup() -> None:
    runs = [
        _run(
            "r1",
            hours_ago=1,
            grants_add=3,
            grants_revoke=1,
            members_add=2,
            members_remove=1,
            duration_seconds=20,
        ),
        _run("r2", hours_ago=2, status=SyncRunStatus.FAILED, error="boom"),
        _run("r3", hours_ago=3, status=SyncRunStatus.RUNNING),
        _run("r4", hours_ago=30, mode="apply", grants_add=5, fingerprint="bbbb", duration_seconds=40),
    ]
    result = build_analytics(runs, now=NOW, days=30)

    kpis = result.kpis
    assert kpis.total_runs == 4
    assert kpis.success_runs == 2
    assert kpis.failed_runs == 1
    assert kpis.running_runs == 1
    assert kpis.success_rate == round(2 / 3, 4)
    assert kpis.avg_duration_seconds == 23.3  # (20 + 10 + 40) / 3 finished runs
    assert kpis.last_failure_at == NOW - timedelta(hours=2)
    assert kpis.last_success_at == NOW - timedelta(hours=1)
    assert kpis.distinct_fingerprints == 2
    assert kpis.fingerprint_changes == 0

    by_key = {(d.day, d.mode): d for d in result.daily}
    today = (NOW - timedelta(hours=1)).date().isoformat()
    assert by_key[(today, "dry_run")].runs == 3
    assert by_key[(today, "dry_run")].success == 1
    assert by_key[(today, "dry_run")].failed == 1
    assert by_key[(today, "dry_run")].grants_added == 3
    assert by_key[(today, "dry_run")].grants_revoked == 1
    assert by_key[(today, "dry_run")].members_added == 2
    assert by_key[(today, "dry_run")].members_removed == 1
    yesterday = (NOW - timedelta(hours=30)).date().isoformat()
    assert by_key[(yesterday, "apply")].grants_added == 5
    # Finished runs that day: success 20s + failed 10s -> 15s average.
    assert by_key[(today, "dry_run")].avg_duration_seconds == 15.0
    assert by_key[(yesterday, "apply")].avg_duration_seconds == 40.0

    assert [f.run_id for f in result.failures] == ["r2"]
    assert result.failures[0].error == "boom"


def test_build_analytics_ignores_runs_outside_window() -> None:
    runs = [_run("old", hours_ago=24 * 10), _run("new", hours_ago=1)]
    result = build_analytics(runs, now=NOW, days=7)
    assert result.kpis.total_runs == 1
    assert result.days == 7


def test_fingerprint_changes_counted_per_mode() -> None:
    runs = [
        _run("a", hours_ago=5, fingerprint="one"),
        _run("b", hours_ago=4, fingerprint="one"),
        _run("c", hours_ago=3, fingerprint="two"),
        _run("d", hours_ago=2, mode="apply", fingerprint="two"),
        _run("e", hours_ago=1, fingerprint="two"),
    ]
    result = build_analytics(runs, now=NOW, days=30)
    assert result.kpis.fingerprint_changes == 1
    assert result.kpis.distinct_fingerprints == 2
    top = result.fingerprints[0]
    assert top.fingerprint == "two"
    assert top.runs == 3
    assert top.first_seen == NOW - timedelta(hours=3)
    assert top.last_seen == NOW - timedelta(hours=1)


def test_failures_limit_and_newest_first() -> None:
    runs = [_run(f"f{i}", hours_ago=i + 1, status=SyncRunStatus.FAILED, error=f"err {i}") for i in range(12)]
    result = build_analytics(runs, now=NOW, days=30)
    assert len(result.failures) == 10
    assert result.failures[0].run_id == "f0"


def test_success_without_fingerprint_is_not_counted_as_distinct() -> None:
    runs = [_run("x", hours_ago=1, fingerprint=None)]
    result = build_analytics(runs, now=NOW, days=30)
    assert result.kpis.success_runs == 1
    assert result.kpis.distinct_fingerprints == 0
    assert result.fingerprints == []


def test_analytics_route_returns_payload(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    body = client.get("/api/data-access/analytics?days=7", headers=admin)
    assert body.status_code == 200
    payload = body.json()
    assert payload["days"] == 7
    assert payload["kpis"]["totalRuns"] == 0
    assert payload["daily"] == []


def test_analytics_route_forbidden_for_non_admin(client: TestClient) -> None:
    user = headers_for("analyst@example.com")
    assert client.get("/api/data-access/analytics", headers=user).status_code == 403


def test_analytics_route_rejects_out_of_range_days(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    assert client.get("/api/data-access/analytics?days=0", headers=admin).status_code == 422
    assert client.get("/api/data-access/analytics?days=31", headers=admin).status_code == 422


def test_lakebase_views_cover_every_dashboard_source() -> None:
    names = [sql.split()[2] for sql in lakebase_views.VIEW_DEFINITIONS]
    assert names == [
        "{schema}.data_access_runs_v",
        "{schema}.data_access_run_grants_v",
        "{schema}.data_access_run_membership_v",
        "{schema}.data_access_daily_v",
        "{schema}.data_access_failures_v",
    ]
    drops = lakebase_views.drop_statements()
    assert len(drops) == len(lakebase_views.VIEW_DEFINITIONS)
    assert all(stmt.startswith("DROP VIEW IF EXISTS {schema}.") for stmt in drops)
    # Dependents are dropped before runs_v, which they read from.
    assert drops[-1].endswith("data_access_runs_v")

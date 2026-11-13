"""App cost report: pricing, sync-window attribution, caching, failures, and the admin route."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.container import ServiceContainer
from backend.app.models.app_cost import AppCostReport
from backend.app.services.app_cost import (
    AppCostService,
    _overlap_fraction,
    _UsageRecord,
    sync_usd,
)
from backend.tests.conftest import headers_for

APP_ID = "da48b54e-2dd9-4baf-98a3-67b16e0413db"


class FakeSql:
    """Records statements and returns canned rows (or raises the configured error)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        if self.error is not None:
            raise self.error
        return list(self.rows)


class FakeRuns:
    """Serves finished sync runs that started at or after the requested time."""

    def __init__(self, runs: list[SimpleNamespace]) -> None:
        self.runs = runs
        self.since: list[datetime] = []

    def finished_runs_since(self, since: datetime) -> list[SimpleNamespace]:
        self.since.append(since)
        return [run for run in self.runs if run.started_at >= since]


def _workspace() -> SimpleNamespace:
    client = SimpleNamespace(apps=SimpleNamespace(get=lambda name: SimpleNamespace(id=APP_ID)))
    return SimpleNamespace(admin_configured=True, admin=lambda: client, app=lambda: client)


def _config() -> SimpleNamespace:
    return SimpleNamespace(app_name="data-governance-app", warehouse_id="wh-1", data_access_sync_job_id="42")


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _row(start: datetime, end: datetime, origin: str, usd: float | None) -> dict[str, Any]:
    return {
        "usage_start_time": start.isoformat(),
        "usage_end_time": end.isoformat(),
        "usage_date": start.date().isoformat(),
        "origin": origin,
        "usd": None if usd is None else str(usd),
    }


def _run(start: datetime, end: datetime) -> SimpleNamespace:
    return SimpleNamespace(started_at=start, finished_at=end)


def _service(sql: FakeSql, runs: FakeRuns | None = None) -> AppCostService:
    return AppCostService(sql, _workspace(), _config(), runs or FakeRuns([]))  # type: ignore[arg-type]


# --- pricing and attribution -------------------------------------------------------------------


def test_overlap_fraction_prorates_within_a_record() -> None:
    day = date(2026, 11, 10)
    start, end = _at(day, 10), _at(day, 11)
    assert _overlap_fraction(start, end, _at(day, 10, 15), _at(day, 10, 30)) == pytest.approx(0.25)
    assert _overlap_fraction(start, end, _at(day, 9), _at(day, 12)) == pytest.approx(1.0)
    assert _overlap_fraction(start, end, _at(day, 11), _at(day, 12)) == pytest.approx(0.0)


def test_overlap_fraction_handles_zero_length_records() -> None:
    day = date(2026, 11, 10)
    instant = _at(day, 10, 20)
    assert _overlap_fraction(instant, instant, _at(day, 10), _at(day, 11)) == 1.0
    assert _overlap_fraction(instant, instant, _at(day, 11), _at(day, 12)) == 0.0


def test_sync_usd_counts_only_warehouse_and_job_records_in_run_windows() -> None:
    day = date(2026, 11, 10)
    records = [
        _UsageRecord(_at(day, 10), _at(day, 11), day, "SQL", 10.0),
        _UsageRecord(_at(day, 10, 20), _at(day, 10, 40), day, "JOBS", 6.0),
        # Apps compute is not a sync cost, even inside the window.
        _UsageRecord(_at(day, 10), _at(day, 11), day, "APPS", 99.0),
        # Unpriced records contribute nothing.
        _UsageRecord(_at(day, 10), _at(day, 11), day, "SQL", None),
    ]
    runs = [_run(_at(day, 10, 15), _at(day, 10, 30))]
    # SQL: 10.0 * 0.25 = 2.5; JOBS: 6.0 * 0.5 = 3.0
    assert sync_usd(records, runs) == pytest.approx(5.5)


def test_sync_usd_sums_across_runs_and_ignores_unfinished_runs() -> None:
    day = date(2026, 11, 10)
    records = [_UsageRecord(_at(day, 10), _at(day, 12), day, "SQL", 8.0)]
    runs = [
        _run(_at(day, 10), _at(day, 10, 30)),  # 0.25 of the hour
        _run(_at(day, 11), _at(day, 11, 30)),  # 0.5 of the hour
        SimpleNamespace(started_at=_at(day, 11, 45), finished_at=None),  # still running: ignored
    ]
    # The record spans two hours ($8). Each 30-minute run covers a quarter of that span: 2.0 + 2.0.
    assert sync_usd(records, runs) == pytest.approx(8.0 * 0.25 + 8.0 * 0.25)


# --- report -------------------------------------------------------------------------------------


def test_report_builds_daily_series_and_summary() -> None:
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    six_days_ago = today - timedelta(days=6)
    rows = [
        _row(_at(today, 0), _at(today, 1), "APPS", 1.5),
        _row(_at(yesterday, 0), _at(yesterday, 1), "APPS", 2.0),
        _row(_at(six_days_ago, 0), _at(six_days_ago, 1), "APPS", 4.0),
        _row(_at(today, 10), _at(today, 11), "SQL", 10.0),
        _row(_at(today, 10, 20), _at(today, 10, 40), "JOBS", 6.0),
        _row(_at(yesterday, 0), _at(yesterday, 1), "APPS", None),  # no list price
    ]
    sql = FakeSql(rows)
    runs = FakeRuns([_run(_at(today, 10, 15), _at(today, 10, 30))])
    service = _service(sql, runs)

    report = service.report(7)

    assert report.available is True
    assert report.days == 7
    assert len(report.daily) == 7
    assert report.daily[0].usage_date == six_days_ago
    assert report.daily[-1].usage_date == today
    assert report.daily[-1].app_usd == pytest.approx(1.5)
    assert report.daily[-1].warehouse_usd == pytest.approx(10.0)
    assert report.daily[-1].job_usd == pytest.approx(6.0)
    assert report.today_app_usd == pytest.approx(1.5)
    assert report.app_usd_last_7_days == pytest.approx(1.5 + 2.0 + 4.0)
    assert report.app_usd_per_active_day == pytest.approx((1.5 + 2.0 + 4.0) / 3)
    assert report.unpriced_records == 1
    assert report.sync_count == 1
    assert report.sync_usd == pytest.approx(2.5 + 3.0)
    assert report.avg_usd_per_sync == pytest.approx(5.5)
    assert "lags" in report.lag_note


def test_report_uses_admin_sql_with_named_parameters_and_run_window_start() -> None:
    today = datetime.now(UTC).date()
    sql = FakeSql([])
    runs = FakeRuns([])
    report = _service(sql, runs).report(3)

    statement, parameters = sql.calls[0]
    assert ":since" in statement and ":app_id" in statement and ":warehouse_id" in statement
    assert parameters == {
        "since": (today - timedelta(days=2)).isoformat(),
        "app_id": APP_ID,
        "warehouse_id": "wh-1",
        "job_id": "42",
    }
    assert runs.since == [datetime.combine(today - timedelta(days=2), time.min, tzinfo=UTC)]
    assert report.sync_count == 0
    assert report.avg_usd_per_sync is None
    assert report.app_usd_per_active_day is None


def test_report_with_no_usage_returns_zero_days() -> None:
    report = _service(FakeSql([])).report(2)
    assert report.available is True
    assert [day.app_usd for day in report.daily] == [0.0, 0.0]
    assert report.today_app_usd == 0.0


def test_failed_billing_read_is_reported_and_not_cached() -> None:
    sql = FakeSql(error=RuntimeError("system.billing.usage is not accessible"))
    service = _service(sql)

    first = service.report(5)
    assert first.available is False
    assert "not accessible" in (first.detail or "")
    assert first.daily == []

    sql.error = None
    second = service.report(5)
    assert second.available is True
    assert len(sql.calls) == 2  # the failure was not cached, so the retry hit billing again


def test_successful_report_is_cached_per_window() -> None:
    sql = FakeSql([])
    service = _service(sql)
    service.report(7)
    service.report(7)
    assert len(sql.calls) == 1
    service.report(3)
    assert len(sql.calls) == 2


def test_missing_app_id_is_reported_as_unavailable() -> None:
    workspace = SimpleNamespace(
        admin_configured=True,
        admin=lambda: SimpleNamespace(apps=SimpleNamespace(get=lambda name: SimpleNamespace(id=None))),
        app=lambda: None,
    )
    service = AppCostService(FakeSql([]), workspace, _config(), FakeRuns([]))  # type: ignore[arg-type]
    report = service.report(4)
    assert report.available is False
    assert "has no id" in (report.detail or "")


@pytest.mark.parametrize(("requested", "expected"), [(None, 30), (0, 1), (7, 7), (999, 30)])
def test_clamp_days(requested: int | None, expected: int) -> None:
    assert AppCostService.clamp_days(requested) == expected


# --- route --------------------------------------------------------------------------------------


def test_app_cost_route_is_admin_only_and_validates_days(
    client: TestClient, container: ServiceContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int | None] = []

    def fake_report(days: int | None = None) -> AppCostReport:
        calls.append(days)
        return AppCostReport(
            available=True,
            app_name="data-governance-app",
            days=days or 30,
            generated_at=datetime.now(UTC),
            lag_note="lag",
            daily=[],
        )

    monkeypatch.setattr(container.app_cost, "report", fake_report)
    admin = headers_for("admin@example.com")

    body = client.get("/api/performance/app-cost?days=7", headers=admin).json()
    assert calls == [7]
    assert body["available"] is True
    assert body["days"] == 7

    assert client.get("/api/performance/app-cost", headers=admin).status_code == 200
    assert client.get("/api/performance/app-cost?days=99", headers=admin).status_code == 422
    assert (
        client.get("/api/performance/app-cost", headers=headers_for("analyst@example.com")).status_code == 403
    )

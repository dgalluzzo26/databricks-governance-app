"""Tests for usage analytics routes and service (/api/usage-analytics)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.tests.conftest import headers_for


class _FakeUsageSql:
    """Mock SQL queries for usage analytics tests."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((statement, parameters))
        # Last run
        if "FROM `data_governance_app`.`usage`.`rollup_runs`" in statement:
            return [
                {
                    "run_id": "r-123",
                    "status": "success",
                    "finished_at": "2026-11-17T06:04:00Z",
                    "data_as_of": "2026-11-16T22:00:00Z",
                    "window_start": "2026-08-19",
                    "window_end": "2026-11-17",
                    "read_events": "467",
                    "catalogs": "data_governance_demo,sandbox",
                }
            ]
        # KPIs - tables
        if "coalesce(sum(reads), 0) AS total_reads" in statement:
            return [
                {
                    "total_reads": "150",
                    "active_users": "12",
                    "tables_accessed": "8",
                }
            ]
        # KPIs - sensitive columns
        if "coalesce(sum(reads), 0) AS sensitive_reads" in statement:
            return [
                {
                    "sensitive_reads": "80",
                    "clear_reads": "10",
                    "masked_reads": "65",
                    "exempt_reads": "5",
                }
            ]
        # Daily trends
        if "WITH table_days AS" in statement:
            return [
                {
                    "event_date": "2026-11-15",
                    "reads": "50",
                    "users": "5",
                    "clear_reads": "2",
                    "masked_reads": "20",
                },
                {
                    "event_date": "2026-11-16",
                    "reads": "100",
                    "users": "10",
                    "clear_reads": "8",
                    "masked_reads": "45",
                },
            ]
        # Sensitive breakdown
        if "SELECT sensitivity_path," in statement and "GROUP BY sensitivity_path" in statement:
            return [
                {
                    "sensitivity_path": "finance.budget.PII.Name",
                    "reads": "80",
                    "clear_reads": "10",
                    "masked_reads": "65",
                    "exempt_reads": "5",
                    "distinct_users": "4",
                    "distinct_tables": "2",
                }
            ]
        # Table detail: user rows
        if "user_table_usage_daily`" in statement and ":table" in statement:
            return [
                {
                    "user_name": "david.galluzzo@databricks.com",
                    "reads": "45",
                    "last_read_at": "2026-11-16T22:00:00Z",
                }
            ]
        # Table detail: column rows
        if "column_usage_daily`" in statement and ":table" in statement:
            return [
                {
                    "column_name": "first_name",
                    "sensitivity_path": "public.PII.Name",
                    "access_mode": "masked",
                    "reads": "45",
                    "distinct_users": "1",
                    "last_read_at": "2026-11-16T22:00:00Z",
                }
            ]
        # Table detail: recent reads
        if "read_events`" in statement and ":table" in statement:
            return [
                {
                    "event_time": "2026-11-16T22:00:00Z",
                    "user_name": "david.galluzzo@databricks.com",
                    "entity_type": "DBSQL_QUERY",
                    "direct_access": True,
                }
            ]
        # Top tables
        if "table_full_name" in statement and "GROUP BY u.table_full_name" in statement:
            return [
                {
                    "table_full_name": "data_governance_demo.people.employees",
                    "catalog_name": "data_governance_demo",
                    "schema_name": "people",
                    "table_name": "employees",
                    "reads": "120",
                    "distinct_users": "8",
                    "last_read_at": "2026-11-16T22:00:00Z",
                }
            ]
        # Top users
        if "user_name" in statement and "GROUP BY user_name" in statement:
            return [
                {
                    "user_name": "david.galluzzo@databricks.com",
                    "reads": "120",
                    "distinct_tables": "5",
                    "last_read_at": "2026-11-16T22:00:00Z",
                }
            ]
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        self.queries.append((statement, parameters))


def test_usage_overview_admin(client: TestClient, container: Any) -> None:
    container.usage_analytics._sql = _FakeUsageSql()
    container.usage_analytics._cache.invalidate()

    resp = client.get("/api/usage-analytics/overview", headers=headers_for("admin@example.com"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["kpis"]["totalTableReads"] == 150
    assert data["kpis"]["activeUsers"] == 12
    assert data["kpis"]["tablesAccessed"] == 8
    assert data["kpis"]["sensitiveColumnReads"] == 80
    assert data["kpis"]["clearReads"] == 10
    assert data["kpis"]["maskedReads"] == 65
    assert data["kpis"]["exemptReads"] == 5
    assert data["lastRun"]["runId"] == "r-123"
    assert data["lastRun"]["catalogs"] == ["data_governance_demo", "sandbox"]
    assert len(data["dailyTrends"]) == 2
    assert len(data["topTables"]) == 1
    assert data["topTables"][0]["tableFullName"] == "data_governance_demo.people.employees"
    assert len(data["topUsers"]) == 1
    assert data["topUsers"][0]["userName"] == "david.galluzzo@databricks.com"
    assert len(data["sensitiveBreakdown"]) == 1


def test_usage_overview_forbidden_for_non_admin(client: TestClient) -> None:
    resp = client.get("/api/usage-analytics/overview", headers=headers_for("analyst@example.com"))
    assert resp.status_code == 403


def test_list_usage_tables_and_users(client: TestClient, container: Any) -> None:
    container.usage_analytics._sql = _FakeUsageSql()
    container.usage_analytics._cache.invalidate()

    tables_resp = client.get(
        "/api/usage-analytics/tables",
        params={"days": 14, "q": "people"},
        headers=headers_for("admin@example.com"),
    )
    assert tables_resp.status_code == 200
    tables = tables_resp.json()
    assert tables["total"] == 1
    assert tables["tables"][0]["tableName"] == "employees"

    users_resp = client.get(
        "/api/usage-analytics/users",
        params={"days": 14, "q": "david"},
        headers=headers_for("admin@example.com"),
    )
    assert users_resp.status_code == 200
    users = users_resp.json()
    assert users["total"] == 1
    assert users["users"][0]["userName"] == "david.galluzzo@databricks.com"


def test_table_detail(client: TestClient, container: Any) -> None:
    container.usage_analytics._sql = _FakeUsageSql()
    container.usage_analytics._cache.invalidate()

    resp = client.get(
        "/api/usage-analytics/table-detail",
        params={"table_full_name": "data_governance_demo.people.employees", "days": 30},
        headers=headers_for("admin@example.com"),
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["tableFullName"] == "data_governance_demo.people.employees"
    assert detail["reads"] == 45
    assert detail["distinctUsers"] == 1
    assert len(detail["users"]) == 1
    assert len(detail["columns"]) == 1
    assert detail["columns"][0]["columnName"] == "first_name"
    assert detail["columns"][0]["accessMode"] == "masked"
    assert len(detail["recentReads"]) == 1


def test_refresh_usage_job(client: TestClient, container: Any) -> None:
    class _FakeJobs:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run_now(self, job_id: int, job_parameters: dict[str, Any]) -> Any:
            self.calls.append({"job_id": job_id, "params": job_parameters})
            from types import SimpleNamespace

            return SimpleNamespace(run_id="999888")

    container.usage_analytics._workspace = type(
        "W", (), {"app": lambda _self: type("A", (), {"jobs": _FakeJobs()})()}
    )()
    container.config.usage_rollup_job_id = "12345"

    resp = client.post("/api/usage-analytics/refresh", headers=headers_for("admin@example.com"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] is True
    assert data["jobId"] == "12345"
    assert data["runId"] == "999888"


def test_refresh_missing_job_id_raises_error(client: TestClient, container: Any) -> None:
    container.config.usage_rollup_job_id = None
    resp = client.post("/api/usage-analytics/refresh", headers=headers_for("admin@example.com"))
    assert resp.status_code == 422
    assert "not configured" in resp.json()["error"]["message"]


def test_export_usage_csv(client: TestClient, container: Any) -> None:
    container.usage_analytics._sql = _FakeUsageSql()
    container.usage_analytics._cache.invalidate()

    resp = client.get(
        "/api/usage-analytics/export",
        params={"kind": "tables", "days": 30},
        headers=headers_for("admin@example.com"),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in resp.headers["content-disposition"]
    text = resp.text
    assert "Table,Catalog,Schema,Name,Reads,Distinct Users,Last Read At" in text
    assert "data_governance_demo.people.employees" in text

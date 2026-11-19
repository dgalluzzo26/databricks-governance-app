"""Unit tests for the usage-analytics rollup (SQL builders + window planning + run loop)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from backend.app.services.usage_rollup import (
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    EVENT_RETENTION_DAYS,
    UsageRollup,
    UsageTables,
    access_holder_sql,
    access_snapshot_sql,
    aggregate_sql,
    catalog_filter,
    plan_window,
    refresh_column_events_sql,
    refresh_exempt_sql,
    refresh_read_events_sql,
    retention_sql,
    table_ddl,
)
from backend.jobs import run_usage_rollup as job


class _FakeSql:
    """Records statements and returns canned query results by substring match."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.responses: dict[str, list[dict[str, Any]]] = {}

    def on(self, needle: str, rows: list[dict[str, Any]]) -> None:
        self.responses[needle] = rows

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.executed.append((statement, parameters))
        for needle, rows in self.responses.items():
            if needle in statement:
                return rows
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        self.executed.append((statement, parameters))


def test_plan_window_first_run_backfills() -> None:
    today = date(2026, 11, 17)
    start = plan_window(today, None, backfill_days=90, lookback_days=3)
    assert start == date(2026, 8, 19)


def test_plan_window_lookback_after_success() -> None:
    today = date(2026, 11, 17)
    start = plan_window(today, date(2026, 11, 16), backfill_days=90, lookback_days=3)
    assert start == date(2026, 11, 13)


def test_plan_window_clamps_to_retention() -> None:
    today = date(2026, 11, 17)
    start = plan_window(today, None, backfill_days=999, lookback_days=3)
    assert start == today - __import__("datetime").timedelta(days=EVENT_RETENTION_DAYS)


def test_plan_window_defaults_match_constants() -> None:
    assert DEFAULT_BACKFILL_DAYS == 90
    assert DEFAULT_LOOKBACK_DAYS == 3


def test_catalog_filter_empty_is_false() -> None:
    assert catalog_filter("source_table_catalog", []) == "FALSE"


def test_catalog_filter_quotes_names() -> None:
    clause = catalog_filter("c", ["data_governance_demo", "sandbox"])
    assert "data_governance_demo" in clause
    assert "sandbox" in clause
    assert clause.startswith("c IN (")


def test_table_ddl_covers_expected_tables() -> None:
    tables = UsageTables(catalog="data_governance_app", identity_table="x.y.z")
    ddl = "\n".join(table_ddl(tables))
    for name in (
        "rollup_runs",
        "read_events",
        "column_read_events",
        "table_usage_daily",
        "user_table_usage_daily",
        "column_usage_daily",
        "user_group_membership",
        "mask_exempt_principals",
        "access_snapshot",
        "group_member_snapshot",
        "governed_table_snapshot",
        "table_access_snapshot",
    ):
        assert name in ddl
    assert "`data_governance_app`.`usage`.`read_events`" in ddl


def test_refresh_sql_scopes_to_catalogs() -> None:
    tables = UsageTables(catalog="app", identity_table="app.app.identity_attributes")
    catalogs = ["data_governance_demo"]
    read = "\n".join(refresh_read_events_sql(tables, catalogs))
    col = "\n".join(refresh_column_events_sql(tables, catalogs))
    assert "system.access.table_lineage" in read
    assert "system.access.column_lineage" in col
    assert "data_governance_demo" in read
    assert "DELETE FROM" in read
    assert "event_date >= :start" in read


def test_aggregate_and_access_sql_mention_sensitivity() -> None:
    tables = UsageTables(catalog="app", identity_table="`app`.`app`.`identity_attributes`")
    agg = "\n".join(aggregate_sql(tables, ["data_governance_demo"]))
    assert "access_mode" in agg or "'masked'" in agg
    assert "sensitivity" in agg
    assert "has_sensitivity" not in agg  # evaluated in SQL via held paths, not the UDF
    snap = "\n".join(access_snapshot_sql(tables))
    assert "identity_attributes" in snap
    assert "current_date()" in snap


def test_access_holder_sql_matches_hierarchical_tag_values() -> None:
    tables = UsageTables(catalog="app", identity_table="`app`.`app`.`identity_attributes`")
    sql = "\n".join(access_holder_sql(tables, ["data_governance_demo"], ["data_access"], "access_path"))
    assert "governed_table_snapshot" in sql
    assert "table_access_snapshot" in sql
    assert "group_member_snapshot" in sql
    assert "startswith" in sql
    assert "attribute_value <> '*'" in sql


def test_retention_sql_uses_configured_windows() -> None:
    tables = UsageTables(catalog="app", identity_table="x")
    sql = "\n".join(retention_sql(tables))
    assert str(EVENT_RETENTION_DAYS) in sql
    assert "read_events" in sql
    assert "column_usage_daily" in sql


def test_exempt_sql_explodes_except_principals() -> None:
    tables = UsageTables(catalog="app", identity_table="x")
    sql = "\n".join(refresh_exempt_sql(tables))
    assert "except_principals" in sql
    assert "COLUMN_MASK" in sql


def test_usage_rollup_run_happy_path() -> None:
    sql = _FakeSql()
    sql.on("catalog_tags", [{"catalog_name": "data_governance_demo"}])
    sql.on("max(window_end)", [{"window_end": None}])
    # Most specific needles first: the users query also mentions the read_events table.
    sql.on("UNION", [{"user_name": "ana@example.com"}])
    sql.on("max(event_time)", [{"data_as_of": "2026-11-16T22:00:00Z"}])
    sql.on("FROM `app`.`usage`.`read_events`", [{"count(*)": "12"}])
    sql.on("FROM `app`.`usage`.`column_read_events`", [{"count(*)": "34"}])

    tables = UsageTables(catalog="app", identity_table="`app`.`app`.`identity_attributes`")
    groups: dict[str, list[str]] = {"ana@example.com": ["finance", "admins"]}
    rollup = UsageRollup(sql, tables, ("data_access_controlled", "true"), groups.__getitem__)
    result = rollup.run(
        trigger="user",
        actor="admin@example.com",
        backfill_days=30,
        lookback_days=3,
        run_id="run-1",
        today=date(2026, 11, 17),
    )
    assert result.status == "success"
    assert result.run_id == "run-1"
    assert result.catalogs == ["data_governance_demo"]
    assert result.read_events == 12
    assert result.column_read_events == 34
    assert result.users_resolved == 1
    assert result.window_start == date(2026, 10, 18)
    assert result.window_end == date(2026, 11, 17)
    assert result.data_as_of == "2026-11-16T22:00:00Z"
    assert result.error is None
    # DDL + start record + lineage deletes/inserts + finish update
    assert any("CREATE TABLE IF NOT EXISTS" in s for s, _ in sql.executed)
    assert any("INSERT INTO `app`.`usage`.`rollup_runs`" in s for s, _ in sql.executed)
    assert any("UPDATE `app`.`usage`.`rollup_runs`" in s for s, _ in sql.executed)
    assert any("user_group_membership" in s and "INSERT INTO" in s for s, _ in sql.executed)


def test_usage_rollup_records_failure() -> None:
    class _Boom(_FakeSql):
        def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
            self.executed.append((statement, parameters))
            if "CREATE TABLE" in statement or "rollup_runs" in statement:
                # DDL and run-record writes succeed; only lineage work fails.
                return
            raise RuntimeError("warehouse down")

        def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            self.executed.append((statement, parameters))
            if "UPDATE" in statement:
                return []
            raise RuntimeError("warehouse down")

    sql = _Boom()
    tables = UsageTables(catalog="app", identity_table="x")
    result = UsageRollup(sql, tables, ("data_access_controlled", "true"), lambda _: []).run(
        run_id="fail-1", today=date(2026, 11, 17)
    )
    assert result.status == "failed"
    assert result.error and "warehouse down" in result.error
    assert any(params and params.get("status") == "failed" for _, params in sql.executed if params)


def test_job_parse_args_defaults() -> None:
    args = job._parse_args([])  # noqa: SLF001
    assert args.trigger == "schedule"
    assert args.actor == "scheduler"
    assert args.backfill_days == 90
    assert args.lookback_days == 3
    assert args.usage_schema == "usage"


def test_system_grant_statements() -> None:
    from provisioning.steps.usage_analytics import SYSTEM_SCHEMAS, system_grant_statements

    stmts = system_grant_statements("bbf03021-a1ad-4721-a6a3-ddd500982f72")
    assert any("USE CATALOG ON CATALOG system" in s for s in stmts)
    for schema in SYSTEM_SCHEMAS:
        assert any(schema in s and "SELECT" in s for s in stmts)


def test_account_group_resolver_fetches_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Client:
        class api_client:
            @staticmethod
            def do(method: str, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
                del method
                calls.append((path, query or {}))
                if path.endswith("/Users") and "filter" in (query or {}):
                    return {"Resources": [{"id": "u1", "userName": "ana@example.com"}]}
                if path.endswith("/Users/u1"):
                    return {
                        "id": "u1",
                        "groups": [
                            {"display": "admins"},
                            {"display": "dac_finance"},
                        ],
                    }
                return {}

        def users(self):  # pragma: no cover - unused when Account SCIM works
            raise AssertionError("workspace fallback should not run")

    resolve = job.account_group_resolver(_Client())  # type: ignore[arg-type]
    assert resolve("ana@example.com") == ["admins", "dac_finance"]
    assert any(path.endswith("/Users/u1") for path, _ in calls)

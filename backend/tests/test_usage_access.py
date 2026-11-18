"""Tests for the access reports (who holds access, and how it is used)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import InvalidRequestError
from backend.app.services.usage_access import UsageAccessService, parse_grants
from backend.tests.conftest import headers_for


class _FakeAccessSql:
    """Returns canned rows for each access query, routed by the distinctive part of the statement."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((statement, parameters))
        # Count wrappers embed the listing SQL, so they must be matched first.
        if "SELECT count(*) AS n FROM" in statement:
            return [{"n": "2"}]
        if "SELECT max(snapshot_date)" in statement:
            return [{"as_of": "2026-11-17"}]
        if "SELECT tag_value FROM governed" in statement:
            return [{"tag_value": "finance.budget"}]
        if "SELECT g.table_full_name" in statement:
            return [
                {
                    "table_full_name": "data_governance_demo.finance.budget",
                    "catalog_name": "data_governance_demo",
                    "schema_name": "finance",
                    "table_name": "budget",
                    "tag_value": "finance.budget",
                    "holders": "3",
                    "holders_with_reads": "1",
                    "reads": "40",
                    "readers_without_access": "1",
                    "last_read_at": "2026-11-16T10:00:00Z",
                }
            ]
        if "SELECT p.user_name, p.grants" in statement:
            return [
                {
                    "user_name": "ana@example.com",
                    "grants": "GROUP|finance_readers|finance",
                    "reads": "40",
                    "last_read_at": "2026-11-16T10:00:00Z",
                },
                {
                    "user_name": "bo@example.com",
                    "grants": "USER|bo@example.com|finance.budget",
                    "reads": "0",
                    "last_read_at": None,
                },
            ]
        if "SELECT w.user_name, w.reads" in statement:
            return [{"user_name": "eve@example.com", "reads": "7", "last_read_at": "2026-11-15T09:00:00Z"}]
        if "SELECT p.table_full_name, p.user_name, p.grants, g.tag_value" in statement:
            return [
                {
                    "table_full_name": "data_governance_demo.finance.budget",
                    "user_name": "bo@example.com",
                    "grants": "USER|bo@example.com|finance.budget",
                    "tag_value": "finance.budget",
                }
            ]
        if "SELECT w.table_full_name, w.user_name, w.reads" in statement:
            return [
                {
                    "table_full_name": "data_governance_demo.finance.budget",
                    "user_name": "eve@example.com",
                    "reads": "7",
                    "last_read_at": "2026-11-15T09:00:00Z",
                }
            ]
        if "SELECT p.table_full_name, g.tag_value, p.user_name" in statement:
            return [
                {
                    "table_full_name": "data_governance_demo.finance.budget",
                    "tag_value": "finance.budget",
                    "user_name": "ana@example.com",
                    "grants": "GROUP|finance_readers|finance",
                    "reads": "40",
                    "last_read_at": "2026-11-16T10:00:00Z",
                }
            ]
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        self.queries.append((statement, parameters))


@pytest.fixture
def access(container: Any) -> UsageAccessService:
    service = UsageAccessService(_FakeAccessSql(), container.config)
    container.usage_access = service
    return service


def test_parse_grants_skips_malformed_entries() -> None:
    grants = parse_grants("GROUP|finance_readers|finance;broken;|x|y;USER|bo@example.com|finance.budget")
    assert [(g.via_type, g.via_principal, g.held_value) for g in grants] == [
        ("GROUP", "finance_readers", "finance"),
        ("USER", "bo@example.com", "finance.budget"),
    ]
    assert parse_grants(None) == []


def test_tables_summarise_holders_unused_and_reader_gaps(access: UsageAccessService) -> None:
    result = access.tables(days=30)
    assert result.as_of == "2026-11-17"
    assert result.total == 2
    table = result.tables[0]
    assert table.table_full_name == "data_governance_demo.finance.budget"
    assert table.holders == 3
    assert table.holders_with_reads == 1
    assert table.unused_holders == 2
    assert table.readers_without_access == 1
    assert table.reads == 40


def test_table_detail_lists_holders_grants_and_reader_gaps(access: UsageAccessService) -> None:
    detail = access.table_detail("Data_Governance_Demo.Finance.Budget", days=30)
    assert detail.table_full_name == "data_governance_demo.finance.budget"
    assert detail.tag_value == "finance.budget"
    ana, bo = detail.holders
    assert ana.user_name == "ana@example.com"
    assert ana.used is True
    assert ana.grants[0].via_type == "GROUP"
    assert ana.grants[0].via_principal == "finance_readers"
    assert bo.used is False
    assert bo.reads == 0
    assert [r.user_name for r in detail.readers_without_access] == ["eve@example.com"]


def test_table_detail_requires_table_name(access: UsageAccessService) -> None:
    with pytest.raises(InvalidRequestError):
        access.table_detail("   ", days=30)


def test_unused_and_no_access_listings(access: UsageAccessService) -> None:
    unused = access.unused(days=30, limit=10)
    assert unused.total == 2
    assert unused.items[0].user_name == "bo@example.com"
    assert unused.items[0].grants[0].held_value == "finance.budget"

    gaps = access.no_access(days=30, limit=10)
    assert gaps.total == 2
    assert gaps.items[0].user_name == "eve@example.com"
    assert gaps.items[0].reads == 7


def test_export_csv_kinds(access: UsageAccessService) -> None:
    tables_csv = access.export_csv("tables", days=30)
    assert tables_csv.splitlines()[0].startswith("Table,Access Tag Value,Holders")
    holders_csv = access.export_csv("holders", days=30)
    assert holders_csv.splitlines()[0].endswith(",Status")
    assert "used" in holders_csv
    assert "group finance_readers -> finance" in holders_csv
    assert "bo@example.com" not in holders_csv  # holders export is the governed pairs only
    unused_csv = access.export_csv("unused", days=30)
    assert "bo@example.com" in unused_csv
    gaps_csv = access.export_csv("no-access", days=30)
    assert "eve@example.com" in gaps_csv


def test_export_rejects_unknown_kind(access: UsageAccessService) -> None:
    with pytest.raises(InvalidRequestError):
        access.export_csv("everything", days=30)


def test_access_routes_admin_only(client: TestClient, container: Any, access: UsageAccessService) -> None:
    resp = client.get("/api/usage-analytics/access/tables", headers=headers_for("analyst@example.com"))
    assert resp.status_code == 403


def test_access_routes_return_camel_case(client: TestClient, access: UsageAccessService) -> None:
    admin = headers_for("admin@example.com")
    tables = client.get("/api/usage-analytics/access/tables", params={"days": 30}, headers=admin)
    assert tables.status_code == 200
    body = tables.json()
    assert body["asOf"] == "2026-11-17"
    assert body["tables"][0]["unusedHolders"] == 2
    assert body["tables"][0]["readersWithoutAccess"] == 1

    detail = client.get(
        "/api/usage-analytics/access/table-detail",
        params={"table_full_name": "data_governance_demo.finance.budget", "days": 30},
        headers=admin,
    )
    assert detail.status_code == 200
    holders = detail.json()["holders"]
    assert holders[0]["grants"][0]["viaPrincipal"] == "finance_readers"

    unused = client.get("/api/usage-analytics/access/unused", headers=admin)
    assert unused.status_code == 200
    assert unused.json()["items"][0]["userName"] == "bo@example.com"

    gaps = client.get("/api/usage-analytics/access/no-access", headers=admin)
    assert gaps.status_code == 200
    assert gaps.json()["items"][0]["userName"] == "eve@example.com"


def test_access_export_route_sets_attachment(client: TestClient, access: UsageAccessService) -> None:
    admin = headers_for("admin@example.com")
    resp = client.get(
        "/api/usage-analytics/access/export", params={"kind": "unused", "days": 14}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="access_unused_14d.csv"'

    bad = client.get("/api/usage-analytics/access/export", params={"kind": "nope"}, headers=admin)
    assert bad.status_code == 422

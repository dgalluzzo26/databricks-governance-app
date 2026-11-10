from __future__ import annotations

from typing import Any

import pytest

from fastapi.testclient import TestClient

from backend.app.models.access_explorer import AnalyzeTableRequest
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeSource,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.core.errors import InvalidRequestError
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.services.access_explorer import AccessExplorerService, has_sensitivity_access
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin
from backend.tests.test_mask_rules import FakeSql, MaskRuleTags, _service as mask_service


def test_has_sensitivity_prefix_rules() -> None:
    held = {"finance.budget"}
    assert has_sensitivity_access(held, "finance.budget")
    assert has_sensitivity_access(held, "finance.budget.PII.Name")
    assert not has_sensitivity_access(held, "finance")
    assert not has_sensitivity_access(held, "finance.contracts")
    assert has_sensitivity_access({"finance.budget.PII.Name"}, "finance.budget.PII.Name")
    assert not has_sensitivity_access({"finance.budget.PII.Name"}, "finance.budget")


class ExplorerSql(FakeSql):
    def __init__(self) -> None:
        super().__init__()
        self.tables = [{"ok": 1}]
        self.columns = [
            {"column_name": "s_name", "full_data_type": "string"},
            {"column_name": "s_open", "full_data_type": "string"},
            {"column_name": "id", "full_data_type": "int"},
        ]
        self.tags = [
            {
                "column_name": "s_name",
                "tag_name": "sensitivity_category",
                "tag_value": "finance.budget",
            },
            {"column_name": "s_name", "tag_name": "sensitivity", "tag_value": "PII.Name"},
            {"column_name": "s_name", "tag_name": "data_type", "tag_value": "string"},
            {"column_name": "s_name", "tag_name": "mask_rule", "tag_value": "full"},
            {
                "column_name": "s_open",
                "tag_name": "sensitivity_category",
                "tag_value": "finance.budget",
            },
            {"column_name": "s_open", "tag_name": "sensitivity", "tag_value": "PII.Name"},
            {"column_name": "s_open", "tag_name": "data_type", "tag_value": "string"},
            {"column_name": "s_open", "tag_name": "mask_rule", "tag_value": "joinable"},
            {"column_name": "s_open", "tag_name": "row_filter", "tag_value": "state"},
        ]
        self.search_rows = [
            {
                "table_catalog": "demo",
                "table_schema": "default",
                "table_name": "abac_type_test",
            }
        ]

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        text = statement
        if "instr(" in text:
            return self.search_rows
        if "information_schema.tables" in text:
            return self.tables
        if "information_schema.columns" in text:
            return self.columns
        if "information_schema.column_tags" in text and "tag_name =" in text:
            # row filter attachment query
            return [
                {
                    "catalog_name": "demo",
                    "schema_name": "default",
                    "table_name": "customers",
                    "column_name": "s_open",
                    "tag_value": "state",
                }
            ]
        if "information_schema.column_tags" in text:
            return self.tags
        return super().query(statement, parameters)


def _explorer(container):  # noqa: ANN001
    mask, sql = mask_service(container)
    # Replace with richer SQL double
    sql = ExplorerSql()
    mask._sql = sql  # type: ignore[attr-defined]
    mask._mirror_sql = sql  # type: ignore[attr-defined]
    container.mask_rules = mask
    container.row_filters._sql = sql  # type: ignore[attr-defined]
    container.row_filters._governed_tags = MaskRuleTags()  # type: ignore[attr-defined]
    # seed mask rules + a state dimension tag
    actor = _admin()
    mask.seed_defaults(actor)  # type: ignore[arg-type]
    tags = container.row_filters._governed_tags  # type: ignore[attr-defined]
    tags.tags["row_filter"] = tags.tags.get("mask_rule").model_copy(  # type: ignore[union-attr]
        update={"tag_key": "row_filter", "values": ["state"], "value_count": 1}
    )
    container.identity_attributes.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            description="US state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY", "*"],
        ),
        actor,  # type: ignore[arg-type]
    )
    service = AccessExplorerService(
        container.config,
        sql,
        container.identity_attributes,
        mask,
        container.row_filters,
        container.access_requests,
    )
    container.access_explorer = service
    return service, sql


def test_analyze_suggests_sensitivity_and_row_filter(container) -> None:  # noqa: ANN001
    service, _ = _explorer(container)
    user = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        user_id="2",
        groups=["users"],
        is_admin=False,
    )
    insight = service.analyze_table(
        AnalyzeTableRequest(table_full_name="demo.default.customers"), user
    )
    by_name = {c.column_name: c for c in insight.columns}
    assert by_name["s_name"].access_state.value == "masked"
    assert by_name["s_name"].mask_handler == "full"
    assert by_name["s_open"].mask_handler == "joinable"
    assert by_name["id"].access_state.value == "not_governed"
    assert insight.row_filter.attached is True
    assert insight.row_filter.user_can_see_rows is False
    kinds = {s.kind.value for s in insight.suggestions}
    assert "sensitivity" in kinds
    assert "row_filter" in kinds
    sens = next(s for s in insight.suggestions if s.kind.value == "sensitivity")
    assert sens.requestable is True
    assert sens.attribute_value == "finance.budget.PII.Name"
    rf = next(s for s in insight.suggestions if s.kind.value == "row_filter")
    assert rf.attribute_key == "state"
    assert rf.attribute_value == "*"
    assert rf.requestable is True  # state definition seeded in _explorer



def test_analyze_unmasks_when_user_has_prefix(container) -> None:  # noqa: ANN001
    service, _ = _explorer(container)
    actor = _admin()
    container.identity_attributes.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="Sensitivity",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["finance.budget", "finance.budget.PII.Name"],
        ),
        actor,  # type: ignore[arg-type]
    )
    container.identity_attributes.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="finance.budget",
        ),
        actor,  # type: ignore[arg-type]
        source=AttributeSource.MANUAL,
    )
    user = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        user_id="2",
        groups=["users"],
        is_admin=False,
    )
    insight = service.analyze_table(
        AnalyzeTableRequest(table_full_name="demo.default.customers"), user
    )
    assert all(
        c.user_has_access
        for c in insight.columns
        if c.column_name in {"s_name", "s_open"}
    )
    assert not any(s.kind.value == "sensitivity" for s in insight.suggestions)


def test_search_keeps_underscores_literal(container) -> None:  # noqa: ANN001
    service, sql = _explorer(container)
    seen: list[str] = []
    original = sql.query

    def capture(statement: str, parameters=None):  # noqa: ANN001
        seen.append(statement)
        return original(statement, parameters)

    sql.query = capture  # type: ignore[method-assign]
    service.search_tables("abac_type")
    assert any("'abac_type'" in s and "instr(" in s for s in seen)


def test_api_search_and_analyze(client: TestClient, container) -> None:  # noqa: ANN001
    _explorer(container)
    headers = headers_for("analyst@example.com")
    search = client.get("/api/access-explorer/search", params={"q": "abac"}, headers=headers)
    assert search.status_code == 200
    assert search.json()["total"] >= 1

    analyzed = client.post(
        "/api/access-explorer/analyze",
        headers=headers,
        json={"tableFullName": "demo.default.customers"},
    )
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["tableFullName"] == "demo.default.customers"
    assert body["summary"]
    assert len(body["columns"]) == 3


class TreeSql(FakeSql):
    def __init__(self) -> None:
        super().__init__()
        self.statements: list[str] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if "information_schema.catalogs" in statement:
            return [{"catalog_name": "demo"}, {"catalog_name": "sandbox"}]
        if "information_schema.schemata" in statement:
            return [{"schema_name": "default"}]
        if "information_schema.tables" in statement and "table_name, table_type" in statement:
            return [{"table_name": "customers", "table_type": "MANAGED"}]
        return super().query(statement, parameters)


def test_tree_levels_and_identifier_escaping(container) -> None:  # noqa: ANN001
    service, _ = _explorer(container)
    sql = TreeSql()
    service._sql = sql  # type: ignore[attr-defined]

    catalogs = service.list_catalogs()
    assert [c.catalog_name for c in catalogs.catalogs] == ["demo", "sandbox"]
    assert catalogs.truncated is False

    schemas = service.list_schemas("demo")
    assert schemas.schemas[0].schema_name == "default"

    tables = service.list_tables("demo", "default")
    assert tables.tables[0].table_name == "customers"
    assert tables.tables[0].table_type == "MANAGED"

    with pytest.raises(InvalidRequestError):
        service.list_schemas("   ")
    # quotes in identifiers are escaped (string_literal uses backslashes), not injected
    service.list_tables("demo", "o'reilly")
    assert any("o\\'reilly" in s for s in sql.statements)


def test_search_does_not_filter_by_bogus_table_type(container) -> None:  # noqa: ANN001
    service, _ = _explorer(container)
    sql = TreeSql()
    service._sql = sql  # type: ignore[attr-defined]
    service.search_tables("customers")
    search_sql = next(s for s in sql.statements if "instr(" in s)
    assert "BASE TABLE" not in search_sql


def test_api_tree_routes(client: TestClient, container) -> None:  # noqa: ANN001
    service, _ = _explorer(container)
    service._sql = TreeSql()  # type: ignore[attr-defined]
    headers = headers_for("analyst@example.com")
    cats = client.get("/api/access-explorer/tree/catalogs", headers=headers)
    assert cats.status_code == 200
    assert cats.json()["total"] == 2
    schemas = client.get(
        "/api/access-explorer/tree/schemas", params={"catalog": "demo"}, headers=headers
    )
    assert schemas.status_code == 200
    tables = client.get(
        "/api/access-explorer/tree/tables",
        params={"catalog": "demo", "schema": "default"},
        headers=headers,
    )
    assert tables.status_code == 200
    assert tables.json()["tables"][0]["tableType"] == "MANAGED"


def test_hierarchy_and_admin_routing(container) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from backend.app.models.access_explorer import RequestRouteKind
    from backend.app.models.access_request import CategoryApprover

    service, sql = _explorer(container)
    # Two columns with related paths → leaf suggestions + parent
    sql.tags = [
        {"column_name": "a", "tag_name": "sensitivity_category", "tag_value": "finance.budget"},
        {"column_name": "a", "tag_name": "sensitivity", "tag_value": "PII.Name"},
        {"column_name": "a", "tag_name": "data_type", "tag_value": "string"},
        {"column_name": "a", "tag_name": "mask_rule", "tag_value": "full"},
        {"column_name": "b", "tag_name": "sensitivity_category", "tag_value": "finance.budget"},
        {"column_name": "b", "tag_name": "sensitivity", "tag_value": "PII.SSN"},
        {"column_name": "b", "tag_name": "data_type", "tag_value": "string"},
        {"column_name": "b", "tag_name": "mask_rule", "tag_value": "full"},
        {"column_name": "c", "tag_name": "sensitivity_category", "tag_value": "public"},
        {"column_name": "c", "tag_name": "sensitivity", "tag_value": "PII.Name"},
        {"column_name": "c", "tag_name": "data_type", "tag_value": "string"},
        {"column_name": "c", "tag_name": "mask_rule", "tag_value": "full"},
    ]
    sql.columns = [
        {"column_name": "a", "full_data_type": "string"},
        {"column_name": "b", "full_data_type": "string"},
        {"column_name": "c", "full_data_type": "string"},
    ]
    actor = _admin()
    container.identity_attributes.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="Sensitivity",
            value_source=ValueSource.FREE_FORM,
            allowed_values=[
                "finance.budget",
                "finance.budget.PII.Name",
                "finance.budget.PII.SSN",
                "public.PII.Name",
            ],
        ),
        actor,  # type: ignore[arg-type]
    )
    # Seed approvers directly (avoids live governed-tag lookup in upsert_approver).
    container.storage.category_approvers.upsert_approver(
        CategoryApprover(
            approver_id="apr-1",
            category_path="finance.budget",
            principal_name="budget-owners",
            principal_type=PrincipalKind.GROUP,
            created_by=actor.user_name,
            created_at=datetime.now(UTC),
        )
    )
    user = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        user_id="2",
        groups=["users"],
        is_admin=False,
    )
    insight = service.analyze_table(
        AnalyzeTableRequest(table_full_name="demo.default.customers"), user
    )
    sens = [s for s in insight.suggestions if s.kind.value == "sensitivity"]
    values = {s.attribute_value for s in sens}
    assert "finance.budget.PII.Name" in values
    assert "finance.budget.PII.SSN" in values
    assert "public.PII.Name" in values
    parents = [s for s in sens if s.is_hierarchy_parent]
    assert any(s.attribute_value == "finance.budget" for s in parents)
    budget_leaf = next(s for s in sens if s.attribute_value == "finance.budget.PII.Name")
    assert budget_leaf.routed_to == RequestRouteKind.CATEGORY_APPROVERS
    assert budget_leaf.approvers[0].principal_name == "budget-owners"
    public_leaf = next(s for s in sens if s.attribute_value == "public.PII.Name")
    assert public_leaf.routed_to == RequestRouteKind.APP_ADMINS


def test_row_filter_only_table_suggests_dimension(container) -> None:  # noqa: ANN001
    service, sql = _explorer(container)
    sql.columns = [
        {"column_name": "abbreviation", "full_data_type": "string"},
        {"column_name": "name", "full_data_type": "string"},
    ]
    sql.tags = [
        {"column_name": "abbreviation", "tag_name": "row_filter", "tag_value": "state"},
    ]
    user = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        user_id="2",
        groups=["users"],
        is_admin=False,
    )
    insight = service.analyze_table(
        AnalyzeTableRequest(table_full_name="demo.default.us_states"), user
    )
    assert insight.row_filter.attached is True
    assert insight.row_filter.dimension_key == "state"
    assert insight.row_filter.user_can_see_rows is False
    rf = [s for s in insight.suggestions if s.kind.value == "row_filter"]
    assert rf, "expected row-filter suggestions on a filter-only table"
    assert any(s.attribute_value == "*" and s.requestable for s in rf)

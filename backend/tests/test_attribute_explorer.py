from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.row_filter import RowFilterAttachment, RowFilterAttachmentList
from backend.app.services.data_access_planner import TagRow
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin, _service


class _FakeSql:
    configured = True

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        assert "column_tags" in statement
        return self.rows


class _FakeRowFilters:
    def __init__(self, attachments: list[RowFilterAttachment] | None = None) -> None:
        self._attachments = attachments or []

    def list_attachments(self) -> RowFilterAttachmentList:
        return RowFilterAttachmentList(attachments=self._attachments, total=len(self._attachments))


def _seed_data_access(container) -> None:  # noqa: ANN001
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="DAC",
            governed_tag_keys=["access_path"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="ana@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="data_access",
            attribute_value="finance",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="budget@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="data_access",
            attribute_value="finance.budget",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    container.data_access._tag_loader = lambda _settings: [  # noqa: SLF001
        TagRow("table", "sandbox", "finance", "budget", "access_path", "finance.budget"),
        TagRow("table", "sandbox", "finance", "budget", "data_governance_status", "ready"),
        TagRow("table", "sandbox", "finance", "forecast", "access_path", "finance"),
        TagRow("table", "sandbox", "finance", "forecast", "data_governance_status", "ready"),
    ]


def test_attribute_explorer_data_access_holders_and_tables(client: TestClient, container) -> None:
    _seed_data_access(container)
    admin = headers_for("admin@example.com")
    body = client.post(
        "/api/attribute-explorer/analyze",
        headers=admin,
        json={"attributeKey": "data_access", "attributeValue": "finance"},
    )
    assert body.status_code == 200
    payload = body.json()
    assert payload["kind"] == "data_access"
    assert payload["groupName"] == "dac_finance"
    holders = {h["principalName"]: h["match"] for h in payload["holders"]}
    assert holders["ana@example.com"] == "exact"
    assert holders["budget@example.com"] == "descendant"
    tables = {t["fullName"]: t["match"] for t in payload["tables"]}
    assert tables["sandbox.finance.forecast"] == "exact"
    assert tables["sandbox.finance.budget"] == "descendant"


def test_attribute_explorer_forbidden_for_non_approver(client: TestClient, container) -> None:
    _seed_data_access(container)
    user = headers_for("analyst@example.com")
    denied = client.post(
        "/api/attribute-explorer/analyze",
        headers=user,
        json={"attributeKey": "data_access", "attributeValue": "finance"},
    )
    assert denied.status_code == 403


def _seed_row_filter(container) -> None:  # noqa: ANN001
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            description="US state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY", "TX"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="ana@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="state",
            attribute_value="CA",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="ana@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="state",
            attribute_value="NY",
            expires_at=datetime.now(UTC) + timedelta(days=10),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="finance-analysts",
            principal_type=PrincipalKind.GROUP,
            attribute_key="state",
            attribute_value="TX",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )


def test_attribute_explorer_row_filter_principals_overview(client: TestClient, container) -> None:
    _seed_row_filter(container)
    container.attribute_explorer._row_filters = _FakeRowFilters(  # noqa: SLF001
        [
            RowFilterAttachment(
                table_full_name="sandbox.default.orders",
                column_name="state_code",
                dimension="state",
            ),
            RowFilterAttachment(
                table_full_name="sandbox.default.other",
                column_name="region",
                dimension="org",
            ),
        ]
    )
    admin = headers_for("admin@example.com")
    body = client.post(
        "/api/attribute-explorer/analyze",
        headers=admin,
        json={"attributeKey": "state", "attributeValue": ""},
    )
    assert body.status_code == 200
    payload = body.json()
    assert payload["kind"] == "row_filter"
    assert payload["attributeValue"] == "*"
    assert payload["holders"] == []
    by_name = {row["principalName"]: row for row in payload["principals"]}
    assert by_name["ana@example.com"]["values"] == ["CA", "NY"]
    assert by_name["finance-analysts"]["values"] == ["TX"]
    assert by_name["ana@example.com"]["principalType"] == "USER"
    assert by_name["finance-analysts"]["principalType"] == "GROUP"
    tables = payload["tables"]
    assert len(tables) == 1
    assert tables[0]["fullName"] == "sandbox.default.orders"
    assert tables[0]["columnName"] == "state_code"
    assert tables[0]["tagValue"] == "state"
    assert tables[0]["match"] == "exact"


def test_attribute_explorer_sensitivity_lists_matching_columns(client: TestClient, container) -> None:
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(key="data_access", governed_tag_keys=["access_path"]),
        actor,
    )
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="Sensitivity",
            governed_tag_keys=["access_path", "sensitivity"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="ana@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="public.PII.Name",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    container.attribute_explorer._sql = _FakeSql(  # noqa: SLF001
        [
            {
                "catalog_name": "sandbox",
                "schema_name": "default",
                "table_name": "customers",
                "column_name": "email",
                "tag_name": "access_path",
                "tag_value": "public",
            },
            {
                "catalog_name": "sandbox",
                "schema_name": "default",
                "table_name": "customers",
                "column_name": "email",
                "tag_name": "sensitivity",
                "tag_value": "PII.Name",
            },
            {
                "catalog_name": "sandbox",
                "schema_name": "default",
                "table_name": "customers",
                "column_name": "phone",
                "tag_name": "access_path",
                "tag_value": "public",
            },
            {
                "catalog_name": "sandbox",
                "schema_name": "default",
                "table_name": "customers",
                "column_name": "phone",
                "tag_name": "sensitivity",
                "tag_value": "PII.Phone",
            },
            {
                "catalog_name": "sandbox",
                "schema_name": "hr",
                "table_name": "employees",
                "column_name": "ssn",
                "tag_name": "access_path",
                "tag_value": "hr",
            },
            {
                "catalog_name": "sandbox",
                "schema_name": "hr",
                "table_name": "employees",
                "column_name": "ssn",
                "tag_name": "sensitivity",
                "tag_value": "PII.SSN",
            },
        ]
    )
    admin = headers_for("admin@example.com")
    body = client.post(
        "/api/attribute-explorer/analyze",
        headers=admin,
        json={"attributeKey": "sensitivity", "attributeValue": "public"},
    )
    assert body.status_code == 200
    payload = body.json()
    assert payload["kind"] == "sensitivity"
    holders = {h["principalName"]: h["match"] for h in payload["holders"]}
    assert holders["ana@example.com"] == "descendant"
    by_column = {t["columnName"]: t for t in payload["tables"]}
    assert set(by_column) == {"email", "phone"}
    assert by_column["email"]["fullName"] == "sandbox.default.customers"
    assert by_column["email"]["tagValue"] == "public.PII.Name"
    assert by_column["email"]["match"] == "descendant"
    assert by_column["phone"]["tagValue"] == "public.PII.Phone"
    assert by_column["phone"]["match"] == "descendant"

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models.attribute_explorer import AttributeHolderMatch, UnlockedTable
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin, _service


class _FakeExplorerResources:
    """Stub resource lookups on the real AttributeExplorerService."""

    def __init__(self, tables: list[UnlockedTable] | None = None) -> None:
        self.tables = tables or []
        self.calls: list[tuple[str, str, str]] = []

    def resources_for(self, kind, key: str, value: str) -> list[UnlockedTable]:  # noqa: ANN001
        self.calls.append((kind.value if hasattr(kind, "value") else str(kind), key, value))
        return self.tables


def test_my_access_snapshot_includes_groups_and_inherited(client: TestClient, container) -> None:
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            description="US state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY"],
        ),
        actor,
    )
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            governed_tag_keys=["sensitivity_category", "sensitivity"],
        ),
        actor,
    )
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="DAC",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["public", "finance"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admin@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="finance",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admins",
            principal_type=PrincipalKind.GROUP,
            attribute_key="state",
            attribute_value="CA",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admin@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="data_access",
            attribute_value="public",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admins",
            principal_type=PrincipalKind.GROUP,
            attribute_key="data_access",
            attribute_value="public",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )

    body = client.get("/api/identity/my-access", headers=headers_for("admin@example.com"))
    assert body.status_code == 200
    payload = body.json()
    assert payload["groupCount"] >= 1
    assert "admins" in {g["name"] for g in payload["groups"]}
    keys = {(a["attributeKey"], a["attributeValue"], a["principalType"]) for a in payload["attributes"]}
    assert ("sensitivity", "finance", "USER") in keys
    assert ("state", "CA", "GROUP") in keys
    assert ("data_access", "public", "USER") in keys
    assert ("data_access", "public", "GROUP") in keys
    assert payload["directAttributeCount"] >= 1
    assert payload["inheritedAttributeCount"] >= 1
    state_kind = next(a["kind"] for a in payload["attributes"] if a["attributeKey"] == "state")
    assert state_kind == "row_filter"


def test_my_access_resources_requires_held_attribute(client: TestClient, container) -> None:
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            governed_tag_keys=["sensitivity_category", "sensitivity"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admin@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="public.PII.Name",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )

    fake = _FakeExplorerResources(
        [
            UnlockedTable(
                full_name="sandbox.default.customers",
                column_name="email",
                tag_value="public.PII.Name",
                match=AttributeHolderMatch.EXACT,
            )
        ]
    )
    container.my_access._explorer.resources_for = fake.resources_for  # type: ignore[method-assign]

    denied = client.post(
        "/api/identity/my-access/resources",
        headers=headers_for("admin@example.com"),
        json={"attributeKey": "sensitivity", "attributeValue": "hr.secret"},
    )
    assert denied.status_code == 403

    ok = client.post(
        "/api/identity/my-access/resources",
        headers=headers_for("admin@example.com"),
        json={"attributeKey": "sensitivity", "attributeValue": "public.PII.Name"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["kind"] == "sensitivity"
    assert payload["tables"][0]["fullName"] == "sandbox.default.customers"
    assert payload["tables"][0]["columnName"] == "email"
    assert fake.calls == [("sensitivity", "sensitivity", "public.PII.Name")]


def test_my_access_resources_row_filter_by_dimension(client: TestClient, container) -> None:
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY"],
        ),
        actor,
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="admin@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="state",
            attribute_value="CA",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        actor,
    )
    fake = _FakeExplorerResources(
        [
            UnlockedTable(
                full_name="sandbox.default.orders",
                column_name="state_code",
                tag_value="state",
                match=AttributeHolderMatch.EXACT,
            )
        ]
    )
    container.my_access._explorer.resources_for = fake.resources_for  # type: ignore[method-assign]

    ok = client.post(
        "/api/identity/my-access/resources",
        headers=headers_for("admin@example.com"),
        json={"attributeKey": "state", "attributeValue": "CA"},
    )
    assert ok.status_code == 200
    assert ok.json()["kind"] == "row_filter"
    assert ok.json()["tables"][0]["columnName"] == "state_code"
    assert fake.calls[0][0] == "row_filter"
    assert fake.calls[0][1] == "state"

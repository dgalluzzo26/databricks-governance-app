from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.services.data_access_planner import TagRow
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin, _service


def _seed_data_access(container) -> None:  # noqa: ANN001
    identity = _service(container)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="DAC",
            governed_tag_keys=["data_access_control"],
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
        TagRow("table", "sandbox", "finance", "budget", "data_access_control", "finance.budget"),
        TagRow("table", "sandbox", "finance", "budget", "clr_review_status", "reviewed"),
        TagRow("table", "sandbox", "finance", "forecast", "data_access_control", "finance"),
        TagRow("table", "sandbox", "finance", "forecast", "clr_review_status", "reviewed"),
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

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import InvalidRequestError
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeDefinition,
    GovernedTagList,
    GovernedTagSummary,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.tests.conftest import headers_for


class FakeGovernedTags:
    def __init__(self) -> None:
        self.tags = {
            "dga_domain": GovernedTagSummary(
                tag_key="dga_domain",
                description="Business domain",
                values=["finance", "finance.budget", "hr"],
                value_count=3,
            ),
            "sub_domain": GovernedTagSummary(
                tag_key="sub_domain",
                description="Subdomain hierarchy",
                values=["finance", "finance.budget", "finance.contracts"],
                value_count=3,
            ),
            "access_path": GovernedTagSummary(
                tag_key="access_path",
                description="Business category hierarchy",
                values=["public", "finance", "finance.budget"],
                value_count=3,
            ),
            "sensitivity": GovernedTagSummary(
                tag_key="sensitivity",
                description="Shared PII hierarchy",
                values=["PII", "PII.Name", "PII.SSN"],
                value_count=3,
            ),
        }

    def list_tags(self, *, q: str | None = None, limit: int = 50) -> GovernedTagList:
        items = list(self.tags.values())
        if q:
            needle = q.lower()
            items = [t for t in items if needle in t.tag_key.lower()]
        return GovernedTagList(tags=items[:limit], total=len(items), truncated=False)

    def get_tag(self, tag_key: str) -> GovernedTagSummary:
        if tag_key not in self.tags:
            raise InvalidRequestError(f"Governed tag '{tag_key}' was not found")
        return self.tags[tag_key]

    def values_for_keys(self, tag_keys: list[str]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for key in tag_keys:
            for value in self.get_tag(key).values:
                if value in seen:
                    continue
                seen.add(value)
                values.append(value)
        return values

    def require_tags(self, tag_keys: list[str]) -> list[GovernedTagSummary]:
        return [self.get_tag(key) for key in tag_keys]


def _admin() -> object:
    return type("U", (), {"user_name": "admin@example.com", "is_admin": True, "groups": ["admins"]})()


def _service(container) -> IdentityAttributeService:  # noqa: ANN001
    service: IdentityAttributeService = container.identity_attributes
    service._governed_tags = FakeGovernedTags()  # type: ignore[method-assign]
    return service


def test_definition_bound_to_governed_tag(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()

    definition = service.upsert_definition(
        UpsertAttributeDefinitionRequest(governed_tag_keys=["dga_domain"]),
        actor,  # type: ignore[arg-type]
    )
    assert definition.key == "dga_domain"
    assert definition.governed_tag_keys == ["dga_domain"]
    assert definition.allowed_values == ["finance", "finance.budget", "hr"]

    assignment = service.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="dga_domain",
            attribute_value="finance.budget",
            justification="Quarterly close",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
        actor,  # type: ignore[arg-type]
    )
    assert assignment.active is True
    assert assignment.attribute_value == "finance.budget"

    revoked = service.revoke(assignment.assignment_id, actor)  # type: ignore[arg-type]
    assert revoked.active is False


def test_rejects_unknown_tag_or_disallowed_values(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()

    with pytest.raises(InvalidRequestError, match="was not found"):
        service.upsert_definition(
            UpsertAttributeDefinitionRequest(governed_tag_keys=["missing_tag"]),
            actor,  # type: ignore[arg-type]
        )

    service.upsert_definition(
        UpsertAttributeDefinitionRequest(key="domain", governed_tag_keys=["dga_domain"]),
        actor,  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidRequestError, match="not allowed"):
        service.assign(
            AssignAttributeRequest(
                principal_name="analyst@example.com",
                principal_type=PrincipalKind.USER,
                attribute_key="domain",
                attribute_value="hr.payroll",
            ),
            actor,  # type: ignore[arg-type]
        )


def test_rejects_assign_to_managed_data_access_group(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(key="domain", governed_tag_keys=["dga_domain"]),
        actor,  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidRequestError, match="managed Data Access group"):
        service.assign(
            AssignAttributeRequest(
                principal_name="dac_finance",
                principal_type=PrincipalKind.GROUP,
                attribute_key="domain",
                attribute_value="finance",
            ),
            actor,  # type: ignore[arg-type]
        )


def test_sensitivity_composes_category_and_leaf(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()

    definition = service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="used to control access to masking",
            governed_tag_keys=["access_path", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    assert definition.governed_tag_keys == ["access_path", "sensitivity"]
    assert "public" in definition.allowed_values
    assert "finance.budget" in definition.allowed_values
    assert "public.PII.Name" in definition.allowed_values
    assert "finance.budget.PII.SSN" in definition.allowed_values
    # Leaf-only values are not assignable without a category prefix.
    assert "PII.Name" not in definition.allowed_values

    assignment = service.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="finance.budget.PII.Name",
        ),
        actor,  # type: ignore[arg-type]
    )
    assert assignment.attribute_value == "finance.budget.PII.Name"

    with pytest.raises(InvalidRequestError, match="not allowed"):
        service.assign(
            AssignAttributeRequest(
                principal_name="analyst@example.com",
                principal_type=PrincipalKind.USER,
                attribute_key="sensitivity",
                attribute_value="PII.Name",
            ),
            actor,  # type: ignore[arg-type]
        )


def test_api_identity_attribute_flow(client: TestClient, container) -> None:  # noqa: ANN001
    _service(container)
    admin = headers_for("admin@example.com")

    # Bypass live UC in the HTTP path by seeding storage + enriching via fake tags.
    container.storage.identity_attributes.upsert_definition(
        AttributeDefinition(
            key="dga_domain",
            description="Business domain",
            governed_tag_keys=["dga_domain"],
            allowed_values=[],
            created_by="admin@example.com",
            created_at=datetime.now(UTC),
            updated_by="admin@example.com",
            updated_at=datetime.now(UTC),
        )
    )

    definitions = client.get("/api/identity/definitions", headers=admin).json()
    assert definitions["total"] == 1
    assert definitions["definitions"][0]["allowedValues"] == ["finance", "finance.budget", "hr"]
    assert definitions["definitions"][0]["governedTagKeys"] == ["dga_domain"]

    assigned = client.post(
        "/api/identity/assignments",
        headers=admin,
        json={
            "principalName": "admin@example.com",
            "principalType": "USER",
            "attributeKey": "dga_domain",
            "attributeValue": "finance",
        },
    )
    assert assigned.status_code == 200
    assignment_id = assigned.json()["assignmentId"]

    mine = client.get("/api/identity/assignments/me", headers=admin).json()
    assert mine["total"] == 1

    revoked = client.post(f"/api/identity/assignments/{assignment_id}/revoke", headers=admin)
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False

    forbidden = client.put(
        "/api/identity/definitions",
        headers=headers_for("analyst@example.com"),
        json={"governedTagKeys": ["dga_domain"]},
    )
    assert forbidden.status_code == 403


def test_rejects_second_data_access_definition(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()
    first = service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            governed_tag_keys=["access_path"],
        ),
        actor,  # type: ignore[arg-type]
    )
    assert first.key == "data_access"
    assert service.find_data_access_definition() is not None

    with pytest.raises(InvalidRequestError, match="already exists"):
        service.upsert_definition(
            UpsertAttributeDefinitionRequest(
                key="data_access_other",
                governed_tag_keys=["access_path"],
            ),
            actor,  # type: ignore[arg-type]
        )

    # Editing the existing definition is allowed.
    updated = service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="updated",
            governed_tag_keys=["access_path"],
        ),
        actor,  # type: ignore[arg-type]
    )
    assert updated.description == "updated"


def test_data_access_definition_must_keep_access_tag(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            governed_tag_keys=["access_path"],
        ),
        actor,  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidRequestError, match="must keep governed tag"):
        service.upsert_definition(
            UpsertAttributeDefinitionRequest(
                key="data_access",
                governed_tag_keys=["dga_domain"],
            ),
            actor,  # type: ignore[arg-type]
        )


def test_find_sensitivity_definition(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()
    assert service.find_sensitivity_definition() is None
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            governed_tag_keys=["access_path", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    found = service.find_sensitivity_definition()
    assert found is not None
    assert found.key == "sensitivity"


def test_approver_can_revoke_assignment_for_their_path(client: TestClient, container) -> None:  # noqa: ANN001
    """Path approvers may revoke; unrelated users may not."""
    from backend.app.models.access_request import UpsertCategoryApproverRequest
    from backend.app.services.access_requests import AccessRequestService
    from backend.tests.conftest import FakeWorkspaceFactory

    fake = FakeWorkspaceFactory(
        {
            "admin@example.com": ["admins", "users"],
            "analyst@example.com": ["users"],
            "finance.lead@example.com": ["finance-owners", "users"],
            "outsider@example.com": ["users"],
        },
        admin_configured=True,
    )
    container.workspace = fake
    container.identity._factory = fake
    container.request_context._factory = fake

    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            governed_tag_keys=["access_path", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="finance",
            principal_name="finance-owners",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,  # type: ignore[arg-type]
    )
    assigned = identity.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="finance.budget.PII.Name",
        ),
        actor,  # type: ignore[arg-type]
    )

    denied = client.post(
        f"/api/identity/assignments/{assigned.assignment_id}/revoke",
        headers=headers_for("outsider@example.com"),
    )
    assert denied.status_code == 403

    revoked = client.post(
        f"/api/identity/assignments/{assigned.assignment_id}/revoke",
        headers=headers_for("finance.lead@example.com"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False


def _seed_sensitivity_and_finance_approver(container) -> None:  # noqa: ANN001
    """Definition + finance-owners group approver, with workspace users for HTTP auth."""
    from backend.app.models.access_request import UpsertCategoryApproverRequest
    from backend.app.services.access_requests import AccessRequestService
    from backend.tests.conftest import FakeWorkspaceFactory

    fake = FakeWorkspaceFactory(
        {
            "admin@example.com": ["admins", "users"],
            "analyst@example.com": ["users"],
            "finance.lead@example.com": ["finance-owners", "users"],
            "outsider@example.com": ["users"],
            "sam@example.com": ["users"],
        },
        admin_configured=True,
    )
    container.workspace = fake
    container.identity._factory = fake
    container.request_context._factory = fake

    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            governed_tag_keys=["access_path", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="finance",
            principal_name="finance-owners",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,  # type: ignore[arg-type]
    )


def test_admin_bulk_assign_creates_cartesian_product(client: TestClient, container) -> None:  # noqa: ANN001
    """Admin bulk assign creates one assignment per principal × attribute with shared fields."""
    _service(container)
    admin = headers_for("admin@example.com")
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    container.storage.identity_attributes.upsert_definition(
        AttributeDefinition(
            key="dga_domain",
            description="Business domain",
            governed_tag_keys=["dga_domain"],
            allowed_values=[],
            created_by="admin@example.com",
            created_at=datetime.now(UTC),
            updated_by="admin@example.com",
            updated_at=datetime.now(UTC),
        )
    )

    response = client.post(
        "/api/identity/assignments/bulk",
        headers=admin,
        json={
            "principals": [
                {"principalName": "analyst@example.com", "principalType": "USER"},
                {"principalName": "data-stewards", "principalType": "GROUP"},
            ],
            "attributes": [
                {"attributeKey": "dga_domain", "attributeValue": "finance"},
                {"attributeKey": "dga_domain", "attributeValue": "hr"},
            ],
            "justification": "Bulk onboard",
            "expiresAt": expires,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalRequested"] == 4
    assert len(body["succeeded"]) == 4
    assert body["skipped"] == []
    assert body["failed"] == []
    for item in body["succeeded"]:
        assert item["justification"] == "Bulk onboard"
        assert item["expiresAt"] is not None
        assert item["active"] is True

    listed = client.get("/api/identity/assignments", headers=admin).json()
    assert listed["total"] >= 4


def test_bulk_assign_skips_already_held_and_reports_partial_failure(client: TestClient, container) -> None:  # noqa: ANN001
    """Idempotent re-grants are skipped; invalid values fail without rolling back successes."""
    _service(container)
    admin = headers_for("admin@example.com")
    container.storage.identity_attributes.upsert_definition(
        AttributeDefinition(
            key="dga_domain",
            description="Business domain",
            governed_tag_keys=["dga_domain"],
            allowed_values=[],
            created_by="admin@example.com",
            created_at=datetime.now(UTC),
            updated_by="admin@example.com",
            updated_at=datetime.now(UTC),
        )
    )
    first = client.post(
        "/api/identity/assignments/bulk",
        headers=admin,
        json={
            "principals": [{"principalName": "analyst@example.com", "principalType": "USER"}],
            "attributes": [{"attributeKey": "dga_domain", "attributeValue": "finance"}],
        },
    )
    assert first.status_code == 200
    assert len(first.json()["succeeded"]) == 1

    second = client.post(
        "/api/identity/assignments/bulk",
        headers=admin,
        json={
            "principals": [{"principalName": "analyst@example.com", "principalType": "USER"}],
            "attributes": [
                {"attributeKey": "dga_domain", "attributeValue": "finance"},
                {"attributeKey": "dga_domain", "attributeValue": "not-a-real-domain"},
            ],
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["totalRequested"] == 2
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["reason"] == "Already held"
    assert len(body["failed"]) == 1
    assert "not-a-real-domain" in body["failed"][0]["attributeValue"]
    assert body["succeeded"] == []


def test_bulk_assign_enforces_caps(client: TestClient, container) -> None:  # noqa: ANN001
    """Server rejects bulk submits that exceed principal / grant caps."""
    _service(container)
    admin = headers_for("admin@example.com")
    container.storage.identity_attributes.upsert_definition(
        AttributeDefinition(
            key="dga_domain",
            description="Business domain",
            governed_tag_keys=["dga_domain"],
            allowed_values=[],
            created_by="admin@example.com",
            created_at=datetime.now(UTC),
            updated_by="admin@example.com",
            updated_at=datetime.now(UTC),
        )
    )
    too_many_principals = client.post(
        "/api/identity/assignments/bulk",
        headers=admin,
        json={
            "principals": [
                {"principalName": f"user{i}@example.com", "principalType": "USER"} for i in range(51)
            ],
            "attributes": [{"attributeKey": "dga_domain", "attributeValue": "finance"}],
        },
    )
    assert too_many_principals.status_code == 422

    too_many_grants = client.post(
        "/api/identity/assignments/bulk",
        headers=admin,
        json={
            "principals": [
                {"principalName": f"user{i}@example.com", "principalType": "USER"} for i in range(50)
            ],
            "attributes": [{"attributeKey": "dga_domain", "attributeValue": "finance"} for _ in range(11)],
        },
    )
    assert too_many_grants.status_code == 422
    assert "500" in too_many_grants.json()["error"]["message"]


def test_approver_bulk_and_list_are_scoped(client: TestClient, container) -> None:  # noqa: ANN001
    """Approvers may bulk-grant and list only paths they can decide."""
    _seed_sensitivity_and_finance_approver(container)
    admin = headers_for("admin@example.com")
    lead = headers_for("finance.lead@example.com")
    outsider = headers_for("outsider@example.com")

    grantable = client.get("/api/identity/grantable-attributes", headers=lead)
    assert grantable.status_code == 200
    keys = {item["key"] for item in grantable.json()["attributes"]}
    assert keys == {"sensitivity"}

    assert client.get("/api/identity/grantable-attributes", headers=outsider).status_code == 403
    assert client.get("/api/identity/assignments", headers=outsider).status_code == 403

    ok = client.post(
        "/api/identity/assignments/bulk",
        headers=lead,
        json={
            "principals": [
                {"principalName": "analyst@example.com", "principalType": "USER"},
                {"principalName": "sam@example.com", "principalType": "USER"},
            ],
            "attributes": [
                {"attributeKey": "sensitivity", "attributeValue": "finance.budget.PII.Name"},
            ],
            "justification": "Finance close",
        },
    )
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["succeeded"]) == 2

    forbidden = client.post(
        "/api/identity/assignments/bulk",
        headers=lead,
        json={
            "principals": [{"principalName": "analyst@example.com", "principalType": "USER"}],
            "attributes": [
                {"attributeKey": "sensitivity", "attributeValue": "public.PII.Name"},
            ],
        },
    )
    assert forbidden.status_code == 403

    # Seed an out-of-scope assignment as admin; approver list must not include it.
    seeded = client.post(
        "/api/identity/assignments",
        headers=admin,
        json={
            "principalName": "outsider@example.com",
            "principalType": "USER",
            "attributeKey": "sensitivity",
            "attributeValue": "public.PII.Name",
        },
    )
    assert seeded.status_code == 200, seeded.text

    lead_list = client.get("/api/identity/assignments", headers=lead).json()
    values = {item["attributeValue"] for item in lead_list["assignments"]}
    assert "finance.budget.PII.Name" in values
    assert "public.PII.Name" not in values

    admin_list = client.get("/api/identity/assignments", headers=admin).json()
    admin_values = {item["attributeValue"] for item in admin_list["assignments"]}
    assert "public.PII.Name" in admin_values
    assert "finance.budget.PII.Name" in admin_values

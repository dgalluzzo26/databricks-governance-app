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
            "sensitivity_category": GovernedTagSummary(
                tag_key="sensitivity_category",
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
            "data_access_control": GovernedTagSummary(
                tag_key="data_access_control",
                description="Tagged-level data access",
                values=["finance", "finance.budget"],
                value_count=2,
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


def test_sensitivity_composes_category_and_leaf(container) -> None:  # noqa: ANN001
    service = _service(container)
    actor = _admin()

    definition = service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="used to control access to masking",
            governed_tag_keys=["sensitivity_category", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    assert definition.governed_tag_keys == ["sensitivity_category", "sensitivity"]
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
            governed_tag_keys=["data_access_control"],
        ),
        actor,  # type: ignore[arg-type]
    )
    assert first.key == "data_access"
    assert service.find_data_access_definition() is not None

    with pytest.raises(InvalidRequestError, match="already exists"):
        service.upsert_definition(
            UpsertAttributeDefinitionRequest(
                key="data_access_other",
                governed_tag_keys=["data_access_control"],
            ),
            actor,  # type: ignore[arg-type]
        )

    # Editing the existing definition is allowed.
    updated = service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="updated",
            governed_tag_keys=["data_access_control"],
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
            governed_tag_keys=["data_access_control"],
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
            governed_tag_keys=["sensitivity_category", "sensitivity"],
        ),
        actor,  # type: ignore[arg-type]
    )
    found = service.find_sensitivity_definition()
    assert found is not None
    assert found.key == "sensitivity"

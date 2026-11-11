from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import ForbiddenError, InvalidRequestError
from backend.app.models.access_request import (
    CreateAccessRequestBody,
    DecideAccessRequestBody,
    UpsertCategoryApproverRequest,
)
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.services.access_requests import AccessRequestService, resolve_category
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import FakeGovernedTags, _admin, _service


def _access_service(container) -> AccessRequestService:  # noqa: ANN001
    identity = _service(container)
    return AccessRequestService(container.storage, identity)


def _seed_sensitivity(service: IdentityAttributeService, actor) -> None:  # noqa: ANN001
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="sensitivity",
            description="masking",
            governed_tag_keys=["sensitivity_category", "sensitivity"],
        ),
        actor,
    )


def test_resolve_category_most_specific() -> None:
    configured = ["finance", "finance.budget"]
    assert resolve_category("finance.budget.PII.Name", configured) == "finance.budget"
    assert resolve_category("finance.contracts.PII", ["finance"]) == "finance"
    assert resolve_category("hr.payroll", ["finance"]) is None


def test_most_specific_approver_and_renewal_window(container) -> None:  # noqa: ANN001
    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    _seed_sensitivity(identity, actor)

    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="finance",
            principal_name="finance-owners",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,  # type: ignore[arg-type]
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="finance.budget",
            principal_name="budget-owner@example.com",
            principal_type=PrincipalKind.USER,
        ),
        actor,  # type: ignore[arg-type]
    )

    requester = type(
        "U",
        (),
        {
            "user_name": "analyst@example.com",
            "is_admin": False,
            "groups": [],
            "email": "analyst@example.com",
            "display_name": "Analyst",
        },
    )()
    created = access.create_request(
        CreateAccessRequestBody(
            attribute_value="finance.budget.PII.Name",
            requested_expires_at=datetime.now(UTC) + timedelta(days=60),
            justification="Need budget PII",
        ),
        requester,  # type: ignore[arg-type]
    )
    assert created.resolved_category == "finance.budget"

    finance_group_user = type(
        "U",
        (),
        {
            "user_name": "finance.lead@example.com",
            "is_admin": False,
            "groups": ["finance-owners"],
            "email": "finance.lead@example.com",
            "display_name": "Finance Lead",
        },
    )()
    with pytest.raises(ForbiddenError):
        access.approve_request(
            created.request_id,
            DecideAccessRequestBody(expires_at=datetime.now(UTC) + timedelta(days=30)),
            finance_group_user,  # type: ignore[arg-type]
        )

    budget_owner = type(
        "U",
        (),
        {
            "user_name": "budget-owner@example.com",
            "is_admin": False,
            "groups": [],
            "email": "budget-owner@example.com",
            "display_name": "Budget Owner",
        },
    )()
    approved = access.approve_request(
        created.request_id,
        DecideAccessRequestBody(expires_at=datetime.now(UTC) + timedelta(days=45)),
        budget_owner,  # type: ignore[arg-type]
    )
    assert approved.status.value == "APPROVED"

    assignments = identity.list_assignments(
        principal_name="analyst@example.com",
        attribute_key="sensitivity",
    )
    assert assignments.total == 1
    assert assignments.assignments[0].attribute_value == "finance.budget.PII.Name"
    assert assignments.assignments[0].source.value == "ACCESS_REQUEST"

    # Parent-only path still uses finance group when budget is not the match.
    parent_request = access.create_request(
        CreateAccessRequestBody(
            attribute_value="finance.PII",
            requested_expires_at=datetime.now(UTC) + timedelta(days=20),
            justification="Need finance PII",
        ),
        requester,  # type: ignore[arg-type]
    )
    assert parent_request.resolved_category == "finance"
    access.approve_request(
        parent_request.request_id,
        DecideAccessRequestBody(),
        finance_group_user,  # type: ignore[arg-type]
    )


def test_request_rejects_past_expiry_and_disallowed_value(container) -> None:  # noqa: ANN001
    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    _seed_sensitivity(identity, actor)
    requester = type(
        "U",
        (),
        {
            "user_name": "analyst@example.com",
            "is_admin": False,
            "groups": [],
            "email": "a@example.com",
            "display_name": "A",
        },
    )()

    with pytest.raises(InvalidRequestError, match="future"):
        access.create_request(
            CreateAccessRequestBody(
                attribute_value="public",
                requested_expires_at=datetime.now(UTC) - timedelta(days=1),
                justification="too late",
            ),
            requester,  # type: ignore[arg-type]
        )

    with pytest.raises(InvalidRequestError, match="not allowed"):
        access.create_request(
            CreateAccessRequestBody(
                attribute_value="PII.Name",
                requested_expires_at=datetime.now(UTC) + timedelta(days=10),
                justification="leaf only",
            ),
            requester,  # type: ignore[arg-type]
        )


def test_renewal_inbox_window(container) -> None:  # noqa: ANN001
    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    _seed_sensitivity(identity, actor)
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="public",
            principal_name="admin@example.com",
            principal_type=PrincipalKind.USER,
        ),
        actor,  # type: ignore[arg-type]
    )

    soon = identity.assign(
        AssignAttributeRequest(
            principal_name="user@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="public",
            expires_at=datetime.now(UTC) + timedelta(days=29),
        ),
        actor,  # type: ignore[arg-type]
    )
    identity.assign(
        AssignAttributeRequest(
            principal_name="other@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="public.PII.Name",
            expires_at=datetime.now(UTC) + timedelta(days=31),
        ),
        actor,  # type: ignore[arg-type]
    )

    inbox = access.approvals_inbox(actor)  # type: ignore[arg-type]
    renewal_ids = [
        item.assignment.assignment_id
        for item in inbox.items
        if item.kind.value == "RENEWAL" and item.assignment is not None
    ]
    assert soon.assignment_id in renewal_ids
    assert len(renewal_ids) == 1

    renewed = access.approve_renewal(
        soon.assignment_id,
        DecideAccessRequestBody(expires_at=datetime.now(UTC) + timedelta(days=90)),
        actor,  # type: ignore[arg-type]
    )
    assert renewed.expires_at is not None
    assert renewed.expires_at > datetime.now(UTC) + timedelta(days=60)


def test_api_access_request_flow(client: TestClient, container) -> None:  # noqa: ANN001
    identity = _service(container)
    _seed_sensitivity(identity, _admin())
    # Ensure container uses the faked governed tags for HTTP path too.
    container.identity_attributes._governed_tags = FakeGovernedTags()  # type: ignore[method-assign]
    container.access_requests = AccessRequestService(
        container.storage, container.identity_attributes
    )

    admin = headers_for("admin@example.com")
    created_approver = client.put(
        "/api/identity/category-approvers",
        headers=admin,
        json={
            "categoryPath": "public",
            "principalName": "admin@example.com",
            "principalType": "USER",
        },
    )
    assert created_approver.status_code == 200

    analyst = headers_for("analyst@example.com")
    created = client.post(
        "/api/identity/access-requests",
        headers=analyst,
        json={
            "attributeValue": "public.PII.Name",
            "requestedExpiresAt": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
            "justification": "Need name for report",
        },
    )
    assert created.status_code == 200
    request_id = created.json()["requestId"]

    mine = client.get("/api/identity/access-requests/mine", headers=analyst).json()
    assert mine["total"] == 1

    inbox = client.get("/api/identity/approvals", headers=admin).json()
    assert inbox["total"] >= 1
    assert any(item["kind"] == "REQUEST" for item in inbox["items"])

    approved = client.post(
        f"/api/identity/access-requests/{request_id}/approve",
        headers=admin,
        json={"expiresAt": (datetime.now(UTC) + timedelta(days=21)).isoformat()},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    forbidden = client.put(
        "/api/identity/category-approvers",
        headers=analyst,
        json={
            "categoryPath": "public",
            "principalName": "x@example.com",
            "principalType": "USER",
        },
    )
    assert forbidden.status_code == 403


def test_row_filter_request_routes_to_admins(container) -> None:  # noqa: ANN001
    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    from backend.app.models.identity_attribute import ValueSource

    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            description="US state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY", "*"],
        ),
        actor,
    )
    requester = type(
        "U",
        (),
        {
            "user_name": "analyst@example.com",
            "is_admin": False,
            "groups": [],
            "email": "analyst@example.com",
            "display_name": "Analyst",
        },
    )()
    created = access.create_request(
        CreateAccessRequestBody(
            attribute_key="state",
            attribute_value="*",
            requested_expires_at=datetime.now(UTC) + timedelta(days=30),
            justification="Need all states for us_states",
        ),
        requester,  # type: ignore[arg-type]
    )
    assert created.attribute_key == "state"
    assert created.attribute_value == "*"
    assert created.routed_to.value == "app_admins"
    inbox_admin = access.approvals_inbox(actor)  # type: ignore[arg-type]
    assert any(i.request and i.request.request_id == created.request_id for i in inbox_admin.items)
    non_admin = type(
        "U",
        (),
        {
            "user_name": "other@example.com",
            "is_admin": False,
            "groups": [],
            "email": "other@example.com",
            "display_name": "Other",
        },
    )()
    inbox_other = access.approvals_inbox(non_admin)  # type: ignore[arg-type]
    assert inbox_other.total == 0


def test_data_access_approver_hierarchical_routing(container) -> None:  # noqa: ANN001
    from backend.app.models.user import CurrentUser

    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="data_access",
            description="DAC",
            governed_tag_keys=["data_access_control"],
        ),
        actor,
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            attribute_key="data_access",
            category_path="finance",
            principal_name="dac-finance",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            attribute_key="data_access",
            category_path="finance.budget",
            principal_name="budget-dac@example.com",
            principal_type=PrincipalKind.USER,
        ),
        actor,
    )

    requester = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        groups=["users"],
        is_admin=False,
    )
    created = access.create_request(
        CreateAccessRequestBody(
            attribute_key="data_access",
            attribute_value="finance.budget",
            requested_expires_at=datetime.now(UTC) + timedelta(days=30),
            justification="Need budget tables",
        ),
        requester,
    )
    assert created.resolved_category == "finance.budget"
    assert created.routed_to.value == "category_approvers"
    assert [a.principal_name for a in created.approvers] == ["budget-dac@example.com"]

    specific = CurrentUser(
        user_name="budget-dac@example.com",
        email="budget-dac@example.com",
        display_name="Budget",
        groups=[],
        is_admin=False,
    )
    broad = CurrentUser(
        user_name="lead@example.com",
        email="lead@example.com",
        display_name="Lead",
        groups=["dac-finance"],
        is_admin=False,
    )
    outsider = CurrentUser(
        user_name="other@example.com",
        email="other@example.com",
        display_name="Other",
        groups=["users"],
        is_admin=False,
    )
    assert any(
        i.request and i.request.request_id == created.request_id
        for i in access.approvals_inbox(specific).items
    )
    # Broader-path group does not decide the more-specific request.
    assert access.approvals_inbox(broad).total == 0
    assert access.approvals_inbox(outsider).total == 0
    decided = access.approve_request(
        created.request_id,
        DecideAccessRequestBody(expires_at=datetime.now(UTC) + timedelta(days=14)),
        specific,
    )
    assert decided.status.value == "APPROVED"


def test_row_filter_request_routes_to_dimension_approvers(container) -> None:  # noqa: ANN001
    from backend.app.models.identity_attribute import ValueSource
    from backend.app.models.user import CurrentUser

    identity = _service(container)
    access = AccessRequestService(container.storage, identity)
    actor = _admin()
    identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state",
            description="US state",
            value_source=ValueSource.FREE_FORM,
            allowed_values=["CA", "NY", "*"],
        ),
        actor,
    )
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            attribute_key="state",
            category_path="state",
            principal_name="geo-owners",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,
    )
    requester = CurrentUser(
        user_name="analyst@example.com",
        email="analyst@example.com",
        display_name="Analyst",
        groups=["users"],
        is_admin=False,
    )
    created = access.create_request(
        CreateAccessRequestBody(
            attribute_key="state",
            attribute_value="CA",
            requested_expires_at=datetime.now(UTC) + timedelta(days=30),
            justification="Need California rows",
        ),
        requester,
    )
    assert created.routed_to.value == "category_approvers"
    assert created.resolved_category == "state"
    assert [a.principal_name for a in created.approvers] == ["geo-owners"]

    member = CurrentUser(
        user_name="lead@example.com",
        email="lead@example.com",
        display_name="Lead",
        groups=["geo-owners"],
        is_admin=False,
    )
    outsider = CurrentUser(
        user_name="other@example.com",
        email="other@example.com",
        display_name="Other",
        groups=["users"],
        is_admin=False,
    )
    inbox_member = access.approvals_inbox(member)
    assert any(i.request and i.request.request_id == created.request_id for i in inbox_member.items)
    inbox_outsider = access.approvals_inbox(outsider)
    assert inbox_outsider.total == 0
    # Admins still see and can decide dimension-routed requests.
    inbox_admin = access.approvals_inbox(actor)
    assert any(i.request and i.request.request_id == created.request_id for i in inbox_admin.items)


def test_is_category_approver_for_user_and_group(container) -> None:  # noqa: ANN001
    from backend.app.models.user import CurrentUser

    access = _access_service(container)
    actor = _admin()
    access.upsert_approver(
        UpsertCategoryApproverRequest(
            category_path="finance",
            principal_name="finance-owners",
            principal_type=PrincipalKind.GROUP,
        ),
        actor,
    )
    member = CurrentUser(
        user_name="lead@example.com",
        email="lead@example.com",
        display_name="Lead",
        groups=["finance-owners"],
        is_admin=False,
    )
    outsider = CurrentUser(
        user_name="other@example.com",
        email="other@example.com",
        display_name="Other",
        groups=["users"],
        is_admin=False,
    )
    assert access.is_category_approver(member) is True
    assert access.is_category_approver(outsider) is False

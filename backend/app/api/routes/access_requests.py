from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container, User
from backend.app.models.access_request import (
    AccessRequest,
    AccessRequestList,
    ApprovalInbox,
    CategoryApprover,
    CategoryApproverList,
    CreateAccessRequestBody,
    DecideAccessRequestBody,
    UpsertCategoryApproverRequest,
)
from backend.app.models.identity_attribute import IdentityAttributeAssignment

router = APIRouter(prefix="/identity", tags=["access-requests"])


@router.get("/category-approvers", response_model=CategoryApproverList)
def list_category_approvers(
    _: User,
    container: Container,
    category_path: str | None = None,
) -> CategoryApproverList:
    return container.access_requests.list_approvers(category_path)


@router.put("/category-approvers", response_model=CategoryApprover)
def upsert_category_approver(
    body: UpsertCategoryApproverRequest, admin: Admin, container: Container
) -> CategoryApprover:
    return container.access_requests.upsert_approver(body, admin)


@router.delete("/category-approvers/{approver_id}", status_code=204)
def delete_category_approver(approver_id: str, admin: Admin, container: Container) -> None:
    container.access_requests.delete_approver(approver_id, admin)


@router.post("/access-requests", response_model=AccessRequest)
def create_access_request(
    body: CreateAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    return container.access_requests.create_request(body, user)


@router.get("/access-requests/mine", response_model=AccessRequestList)
def my_access_requests(user: User, container: Container) -> AccessRequestList:
    return container.access_requests.list_mine(user)


@router.post("/access-requests/{request_id}/cancel", response_model=AccessRequest)
def cancel_access_request(
    request_id: str, user: User, container: Container
) -> AccessRequest:
    return container.access_requests.cancel(request_id, user)


@router.get("/approvals", response_model=ApprovalInbox)
def approvals_inbox(user: User, container: Container) -> ApprovalInbox:
    return container.access_requests.approvals_inbox(user)


@router.post("/access-requests/{request_id}/approve", response_model=AccessRequest)
def approve_access_request(
    request_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    return container.access_requests.approve_request(request_id, body, user)


@router.post("/access-requests/{request_id}/deny", response_model=AccessRequest)
def deny_access_request(
    request_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    return container.access_requests.deny_request(request_id, body, user)


@router.post(
    "/renewals/{assignment_id}/approve", response_model=IdentityAttributeAssignment
)
def approve_renewal(
    assignment_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> IdentityAttributeAssignment:
    return container.access_requests.approve_renewal(assignment_id, body, user)


@router.post("/renewals/{assignment_id}/dismiss", status_code=204)
def dismiss_renewal(assignment_id: str, user: User, container: Container) -> None:
    container.access_requests.dismiss_renewal(assignment_id, user)

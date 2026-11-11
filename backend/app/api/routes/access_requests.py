"""Sensitivity access requests and category approvers under ``/api/identity``.

Admins manage category approvers. Any signed-in user may create requests, view
their own, and act on the approval/renewal inbox when they are approvers.
"""

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
    _: Admin,
    container: Container,
    category_path: str | None = None,
) -> CategoryApproverList:
    """List sensitivity category approvers.

    Requires app admin.

    Args:
        container: Process-scoped service container (dependency injection).
        category_path: Optional sensitivity path filter.

    Returns:
        Matching category approver records.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.access_requests.list_approvers(category_path)


@router.put("/category-approvers", response_model=CategoryApprover)
def upsert_category_approver(
    body: UpsertCategoryApproverRequest, admin: Admin, container: Container
) -> CategoryApprover:
    """Create or update a category approver mapping.

    Requires app admin.

    Args:
        body: Category path and approver principal to upsert.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The persisted category approver.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.access_requests.upsert_approver(body, admin)


@router.delete("/category-approvers/{approver_id}", status_code=204)
def delete_category_approver(approver_id: str, admin: Admin, container: Container) -> None:
    """Delete a category approver by id.

    Requires app admin.

    Args:
        approver_id: Approver record identifier.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the approver does not exist (HTTP 404).
    """
    container.access_requests.delete_approver(approver_id, admin)


@router.post("/access-requests", response_model=AccessRequest)
def create_access_request(
    body: CreateAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    """Submit a new sensitivity access request for the caller.

    Any signed-in user.

    Args:
        body: Requested attribute/category and justification.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The created access request.
    """
    return container.access_requests.create_request(body, user)


@router.get("/access-requests/mine", response_model=AccessRequestList)
def my_access_requests(user: User, container: Container) -> AccessRequestList:
    """List access requests created by the current user.

    Any signed-in user.

    Returns:
        The caller's access requests.
    """
    return container.access_requests.list_mine(user)


@router.post("/access-requests/{request_id}/cancel", response_model=AccessRequest)
def cancel_access_request(
    request_id: str, user: User, container: Container
) -> AccessRequest:
    """Cancel one of the caller's pending access requests.

    Any signed-in user (must be the requester).

    Args:
        request_id: Access request identifier.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The cancelled access request.

    Raises:
        NotFoundError: If the request does not exist (HTTP 404).
        ForbiddenError: If the caller is not the requester (HTTP 403).
    """
    return container.access_requests.cancel(request_id, user)


@router.get("/approvals", response_model=ApprovalInbox)
def approvals_inbox(user: User, container: Container) -> ApprovalInbox:
    """Return the caller's approval and renewal inbox.

    Any signed-in user (contents depend on approver/admin status).

    Returns:
        Pending access requests and renewals the caller may act on.
    """
    return container.access_requests.approvals_inbox(user)


@router.post("/access-requests/{request_id}/approve", response_model=AccessRequest)
def approve_access_request(
    request_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    """Approve a pending access request.

    Any signed-in user who is an eligible category approver or app admin.

    Args:
        request_id: Access request identifier.
        body: Decision notes and optional overrides.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The approved access request.

    Raises:
        NotFoundError: If the request does not exist (HTTP 404).
        ForbiddenError: If the caller may not decide this request (HTTP 403).
    """
    return container.access_requests.approve_request(request_id, body, user)


@router.post("/access-requests/{request_id}/deny", response_model=AccessRequest)
def deny_access_request(
    request_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> AccessRequest:
    """Deny a pending access request.

    Any signed-in user who is an eligible category approver or app admin.

    Args:
        request_id: Access request identifier.
        body: Decision notes explaining the denial.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The denied access request.

    Raises:
        NotFoundError: If the request does not exist (HTTP 404).
        ForbiddenError: If the caller may not decide this request (HTTP 403).
    """
    return container.access_requests.deny_request(request_id, body, user)


@router.post(
    "/renewals/{assignment_id}/approve", response_model=IdentityAttributeAssignment
)
def approve_renewal(
    assignment_id: str, body: DecideAccessRequestBody, user: User, container: Container
) -> IdentityAttributeAssignment:
    """Approve renewal of an expiring identity attribute assignment.

    Any signed-in user who is an eligible category approver or app admin.

    Args:
        assignment_id: Assignment identifier pending renewal.
        body: Decision notes and optional overrides.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The renewed assignment.

    Raises:
        NotFoundError: If the assignment does not exist (HTTP 404).
        ForbiddenError: If the caller may not decide this renewal (HTTP 403).
    """
    return container.access_requests.approve_renewal(assignment_id, body, user)


@router.post("/renewals/{assignment_id}/dismiss", status_code=204)
def dismiss_renewal(assignment_id: str, user: User, container: Container) -> None:
    """Dismiss a renewal reminder from the caller's inbox.

    Any signed-in user who can see the renewal in their inbox.

    Args:
        assignment_id: Assignment identifier whose renewal reminder to dismiss.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        NotFoundError: If the assignment does not exist (HTTP 404).
    """
    container.access_requests.dismiss_renewal(assignment_id, user)

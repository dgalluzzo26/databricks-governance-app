"""Identity attribute definitions and assignments under ``/api/identity``.

Any signed-in user may list governed tags, definitions, and their own
assignments. Creating/updating definitions and UC sync require app admin.
Listing and assigning attributes is allowed for app admins and path approvers
(approvers only see and grant paths they can decide). Revoking an assignment is
allowed for app admins and for approvers of that attribute path.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container, User
from backend.app.core.errors import ForbiddenError
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeAssignmentList,
    AttributeDefinition,
    AttributeDefinitionList,
    BulkAssignRequest,
    BulkAssignResult,
    GovernedTagList,
    GrantableAttributeList,
    IdentityAttributeAssignment,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.my_access import (
    MyAccessResources,
    MyAccessResourcesRequest,
    MyAccessSnapshot,
)
from backend.app.models.principal import PrincipalKind

router = APIRouter(prefix="/identity", tags=["identity-attributes"])

# Optional principal-kind query filter for assignment listing.
PrincipalTypeQuery = Annotated[PrincipalKind | None, Query()]


def _require_assigner(user: User) -> None:
    """Raise when the caller is neither an app admin nor a path approver."""
    if not (user.is_admin or user.is_approver):
        raise ForbiddenError("Assignments are limited to admins and approvers")


@router.get("/governed-tags", response_model=GovernedTagList)
def list_governed_tags(
    _: User,
    container: Container,
    q: str | None = None,
    limit: int = 50,
) -> GovernedTagList:
    """Search Unity Catalog governed tags for attribute mapping.

    Any signed-in user.

    Args:
        container: Process-scoped service container (dependency injection).
        q: Optional search substring for tag names.
        limit: Maximum number of tags to return (default 50).

    Returns:
        Matching governed tags.
    """
    return container.identity_attributes.list_governed_tags(q=q, limit=limit)


@router.get("/definitions", response_model=AttributeDefinitionList)
def list_definitions(_: User, container: Container) -> AttributeDefinitionList:
    """List identity attribute definitions.

    Any signed-in user.

    Returns:
        All configured attribute definitions.
    """
    return container.identity_attributes.list_definitions()


@router.put("/definitions", response_model=AttributeDefinition)
def upsert_definition(
    body: UpsertAttributeDefinitionRequest, admin: Admin, container: Container
) -> AttributeDefinition:
    """Create or update an identity attribute definition.

    Requires app admin.

    Args:
        body: Definition fields to upsert (key, display, governed tag mapping).
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The persisted attribute definition.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.identity_attributes.upsert_definition(body, admin)


@router.delete("/definitions/{key}", status_code=204)
def delete_definition(key: str, admin: Admin, container: Container) -> None:
    """Delete an identity attribute definition by key.

    Requires app admin.

    Args:
        key: Attribute definition key to remove.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the definition does not exist (HTTP 404).
    """
    container.identity_attributes.delete_definition(key, admin)


@router.get("/grantable-attributes", response_model=GrantableAttributeList)
def list_grantable_attributes(user: User, container: Container) -> GrantableAttributeList:
    """Attribute keys the caller may grant from the Assignments form.

    App admins get every definition. Path approvers get keys (and category paths) they are
    designated for. Values are still re-checked on assign.

    Args:
        user: Signed-in user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Grantable attribute keys.

    Raises:
        ForbiddenError: If the caller is neither an admin nor an approver (HTTP 403).
    """
    _require_assigner(user)
    return container.access_requests.grantable_attributes(user)


@router.get("/assignments", response_model=AttributeAssignmentList)
def list_assignments(
    user: User,
    container: Container,
    principal_name: str | None = None,
    principal_search: str | None = None,
    principal_type: PrincipalTypeQuery = None,
    attribute_key: str | None = None,
    include_inactive: bool = False,
) -> AttributeAssignmentList:
    """List identity attribute assignments with optional filters.

    App admins see every matching assignment. Path approvers only see assignments for
    paths they can decide.

    Args:
        user: Signed-in user (dependency injection).
        container: Process-scoped service container (dependency injection).
        principal_name: Optional exact principal name filter.
        principal_search: Optional partial principal name filter (case-insensitive contains).
        principal_type: Optional principal kind filter (user/group/sp).
        attribute_key: Optional attribute definition key filter.
        include_inactive: When true, include revoked/expired assignments.

    Returns:
        Matching attribute assignments.

    Raises:
        ForbiddenError: If the caller is neither an admin nor an approver (HTTP 403).
    """
    _require_assigner(user)
    return container.identity_attributes.list_assignments(
        principal_name=principal_name,
        principal_search=principal_search,
        principal_type=principal_type,
        attribute_key=attribute_key,
        include_inactive=include_inactive,
        actor=user,
        can_decide=lambda key, value: container.access_requests._can_decide_for(  # noqa: SLF001
            user, value, attribute_key=key
        ),
    )


@router.get("/assignments/me", response_model=AttributeAssignmentList)
def my_assignments(user: User, container: Container) -> AttributeAssignmentList:
    """List identity attribute assignments for the current user.

    Any signed-in user.

    Returns:
        Assignments belonging to the caller (and their groups as applicable).
    """
    return container.identity_attributes.list_for_user(user)


@router.get("/my-access", response_model=MyAccessSnapshot)
def my_access(user: User, container: Container) -> MyAccessSnapshot:
    """Groups the caller belongs to and every attribute they hold (direct or via group).

    Any signed-in user. Data-access rows include sync status when available.
    """
    return container.my_access.snapshot(user)


@router.post("/my-access/resources", response_model=MyAccessResources)
def my_access_resources(
    body: MyAccessResourcesRequest, user: User, container: Container
) -> MyAccessResources:
    """Tables/columns affected by one attribute the caller holds.

    Any signed-in user. The key/value must appear in their effective attributes
    (for row-filter dimensions, any held value for that key is enough).
    """
    return container.my_access.resources(body, user)


@router.post("/assignments", response_model=IdentityAttributeAssignment)
def assign_attribute(
    body: AssignAttributeRequest, user: User, container: Container
) -> IdentityAttributeAssignment:
    """Assign an identity attribute to a principal.

    App admins may assign any defined value. Path approvers may only assign values for
    paths they are authorized to decide (same scope as Approvals / Attribute Explorer).

    Args:
        body: Principal, attribute key, value, and optional expiry.
        user: Signed-in user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The created assignment.

    Raises:
        ForbiddenError: If the caller is not an admin or an approver for this path (HTTP 403).
    """
    _require_assigner(user)
    if not user.is_admin:
        container.access_requests._require_can_decide(  # noqa: SLF001
            user, body.attribute_value, attribute_key=body.attribute_key
        )
    return container.identity_attributes.assign(body, user)


@router.post("/assignments/bulk", response_model=BulkAssignResult)
def bulk_assign_attributes(body: BulkAssignRequest, user: User, container: Container) -> BulkAssignResult:
    """Assign many principals × many attribute values in one submit.

    Justification and expiry are shared. Each grant is still created and audited as an
    individual assignment. Partial success is allowed: failed rows are listed with reasons.

    Args:
        body: Principals, attribute rows, and shared justification/expiry.
        user: Signed-in user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Succeeded, skipped, and failed items.

    Raises:
        ForbiddenError: If the caller is neither an admin nor an approver, or an attribute
            value is out of the caller's scope (HTTP 403).
        InvalidRequestError: If caps are exceeded (HTTP 422).
    """
    _require_assigner(user)

    def require_can_decide(attribute_key: str, attribute_value: str) -> None:
        container.access_requests._require_can_decide(  # noqa: SLF001
            user, attribute_value, attribute_key=attribute_key
        )

    return container.identity_attributes.bulk_assign(
        body,
        user,
        require_can_decide=None if user.is_admin else require_can_decide,
    )


@router.post("/assignments/{assignment_id}/revoke", response_model=IdentityAttributeAssignment)
def revoke_assignment(assignment_id: str, user: User, container: Container) -> IdentityAttributeAssignment:
    """Revoke an identity attribute assignment.

    App admins may revoke any assignment. Category / data-access / dimension approvers may
    revoke assignments for paths they are authorized to decide (same scope as Attribute Explorer).

    Args:
        assignment_id: Assignment identifier to revoke.
        user: Signed-in user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The revoked assignment record.

    Raises:
        ForbiddenError: If the caller is not an admin or an approver for this path (HTTP 403).
        NotFoundError: If the assignment does not exist (HTTP 404).
    """
    current = container.identity_attributes.get_assignment(assignment_id)
    container.access_requests._require_can_decide(  # noqa: SLF001
        user, current.attribute_value, attribute_key=current.attribute_key
    )
    return container.identity_attributes.revoke(assignment_id, user)


@router.post("/sync-uc")
def sync_identity_to_uc(admin: Admin, container: Container) -> dict[str, int]:
    """Replace the UC Delta mirror from Lakebase (for mask/row-filter UDFs).

    Requires app admin.

    Returns:
        Counts of rows written/synced to Unity Catalog tables.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    with container.tasks.track(
        "identity.sync_uc", "Sync identity attributes to Unity Catalog", admin.user_name, link="/identity"
    ):
        return container.identity_attributes.sync_to_uc()

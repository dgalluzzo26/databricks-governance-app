"""Identity attribute definitions and assignments under ``/api/identity``.

Any signed-in user may list governed tags, definitions, and their own
assignments. Creating/updating definitions, managing assignments, and UC sync
require app admin.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container, User
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeAssignmentList,
    AttributeDefinition,
    AttributeDefinitionList,
    GovernedTagList,
    IdentityAttributeAssignment,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind

router = APIRouter(prefix="/identity", tags=["identity-attributes"])

# Optional principal-kind query filter for assignment listing.
PrincipalTypeQuery = Annotated[PrincipalKind | None, Query()]


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


@router.get("/assignments", response_model=AttributeAssignmentList)
def list_assignments(
    _: Admin,
    container: Container,
    principal_name: str | None = None,
    principal_type: PrincipalTypeQuery = None,
    attribute_key: str | None = None,
    include_inactive: bool = False,
) -> AttributeAssignmentList:
    """List identity attribute assignments with optional filters.

    Requires app admin.

    Args:
        container: Process-scoped service container (dependency injection).
        principal_name: Optional principal name filter.
        principal_type: Optional principal kind filter (user/group/sp).
        attribute_key: Optional attribute definition key filter.
        include_inactive: When true, include revoked/expired assignments.

    Returns:
        Matching attribute assignments.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.identity_attributes.list_assignments(
        principal_name=principal_name,
        principal_type=principal_type,
        attribute_key=attribute_key,
        include_inactive=include_inactive,
    )


@router.get("/assignments/me", response_model=AttributeAssignmentList)
def my_assignments(user: User, container: Container) -> AttributeAssignmentList:
    """List identity attribute assignments for the current user.

    Any signed-in user.

    Returns:
        Assignments belonging to the caller (and their groups as applicable).
    """
    return container.identity_attributes.list_for_user(user)


@router.post("/assignments", response_model=IdentityAttributeAssignment)
def assign_attribute(
    body: AssignAttributeRequest, admin: Admin, container: Container
) -> IdentityAttributeAssignment:
    """Assign an identity attribute to a principal.

    Requires app admin.

    Args:
        body: Principal, attribute key, value, and optional expiry.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The created assignment.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.identity_attributes.assign(body, admin)


@router.post("/assignments/{assignment_id}/revoke", response_model=IdentityAttributeAssignment)
def revoke_assignment(
    assignment_id: str, admin: Admin, container: Container
) -> IdentityAttributeAssignment:
    """Revoke an identity attribute assignment.

    Requires app admin.

    Args:
        assignment_id: Assignment identifier to revoke.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The revoked assignment record.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the assignment does not exist (HTTP 404).
    """
    return container.identity_attributes.revoke(assignment_id, admin)


@router.post("/sync-uc")
def sync_identity_to_uc(admin: Admin, container: Container) -> dict[str, int]:
    """Replace the UC Delta mirror from Lakebase (for mask/row-filter UDFs).

    Requires app admin.

    Returns:
        Counts of rows written/synced to Unity Catalog tables.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    _ = admin
    return container.identity_attributes.sync_to_uc()

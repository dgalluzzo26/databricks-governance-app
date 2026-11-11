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

PrincipalTypeQuery = Annotated[PrincipalKind | None, Query()]


@router.get("/governed-tags", response_model=GovernedTagList)
def list_governed_tags(
    _: User,
    container: Container,
    q: str | None = None,
    limit: int = 50,
) -> GovernedTagList:
    return container.identity_attributes.list_governed_tags(q=q, limit=limit)


@router.get("/definitions", response_model=AttributeDefinitionList)
def list_definitions(_: User, container: Container) -> AttributeDefinitionList:
    return container.identity_attributes.list_definitions()


@router.put("/definitions", response_model=AttributeDefinition)
def upsert_definition(
    body: UpsertAttributeDefinitionRequest, admin: Admin, container: Container
) -> AttributeDefinition:
    return container.identity_attributes.upsert_definition(body, admin)


@router.delete("/definitions/{key}", status_code=204)
def delete_definition(key: str, admin: Admin, container: Container) -> None:
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
    return container.identity_attributes.list_assignments(
        principal_name=principal_name,
        principal_type=principal_type,
        attribute_key=attribute_key,
        include_inactive=include_inactive,
    )


@router.get("/assignments/me", response_model=AttributeAssignmentList)
def my_assignments(user: User, container: Container) -> AttributeAssignmentList:
    return container.identity_attributes.list_for_user(user)


@router.post("/assignments", response_model=IdentityAttributeAssignment)
def assign_attribute(
    body: AssignAttributeRequest, admin: Admin, container: Container
) -> IdentityAttributeAssignment:
    return container.identity_attributes.assign(body, admin)


@router.post("/assignments/{assignment_id}/revoke", response_model=IdentityAttributeAssignment)
def revoke_assignment(
    assignment_id: str, admin: Admin, container: Container
) -> IdentityAttributeAssignment:
    return container.identity_attributes.revoke(assignment_id, admin)


@router.post("/sync-uc")
def sync_identity_to_uc(admin: Admin, container: Container) -> dict[str, int]:
    """Replace the UC Delta mirror from Lakebase (for mask/row-filter UDFs)."""
    _ = admin
    return container.identity_attributes.sync_to_uc()

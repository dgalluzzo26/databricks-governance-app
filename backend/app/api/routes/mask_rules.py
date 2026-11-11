"""Column mask rule registry and pack operations under ``/api/mask-rules``.

Requires app admin for all endpoints. Manages mask rules, salt, handlers, and
Unity Catalog sync for column masking.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.mask_rule import (
    MaskHandlerDraft,
    MaskHandlerDraftRequest,
    MaskHandlerValidateRequest,
    MaskHandlerValidateResult,
    MaskingConfigStatus,
    MaskRuleDefinition,
    MaskRuleList,
    MaskRulePackStatus,
    UpsertMaskRuleRequest,
)

router = APIRouter(prefix="/mask-rules", tags=["mask-rules"])


@router.get("", response_model=MaskRuleList)
def list_rules(_: Admin, container: Container) -> MaskRuleList:
    """List registered column mask rules.

    Requires app admin.

    Returns:
        All mask rule definitions.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.list_rules()


@router.put("", response_model=MaskRuleDefinition)
def upsert_rule(body: UpsertMaskRuleRequest, admin: Admin, container: Container) -> MaskRuleDefinition:
    """Create or update a column mask rule.

    Requires app admin.

    Args:
        body: Mask rule key, handler, and metadata to upsert.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The persisted mask rule definition.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.upsert_rule(body, admin)


@router.delete("/{rule_key}", status_code=204)
def delete_rule(rule_key: str, admin: Admin, container: Container) -> None:
    """Delete a column mask rule by key.

    Requires app admin.

    Args:
        rule_key: Mask rule key to remove.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the rule does not exist (HTTP 404).
    """
    container.mask_rules.delete_rule(rule_key, admin)


@router.post("/seed", response_model=MaskRuleList)
def seed_defaults(admin: Admin, container: Container) -> MaskRuleList:
    """Seed built-in default mask rules when the registry is empty.

    Requires app admin.

    Returns:
        Mask rules after seeding.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.seed_defaults(admin)


@router.get("/pack", response_model=MaskRulePackStatus)
def pack_status(admin: Admin, container: Container) -> MaskRulePackStatus:
    """Return mask-rule pack / UDF / policy status.

    Requires app admin.

    Returns:
        Current pack deployment status.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    _ = admin
    return container.mask_rules.pack_status()


@router.post("/pack/ensure", response_model=MaskRulePackStatus)
def ensure_pack(admin: Admin, container: Container) -> MaskRulePackStatus:
    """Ensure the mask-rule pack, salt, and UC artifacts exist.

    Requires app admin.

    Returns:
        Pack status after ensure/rebuild.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.ensure_pack(admin)


@router.post("/sync-uc")
def sync_uc(admin: Admin, container: Container) -> dict[str, int]:
    """Sync the mask-rule registry to Unity Catalog Delta tables.

    Requires app admin.

    Returns:
        Counts of rows written/synced.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    _ = admin
    return container.mask_rules.sync_to_uc()


@router.post("/salt/rotate", response_model=MaskingConfigStatus)
def rotate_salt(admin: Admin, container: Container) -> MaskingConfigStatus:
    """Rotate the hashing salt used by mask handlers.

    Requires app admin.

    Returns:
        Updated masking configuration status after rotation.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.ensure_salt(admin, rotate=True)


@router.post("/handlers/draft", response_model=MaskHandlerDraft)
def draft_handler(
    body: MaskHandlerDraftRequest, admin: Admin, container: Container
) -> MaskHandlerDraft:
    """Draft SQL/handler code for a custom mask rule.

    Requires app admin.

    Args:
        body: Inputs describing the desired mask handler.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Drafted handler source and related metadata.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.draft_handler(body, admin)


@router.post("/handlers/validate", response_model=MaskHandlerValidateResult)
def validate_handler(
    body: MaskHandlerValidateRequest, admin: Admin, container: Container
) -> MaskHandlerValidateResult:
    """Validate a custom mask handler before saving.

    Requires app admin.

    Args:
        body: Handler source and validation parameters.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Validation result including errors or sample output.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.mask_rules.validate_handler(body, admin)

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
    return container.mask_rules.list_rules()


@router.put("", response_model=MaskRuleDefinition)
def upsert_rule(body: UpsertMaskRuleRequest, admin: Admin, container: Container) -> MaskRuleDefinition:
    return container.mask_rules.upsert_rule(body, admin)


@router.delete("/{rule_key}", status_code=204)
def delete_rule(rule_key: str, admin: Admin, container: Container) -> None:
    container.mask_rules.delete_rule(rule_key, admin)


@router.post("/seed", response_model=MaskRuleList)
def seed_defaults(admin: Admin, container: Container) -> MaskRuleList:
    return container.mask_rules.seed_defaults(admin)


@router.get("/pack", response_model=MaskRulePackStatus)
def pack_status(admin: Admin, container: Container) -> MaskRulePackStatus:
    _ = admin
    return container.mask_rules.pack_status()


@router.post("/pack/ensure", response_model=MaskRulePackStatus)
def ensure_pack(admin: Admin, container: Container) -> MaskRulePackStatus:
    return container.mask_rules.ensure_pack(admin)


@router.post("/sync-uc")
def sync_uc(admin: Admin, container: Container) -> dict[str, int]:
    _ = admin
    return container.mask_rules.sync_to_uc()


@router.post("/salt/rotate", response_model=MaskingConfigStatus)
def rotate_salt(admin: Admin, container: Container) -> MaskingConfigStatus:
    return container.mask_rules.ensure_salt(admin, rotate=True)


@router.post("/handlers/draft", response_model=MaskHandlerDraft)
def draft_handler(
    body: MaskHandlerDraftRequest, admin: Admin, container: Container
) -> MaskHandlerDraft:
    return container.mask_rules.draft_handler(body, admin)


@router.post("/handlers/validate", response_model=MaskHandlerValidateResult)
def validate_handler(
    body: MaskHandlerValidateRequest, admin: Admin, container: Container
) -> MaskHandlerValidateResult:
    return container.mask_rules.validate_handler(body, admin)

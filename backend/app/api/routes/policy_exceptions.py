"""Admin API for global ABAC policy exceptions."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.policy_exception import (
    ApplyPolicyExceptionsResult,
    PolicyException,
    PolicyExceptionList,
    UpsertPolicyExceptionRequest,
)

router = APIRouter(prefix="/policy-exceptions", tags=["policy-exceptions"])


@router.get("", response_model=PolicyExceptionList)
def list_exceptions(_: Admin, container: Container) -> PolicyExceptionList:
    return container.policy_exceptions.list_exceptions()


@router.put("", response_model=PolicyException)
def upsert_exception(
    body: UpsertPolicyExceptionRequest, admin: Admin, container: Container
) -> PolicyException:
    return container.policy_exceptions.upsert(body, admin)


@router.delete("/{exception_id}")
def delete_exception(exception_id: str, admin: Admin, container: Container) -> dict[str, bool]:
    container.policy_exceptions.delete(exception_id, admin)
    return {"ok": True}


@router.post("/apply", response_model=ApplyPolicyExceptionsResult)
def apply_exceptions(admin: Admin, container: Container) -> ApplyPolicyExceptionsResult:
    return container.policy_exceptions.apply(admin)

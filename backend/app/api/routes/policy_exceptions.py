"""Admin API for global ABAC policy exceptions under ``/api/policy-exceptions``.

Requires app admin. Manages principals listed on ABAC EXCEPT clauses and
rebuilds mask/row-filter packs so exceptions take effect.
"""

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
    """List registered global policy exception principals.

    Requires app admin.

    Returns:
        All stored policy exceptions.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.policy_exceptions.list_exceptions()


@router.put("", response_model=PolicyException)
def upsert_exception(
    body: UpsertPolicyExceptionRequest, admin: Admin, container: Container
) -> PolicyException:
    """Create or update a global policy exception.

    Requires app admin.

    Args:
        body: Exception principal and metadata to upsert.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The persisted policy exception.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.policy_exceptions.upsert(body, admin)


@router.delete("/{exception_id}")
def delete_exception(exception_id: str, admin: Admin, container: Container) -> dict[str, bool]:
    """Delete a global policy exception by id.

    Requires app admin.

    Args:
        exception_id: Identifier of the exception to remove.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        ``{"ok": True}`` when deletion succeeds.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the exception does not exist (HTTP 404).
    """
    container.policy_exceptions.delete(exception_id, admin)
    return {"ok": True}


@router.post("/apply", response_model=ApplyPolicyExceptionsResult)
def apply_exceptions(admin: Admin, container: Container) -> ApplyPolicyExceptionsResult:
    """Rebuild packs so EXCEPT lists pick up the current registry.

    Requires app admin.

    Returns:
        Summary of apply/rebuild outcomes.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.policy_exceptions.apply(admin)

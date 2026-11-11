"""Unity Catalog ABAC policy read API under ``/api/policies``.

Requires app admin. Lists and fetches policy details from the workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container
from backend.app.models.policy import PolicyDetail, PolicyListResponse

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=PolicyListResponse)
def list_policies(
    _: Admin,
    container: Container,
    scope: str | None = Query(
        None,
        description="Optional bucket filter: metastore or catalog",
        pattern="^(metastore|catalog)$",
    ),
    policy_type: str | None = Query(None, description="COLUMN_MASK, ROW_FILTER, GRANT, or DENY"),
    catalog: str | None = Query(None, description="Filter catalog-scoped policies by catalog name"),
    q: str | None = Query(None, description="Search name, scope, principals, tags, or condition"),
) -> PolicyListResponse:
    """List ABAC policies with optional filters.

    Requires app admin.

    Args:
        container: Process-scoped service container (dependency injection).
        scope: Optional ``metastore`` or ``catalog`` bucket filter.
        policy_type: Optional type filter (COLUMN_MASK, ROW_FILTER, GRANT, DENY).
        catalog: Optional catalog name for catalog-scoped policies.
        q: Optional free-text search across name, scope, principals, tags, condition.

    Returns:
        Paginated or aggregated policy list response.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.policies.list_policies(scope=scope, policy_type=policy_type, catalog=catalog, q=q)


@router.get("/{policy_id}", response_model=PolicyDetail)
def get_policy(_: Admin, container: Container, policy_id: str) -> PolicyDetail:
    """Fetch a single ABAC policy by id.

    Requires app admin.

    Args:
        container: Process-scoped service container (dependency injection).
        policy_id: Unity Catalog policy identifier.

    Returns:
        Full policy detail including condition and principals.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If no policy exists for ``policy_id`` (HTTP 404).
    """
    return container.policies.get_policy(policy_id)

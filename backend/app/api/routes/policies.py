from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Container, User
from backend.app.models.policy import PolicyDetail, PolicyListResponse

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=PolicyListResponse)
def list_policies(
    _: User,
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
    return container.policies.list_policies(scope=scope, policy_type=policy_type, catalog=catalog, q=q)


@router.get("/{policy_id}", response_model=PolicyDetail)
def get_policy(_: User, container: Container, policy_id: str) -> PolicyDetail:
    return container.policies.get_policy(policy_id)

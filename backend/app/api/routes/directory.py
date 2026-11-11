"""Directory search for users and groups under ``/api/directory``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container
from backend.app.models.directory import DirectoryPrincipalList
from backend.app.models.principal import PrincipalKind

router = APIRouter(prefix="/directory", tags=["directory"])

PrincipalKindQuery = Annotated[PrincipalKind | None, Query()]


@router.get("/principals", response_model=DirectoryPrincipalList)
def search_principals(
    _: Admin,
    container: Container,
    q: str = Query(..., min_length=2, max_length=200),
    kind: PrincipalKindQuery = None,
    limit: int = Query(20, ge=1, le=50),
) -> DirectoryPrincipalList:
    """Search users and/or groups by name substring.

    Requires app admin. Used by Identity assignment and approver forms.

    Args:
        container: Process-scoped service container.
        q: Search text (2-200 characters).
        kind: Optional ``USER`` or ``GROUP`` filter.
        limit: Maximum results (1-50, default 20).

    Returns:
        Matching principals.
    """
    return container.identity.search_principals(q, kind=kind, limit=limit)

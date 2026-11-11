"""Attribute Explorer API — who holds a path and which tables it unlocks."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Container, User
from backend.app.core.errors import ForbiddenError
from backend.app.models.attribute_explorer import AnalyzeAttributeRequest, AttributeExplorerResult

router = APIRouter(prefix="/attribute-explorer", tags=["attribute-explorer"])


@router.post("/analyze", response_model=AttributeExplorerResult)
def analyze_attribute(
    body: AnalyzeAttributeRequest, user: User, container: Container
) -> AttributeExplorerResult:
    """Inspect holders (and unlocked tables for data-access) for one attribute path.

    App admins may analyze any path. Category / data-access / dimension approvers may only
    analyze paths they are authorized to decide.

    Args:
        body: Attribute key and value.
        user: Signed-in user.
        container: Service container.

    Returns:
        Holders, optional tables, and group metadata.
    """
    if not (user.is_admin or user.is_approver):
        raise ForbiddenError("Attribute Explorer is limited to admins and approvers")
    return container.attribute_explorer.analyze(body, user)

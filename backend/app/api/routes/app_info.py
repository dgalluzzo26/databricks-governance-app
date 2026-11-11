"""Deployment diagnostics at ``GET /api/app-info``.

Requires app admin. Surfaces config and connectivity status for operators.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.app_info import AppInfo

router = APIRouter(tags=["app"])


@router.get("/app-info", response_model=AppInfo)
def app_info(_: Admin, container: Container) -> AppInfo:
    """Return deployment and connectivity diagnostics.

    Requires app admin.

    Returns:
        Aggregated app info from the service container.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.app_info.info()

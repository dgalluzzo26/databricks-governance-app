"""App-wide runtime settings under ``/api/app-settings``.

Requires app admin. Holds switches that apply to the whole app rather than to data-access sync.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.app_settings import AppSettings

router = APIRouter(prefix="/app-settings", tags=["app-settings"])


@router.get("", response_model=AppSettings)
def get_settings(_: Admin, container: Container) -> AppSettings:
    """Return app-wide runtime settings.

    Requires app admin.

    Returns:
        Stored settings, or defaults when none are saved.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.app_settings.get_settings()


@router.put("", response_model=AppSettings)
def put_settings(body: AppSettings, admin: Admin, container: Container) -> AppSettings:
    """Save app-wide runtime settings.

    Requires app admin. Fields the request leaves out keep their stored values.

    Args:
        body: Settings to persist.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The settings as stored.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.app_settings.save_settings(body, admin)

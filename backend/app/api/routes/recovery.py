"""Recovery export API under ``/api/recovery``.

Requires app admin. Phase 1 exposes the Git target settings and a dry-run preview of the YAML
files a recovery push would commit. Nothing is written to Git yet.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.recovery import RecoveryGitSettings, RecoveryPreview

router = APIRouter(prefix="/recovery", tags=["recovery"])


@router.get("/settings", response_model=RecoveryGitSettings)
def get_settings(_: Admin, container: Container) -> RecoveryGitSettings:
    """Return the Git target settings for recovery exports.

    Requires app admin.

    Returns:
        Saved settings, or defaults when none are saved. Never includes the token.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.recovery.get_settings()


@router.put("/settings", response_model=RecoveryGitSettings)
def save_settings(body: RecoveryGitSettings, admin: Admin, container: Container) -> RecoveryGitSettings:
    """Save the Git target settings (URL, branch, path, user, token secret key name).

    Requires app admin.

    Args:
        body: Settings to persist.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The settings as stored.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.recovery.save_settings(body, admin)


@router.post("/export/preview", response_model=RecoveryPreview)
def preview_export(admin: Admin, container: Container) -> RecoveryPreview:
    """Build the recovery export files without writing to Git.

    Requires app admin. Each file's YAML is returned so it can be reviewed before a push.

    Args:
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The files, target, and warnings for this snapshot.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.recovery.build_preview(admin)

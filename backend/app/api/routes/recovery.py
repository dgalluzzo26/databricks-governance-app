"""Recovery export / import API under ``/api/recovery``.

Requires app admin. Exposes the Git target settings, a dry-run export preview, the push that
commits those files, an import that reads them back, and recover that upserts into app storage.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.recovery import (
    RecoveryApplyRequest,
    RecoveryApplyResult,
    RecoveryGitSettings,
    RecoveryImportPreview,
    RecoveryPreview,
    RecoveryPushResult,
)

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


@router.post("/export/push", response_model=RecoveryPushResult)
def push_export(admin: Admin, container: Container) -> RecoveryPushResult:
    """Commit the export files to the saved Git repository and branch.

    Requires app admin. Reads the token from the admin secret scope at push time; the token is
    never returned. Pushes nothing when the branch already matches the export.

    Args:
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The commit reference and whether a new commit was pushed.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        InvalidRequestError: If the repository, path, or token secret is not configured (HTTP 422).
        UpstreamError: If a Git step failed (HTTP 502).
    """
    return container.recovery.push(admin)


@router.post("/import", response_model=RecoveryImportPreview)
def import_from_git(admin: Admin, container: Container) -> RecoveryImportPreview:
    """Fetch the recovery snapshot from Git without writing app state.

    Requires app admin. Returns the files, occupancy of the current app, and warnings so the
    operator can decide whether to recover.

    Args:
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Parsed files and occupancy warning.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        InvalidRequestError: If the repository, path, token, or snapshot is invalid (HTTP 422).
        UpstreamError: If a Git step failed (HTTP 502).
    """
    return container.recovery.import_from_git(admin)


@router.post("/recover", response_model=RecoveryApplyResult)
def recover(body: RecoveryApplyRequest, admin: Admin, container: Container) -> RecoveryApplyResult:
    """Pull the snapshot again and upsert it into app storage.

    Requires app admin and ``confirm=true``. Re-reads Git (does not trust a prior browser import).
    Local rows not in the snapshot are kept. Does not restore the joinable salt or sync UC.

    Args:
        body: Must set ``confirm`` to True.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Counts written per section and follow-up warnings.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        InvalidRequestError: If confirm is missing or the snapshot is invalid (HTTP 422).
        UpstreamError: If a Git step failed (HTTP 502).
    """
    return container.recovery.recover(admin, body)

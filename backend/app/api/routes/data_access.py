"""Admin API for data-access group settings, readiness, and sync runs."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container
from backend.app.models.data_access import (
    DataAccessReadiness,
    DataAccessRun,
    DataAccessSettings,
    DataAccessSettingsView,
    RunDataAccessRequest,
)

router = APIRouter(prefix="/data-access", tags=["data-access"])


@router.get("/settings", response_model=DataAccessSettingsView)
def get_settings(_: Admin, container: Container) -> DataAccessSettingsView:
    """Return data-access sync settings.

    Requires app admin.

    Returns:
        Current settings (or defaults) plus the minimum interval.
    """
    return container.data_access.get_settings()


@router.put("/settings", response_model=DataAccessSettingsView)
def put_settings(body: DataAccessSettings, admin: Admin, container: Container) -> DataAccessSettingsView:
    """Save data-access sync settings.

    Requires app admin.

    Args:
        body: Settings to persist.
        admin: Signed-in app admin.
        container: Service container.

    Returns:
        The stored settings view.
    """
    return container.data_access.save_settings(body, admin)


@router.get("/readiness", response_model=DataAccessReadiness)
def readiness(_: Admin, container: Container) -> DataAccessReadiness:
    """Probe Account Admin SP capabilities for dry-run and apply.

    Requires app admin.

    Returns:
        Per-check results and rolled-up ready flags.
    """
    return container.data_access.readiness()


@router.get("/runs", response_model=list[DataAccessRun])
def list_runs(_: Admin, container: Container) -> list[DataAccessRun]:
    """List recent syncs, newest first (summaries only).

    Requires app admin.

    Returns:
        Stored runs without full plans.
    """
    return container.data_access.list_runs()


@router.get("/runs/latest", response_model=DataAccessRun | None)
def latest_run(_: Admin, container: Container) -> DataAccessRun | None:
    """Return the most recent sync including its truncated plan.

    Requires app admin.

    Returns:
        The latest run, or null when none exist.
    """
    return container.data_access.latest_run()


@router.post("/runs", response_model=DataAccessRun)
def run_now(
    admin: Admin, container: Container, body: RunDataAccessRequest | None = None
) -> DataAccessRun:
    """Run a data-access sync now (dry-run or apply).

    Requires app admin. Apply creates/nests groups, syncs membership, and GRANT/REVOKE.

    Args:
        admin: Signed-in app admin.
        container: Service container.
        body: Optional mode (defaults to dry_run).

    Returns:
        The finished run.
    """
    mode = (body.mode if body is not None else "dry_run")
    return container.data_access.run_now(admin, mode=mode)

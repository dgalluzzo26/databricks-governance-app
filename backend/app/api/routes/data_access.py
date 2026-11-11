"""Admin API for data-access group settings, readiness, and sync runs."""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container
from backend.app.models.data_access import (
    RUN_HISTORY_PAGE_SIZE_DEFAULT,
    RUN_HISTORY_PAGE_SIZE_MAX,
    DataAccessReadiness,
    DataAccessRun,
    DataAccessRunList,
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


@router.get("/runs", response_model=DataAccessRunList)
def list_runs(
    _: Admin,
    container: Container,
    mode: str | None = Query(default=None, description="dry_run or apply"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=RUN_HISTORY_PAGE_SIZE_DEFAULT,
        ge=1,
        le=RUN_HISTORY_PAGE_SIZE_MAX,
    ),
) -> DataAccessRunList:
    """List syncs for the last 30 days, newest first (summaries only).

    Requires app admin. Filter by mode so Dry-run and Live tabs stay separate.

    Args:
        container: Service container.
        mode: Optional ``dry_run`` or ``apply``.
        page: 1-based page.
        page_size: Rows per page.

    Returns:
        Paginated runs without full plans.
    """
    return container.data_access.list_runs(mode=mode, page=page, page_size=page_size)


@router.get("/runs/latest", response_model=DataAccessRun | None)
def latest_run(
    _: Admin,
    container: Container,
    mode: str | None = Query(default=None, description="dry_run or apply"),
) -> DataAccessRun | None:
    """Return the most recent sync including its truncated plan.

    Requires app admin.

    Args:
        container: Service container.
        mode: Optional ``dry_run`` or ``apply`` filter.

    Returns:
        The latest matching run, or null when none exist.
    """
    return container.data_access.latest_run(mode=mode)


@router.get("/runs/{run_id}", response_model=DataAccessRun)
def get_run(_: Admin, container: Container, run_id: str) -> DataAccessRun:
    """Return one sync including its truncated plan.

    Requires app admin. Used when an operator clicks a row in Dry-run or Live logs.

    Args:
        container: Service container.
        run_id: Identifier of the stored run.

    Returns:
        The run with sampled plan lists.

    Raises:
        NotFoundError: If the run is unknown or older than the retention window.
    """
    return container.data_access.get_run(run_id)


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
    mode = body.mode if body is not None else "dry_run"
    return container.data_access.run_now(admin, mode=mode)

"""Health monitor: request latency summaries, slow-request pages, and current resource sizing."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.models.performance import (
    CapacityReport,
    CapacitySetting,
    PerformanceSummary,
    RecommendationTarget,
    SlowRequestPage,
)
from backend.app.repositories.base import StorageProvider
from backend.app.services.performance_analytics import build_summary, slow_requests

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24
MAX_HOURS = 24 * 14
SLOW_PAGE_SIZE_DEFAULT = 25
SLOW_PAGE_SIZE_MAX = 100


class PerformanceService:
    """Read-side of the request timing data plus live sizing of app, Lakebase, and warehouse.

    Args:
        storage: Storage provider holding the request metrics.
        workspace: Factory for the Account Admin SP client (used for sizing reads).
        config: App configuration (app name, warehouse id, Lakebase endpoint).
    """

    def __init__(
        self, storage: StorageProvider, workspace: WorkspaceClientFactory, config: AppConfig
    ) -> None:
        self._storage = storage
        self._workspace = workspace
        self._config = config

    @staticmethod
    def clamp_hours(hours: int | None) -> int:
        """Clamp the window to 1 hour .. 14 days."""
        if hours is None:
            return DEFAULT_HOURS
        return min(max(1, hours), MAX_HOURS)

    def _window(self, hours: int) -> tuple[datetime, list]:
        now = datetime.now(UTC)
        return now, self._storage.performance.list_since(now - timedelta(hours=hours))

    def summary(self, hours: int | None = None) -> PerformanceSummary:
        """Latency KPIs, per-activity stats, hourly roll-up, and recommendations for the window."""
        window = self.clamp_hours(hours)
        now, metrics = self._window(window)
        return build_summary(metrics, now=now, hours=window)

    def slow_requests(
        self, *, hours: int | None = None, page: int = 1, page_size: int = SLOW_PAGE_SIZE_DEFAULT
    ) -> SlowRequestPage:
        """One page of slow requests, newest first."""
        window = self.clamp_hours(hours)
        _, metrics = self._window(window)
        rows = slow_requests(metrics)
        size = min(max(1, page_size), SLOW_PAGE_SIZE_MAX)
        start = (max(1, page) - 1) * size
        return SlowRequestPage(
            items=rows[start : start + size],
            total=len(rows),
            page=max(1, page),
            page_size=size,
        )

    def capacity(self) -> CapacityReport:
        """Current sizing of the app, Lakebase endpoint, and SQL warehouse.

        Reads use the Account Admin SP (account and workspace admin). Each resource fails
        independently, so one unreadable setting does not hide the others.
        """
        return CapacityReport(
            settings=[
                self._app_setting(),
                self._lakebase_setting(),
                self._warehouse_setting(),
            ]
        )

    def _client(self):  # noqa: ANN202 - WorkspaceClient
        if self._workspace.admin_configured:
            return self._workspace.admin()
        return self._workspace.app()

    def _app_setting(self) -> CapacitySetting:
        name = self._config.app_name
        try:
            app = self._client().apps.get(name=name)
            return CapacitySetting(
                target=RecommendationTarget.APP,
                name=name,
                size=str(app.compute_size) if app.compute_size else None,
                detail="App compute size",
            )
        except Exception as exc:  # noqa: BLE001 - shown to the admin, not raised
            logger.warning("Could not read app compute size: %s", exc)
            return CapacitySetting(
                target=RecommendationTarget.APP, name=name, available=False, detail=str(exc)
            )

    def _lakebase_setting(self) -> CapacitySetting:
        endpoint = self._config.lakebase_endpoint
        if not endpoint:
            return CapacitySetting(
                target=RecommendationTarget.LAKEBASE,
                name="Lakebase",
                available=False,
                detail="LAKEBASE_ENDPOINT is not configured",
            )
        try:
            found = self._client().postgres.get_endpoint(name=endpoint)
            status = found.status
            low = getattr(status, "autoscaling_limit_min_cu", None)
            high = getattr(status, "autoscaling_limit_max_cu", None)
            state = getattr(getattr(status, "current_state", None), "value", None)
            return CapacitySetting(
                target=RecommendationTarget.LAKEBASE,
                name=endpoint.rsplit("/", 1)[-1],
                size=f"{low:g}–{high:g} CU" if low is not None and high is not None else None,
                autoscaling=f"{low:g} to {high:g} CU" if low is not None and high is not None else None,
                detail=f"State: {state}" if state else "Autoscaling endpoint",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read Lakebase endpoint sizing: %s", exc)
            return CapacitySetting(
                target=RecommendationTarget.LAKEBASE, name="Lakebase", available=False, detail=str(exc)
            )

    def _warehouse_setting(self) -> CapacitySetting:
        warehouse_id = self._config.warehouse_id
        if not warehouse_id:
            return CapacitySetting(
                target=RecommendationTarget.WAREHOUSE,
                name="SQL warehouse",
                available=False,
                detail="DATABRICKS_WAREHOUSE_ID is not configured",
            )
        try:
            wh = self._client().warehouses.get(id=warehouse_id)
            clusters = (
                f"{wh.min_num_clusters}–{wh.max_num_clusters} clusters"
                if wh.min_num_clusters is not None and wh.max_num_clusters is not None
                else None
            )
            state = wh.state.value if wh.state else "unknown"
            return CapacitySetting(
                target=RecommendationTarget.WAREHOUSE,
                name=wh.name or warehouse_id,
                size=wh.cluster_size,
                autoscaling=clusters,
                detail=f"State: {state}, auto-stop {wh.auto_stop_mins} min",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read warehouse sizing: %s", exc)
            return CapacitySetting(
                target=RecommendationTarget.WAREHOUSE,
                name="SQL warehouse",
                available=False,
                detail=str(exc),
            )

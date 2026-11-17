"""Health monitor: request latency summaries, slow-request pages, and current resource sizing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.models.performance import (
    CapacityReport,
    CapacitySetting,
    PerformanceSummary,
    RecommendationTarget,
    SlowRequest,
    SlowRequestPage,
)
from backend.app.models.task import RequestPage, RequestRecord
from backend.app.repositories.base import StorageProvider
from backend.app.services.performance_analytics import build_summary, slow_requests
from backend.app.services.table_query import TableQuery, sort_and_filter

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24
MAX_HOURS = 24 * 14
SLOW_PAGE_SIZE_DEFAULT = 25
SLOW_PAGE_SIZE_MAX = 100


# In-memory readers for the slow-request table. Keys match the frontend column keys.
SLOW_REQUEST_READERS: dict[str, Callable[[SlowRequest], Any]] = {
    "when": lambda item: item.recorded_at,
    "activity": lambda item: f"{item.activity} {item.method} {item.route}",
    "status": lambda item: item.status_code,
    "total": lambda item: item.duration_ms,
    "expected": lambda item: item.expected_ms,
    "warehouse": lambda item: item.warehouse_ms,
    "lakebase": lambda item: item.lakebase_ms,
    "app": lambda item: item.app_ms,
    "user": lambda item: item.user_name,
}

# In-memory readers for the request history table. Keys match the frontend column keys.
REQUEST_READERS: dict[str, Callable[[RequestRecord], Any]] = {
    "when": lambda item: item.recorded_at,
    "activity": lambda item: f"{item.activity} {item.method} {item.route}",
    "status": lambda item: item.status_code,
    "total": lambda item: item.duration_ms,
    "user": lambda item: item.user_name,
    "error": lambda item: f"{item.error_code or ''} {item.error_message or ''}".strip(),
    "request": lambda item: item.request_id,
}

REQUEST_STATUS_FILTERS = ("all", "succeeded", "failed")


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
        self,
        *,
        hours: int | None = None,
        page: int = 1,
        page_size: int = SLOW_PAGE_SIZE_DEFAULT,
        table_query: TableQuery | None = None,
    ) -> SlowRequestPage:
        """One page of slow requests, newest first unless sorted.

        Args:
            hours: Window length in hours.
            page: 1-based page.
            page_size: Rows per page.
            table_query: Optional column sort and filters applied before paging.

        Returns:
            The page and total.
        """
        window = self.clamp_hours(hours)
        _, metrics = self._window(window)
        rows = sort_and_filter(slow_requests(metrics), table_query or TableQuery(), SLOW_REQUEST_READERS)
        size = min(max(1, page_size), SLOW_PAGE_SIZE_MAX)
        start = (max(1, page) - 1) * size
        return SlowRequestPage(
            items=rows[start : start + size],
            total=len(rows),
            page=max(1, page),
            page_size=size,
        )

    def requests_page(
        self,
        *,
        hours: int | None = None,
        status: str = "all",
        page: int = 1,
        page_size: int = SLOW_PAGE_SIZE_DEFAULT,
        table_query: TableQuery | None = None,
    ) -> RequestPage:
        """One page of every user's requests, newest first, with success and failure counts.

        Args:
            hours: Window length in hours.
            status: ``all``, ``succeeded`` (status below 400), or ``failed`` (400 and above).
            page: 1-based page.
            page_size: Rows per page.
            table_query: Optional column sort and filters applied before paging.

        Returns:
            The page; ``succeeded`` and ``failed`` count the whole window.
        """
        window = self.clamp_hours(hours)
        _, metrics = self._window(window)
        records = [RequestRecord.from_metric(m) for m in reversed(metrics)]
        succeeded = sum(1 for r in records if r.ok)
        failed = len(records) - succeeded
        wanted = status if status in REQUEST_STATUS_FILTERS else "all"
        if wanted != "all":
            records = [r for r in records if r.ok == (wanted == "succeeded")]
        rows = sort_and_filter(records, table_query or TableQuery(), REQUEST_READERS)
        size = min(max(1, page_size), SLOW_PAGE_SIZE_MAX)
        start = (max(1, page) - 1) * size
        return RequestPage(
            items=rows[start : start + size],
            total=len(rows),
            page=max(1, page),
            page_size=size,
            succeeded=succeeded,
            failed=failed,
        )

    def requests_for_user(
        self, user_name: str, *, hours: int = 24, failed_only: bool = True, limit: int = 30
    ) -> RequestPage:
        """The signed-in user's recent requests (failures by default) for the activity panel.

        Args:
            user_name: The caller.
            hours: Window length in hours.
            failed_only: When True, only failed requests.
            limit: Most rows to return.

        Returns:
            The requests, newest first.
        """
        window = self.clamp_hours(hours)
        since = datetime.now(UTC) - timedelta(hours=window)
        cap = min(max(1, limit), SLOW_PAGE_SIZE_MAX)
        metrics = self._storage.performance.list_for_user(
            user_name, since, failed_only=failed_only, limit=cap
        )
        records = [RequestRecord.from_metric(m) for m in metrics]
        failed = sum(1 for r in records if not r.ok)
        return RequestPage(
            items=records,
            total=len(records),
            page=1,
            page_size=cap,
            succeeded=len(records) - failed,
            failed=failed,
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

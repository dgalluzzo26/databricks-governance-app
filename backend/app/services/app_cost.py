"""App cost report: Databricks Apps compute per day, and warehouse plus job cost per sync.

Reads ``system.billing.usage`` joined to ``system.billing.list_prices`` through the Account
Admin SP, which is a billing admin. Three billing sources are used:

- ``APPS`` usage with this app's ``app_id``: always-on compute, reported per day.
- ``SQL`` usage on the app's dedicated warehouse and ``JOBS`` usage on the sync job. These
  count toward a sync only inside that sync's start and finish window, pro-rated by time.
  Hourly warehouse records are split in proportion to the overlap.

Costs are list prices, not invoice amounts. Billing data lags behind real usage, so today's
figure is partial. Successful reports are cached; failures are not.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.models.app_cost import AppCostDay, AppCostReport
from backend.app.models.data_access import DataAccessRun

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_DAYS = 30  # Matches the sync-run retention window, which bounds the per-sync sample.
CACHE_SECONDS = 900
LAG_NOTE = (
    "Billing data lags real usage by up to about a day, so today's figures are partial. "
    "Amounts use Databricks list prices, not your invoice."
)

# Records for the app's compute, the dedicated warehouse, and the sync job. A filter whose ID is
# NULL matches nothing, so an unset job ID drops only job cost. Quantities are DBUs.
_USAGE_SQL = """
SELECT
  u.usage_start_time,
  u.usage_end_time,
  u.usage_date,
  u.billing_origin_product AS origin,
  u.usage_quantity * p.pricing.default AS usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON p.sku_name = u.sku_name
 AND p.cloud = u.cloud
 AND u.usage_end_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_end_time < p.price_end_time)
WHERE u.usage_date >= :since
  AND u.usage_unit = 'DBU'
  AND (
        (u.billing_origin_product = 'APPS' AND u.usage_metadata.app_id = :app_id)
     OR (u.billing_origin_product = 'SQL' AND u.usage_metadata.warehouse_id = :warehouse_id)
     OR (u.billing_origin_product = 'JOBS' AND u.usage_metadata.job_id = :job_id)
  )
"""

_SOURCES = ("APPS", "SQL", "JOBS")


class _RunSource(Protocol):
    """The part of the data-access service the cost report needs."""

    def finished_runs_since(self, since: datetime) -> list[DataAccessRun]:
        """Finished sync runs that started at or after ``since``."""
        ...


@dataclass(frozen=True)
class _UsageRecord:
    """One priced billing record (DBUs times list price, USD)."""

    start: datetime
    end: datetime
    usage_date: date
    origin: str
    usd: float | None


def _parse_timestamp(value: Any) -> datetime:
    """Parse a Statement Execution timestamp as UTC (values are JSON strings)."""
    text = str(value).replace("Z", "+00:00").replace(" ", "T", 1)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _overlap_fraction(start: datetime, end: datetime, run_start: datetime, run_end: datetime) -> float:
    """Share of a billing record's span that falls inside a sync run.

    A zero-length record counts fully when its instant lies inside the run.
    """
    span = (end - start).total_seconds()
    if span <= 0:
        return 1.0 if run_start <= start < run_end else 0.0
    overlap = (min(end, run_end) - max(start, run_start)).total_seconds()
    return max(0.0, overlap) / span


def sync_usd(records: list[_UsageRecord], runs: list[DataAccessRun]) -> float:
    """Warehouse and job cost that falls inside the given sync runs, in USD.

    Args:
        records: Priced billing records (only ``SQL`` and ``JOBS`` count toward syncs).
        runs: Finished sync runs; each run's start and finish bound its window.

    Returns:
        The pro-rated USD total across all runs.
    """
    windows = [(run.started_at, run.finished_at) for run in runs if run.finished_at is not None]
    total = 0.0
    for record in records:
        if record.origin not in ("SQL", "JOBS") or record.usd is None:
            continue
        for run_start, run_end in windows:
            total += record.usd * _overlap_fraction(record.start, record.end, run_start, run_end)
    return total


class AppCostService:
    """Builds the app cost report from billing system tables and sync run records.

    Args:
        sql: Executor bound to the Account Admin SP (billing reads) and the dedicated warehouse.
        workspace: Factory used to look up this app's ID through the Apps API.
        config: App configuration (app name, warehouse ID, sync job ID).
        runs: Data-access service that supplies finished sync runs.
    """

    def __init__(
        self,
        sql: SqlExecutor,
        workspace: WorkspaceClientFactory,
        config: AppConfig,
        runs: _RunSource,
    ) -> None:
        self._sql = sql
        self._workspace = workspace
        self._config = config
        self._runs = runs
        self._cache: TtlCache = TtlCache(CACHE_SECONDS, maxsize=8)
        self._app_id: str | None = None

    @staticmethod
    def clamp_days(days: int | None) -> int:
        """Clamp the window to 1 .. 30 days."""
        if days is None:
            return DEFAULT_DAYS
        return min(max(1, days), MAX_DAYS)

    def report(self, days: int | None = None) -> AppCostReport:
        """Cost for the last ``days`` UTC days, with per-sync averages.

        Failures are returned as ``available=False`` rather than raised, and are not cached, so
        the next request retries.

        Args:
            days: Window length in days (clamped to 1–30).

        Returns:
            The report.
        """
        window = self.clamp_days(days)
        try:
            return self._cache.get_or_load(window, lambda: self._build(window))
        except Exception as exc:  # noqa: BLE001 - shown to the admin, not raised
            logger.warning("App cost report unavailable: %s", exc)
            return AppCostReport(
                available=False,
                detail=str(exc),
                app_name=self._config.app_name,
                dedicated_warehouse_id=self._config.warehouse_id,
                days=window,
                generated_at=datetime.now(UTC),
                lag_note=LAG_NOTE,
            )

    def _resolve_app_id(self) -> str:
        """The app's ID (also its service principal's application ID), cached after one lookup."""
        if self._app_id is None:
            client = self._workspace.admin() if self._workspace.admin_configured else self._workspace.app()
            app = client.apps.get(name=self._config.app_name)
            if not app.id:
                raise RuntimeError(f"Databricks App {self._config.app_name} has no id")
            self._app_id = app.id
        return self._app_id

    def _usage(self, since: date, app_id: str) -> list[_UsageRecord]:
        rows = self._sql.query(
            _USAGE_SQL,
            {
                "since": since.isoformat(),
                "app_id": app_id,
                "warehouse_id": self._config.warehouse_id,
                "job_id": self._config.data_access_sync_job_id,
            },
        )
        records: list[_UsageRecord] = []
        for row in rows:
            usd = row.get("usd")
            records.append(
                _UsageRecord(
                    start=_parse_timestamp(row["usage_start_time"]),
                    end=_parse_timestamp(row["usage_end_time"]),
                    usage_date=date.fromisoformat(str(row["usage_date"])[:10]),
                    origin=str(row["origin"]),
                    usd=float(usd) if usd not in (None, "") else None,
                )
            )
        return records

    def _build(self, days: int) -> AppCostReport:
        now = datetime.now(UTC)
        today = now.date()
        since = today - timedelta(days=days - 1)
        records = self._usage(since, self._resolve_app_id())
        runs = self._runs.finished_runs_since(datetime.combine(since, time.min, tzinfo=UTC))

        by_day: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        unpriced = 0
        for record in records:
            if record.origin not in _SOURCES:
                continue
            if record.usd is None:
                unpriced += 1
                continue
            by_day[record.usage_date][record.origin] += record.usd

        app_by_day = {d: totals["APPS"] for d, totals in by_day.items() if totals.get("APPS")}
        week_start = today - timedelta(days=6)
        sync_total = sync_usd(records, runs)
        daily: list[AppCostDay] = []
        for offset in range(days):
            day = since + timedelta(days=offset)
            totals = by_day.get(day, {})
            daily.append(
                AppCostDay(
                    usage_date=day,
                    app_usd=round(totals.get("APPS", 0.0), 4),
                    warehouse_usd=round(totals.get("SQL", 0.0), 4),
                    job_usd=round(totals.get("JOBS", 0.0), 4),
                )
            )
        return AppCostReport(
            app_name=self._config.app_name,
            dedicated_warehouse_id=self._config.warehouse_id,
            days=days,
            generated_at=now,
            today_app_usd=round(app_by_day.get(today, 0.0), 4),
            app_usd_last_7_days=round(sum(usd for d, usd in app_by_day.items() if d >= week_start), 4),
            app_usd_per_active_day=(
                round(sum(app_by_day.values()) / len(app_by_day), 4) if app_by_day else None
            ),
            sync_count=len(runs),
            sync_usd=round(sync_total, 4),
            avg_usd_per_sync=round(sync_total / len(runs), 4) if runs else None,
            unpriced_records=unpriced,
            lag_note=LAG_NOTE,
            daily=daily,
        )

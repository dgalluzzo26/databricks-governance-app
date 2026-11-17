"""Usage analytics service: overview KPIs, top tables/users, sensitive column stats, and drilldown."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from typing import Any

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.usage_analytics import (
    UsageColumnSummary,
    UsageDailyPoint,
    UsageKpis,
    UsageLastRun,
    UsageOverview,
    UsageRecentReadEvent,
    UsageRefreshResponse,
    UsageSensitiveSummary,
    UsageTableDetail,
    UsageTableList,
    UsageTableSummary,
    UsageUserList,
    UsageUserSummary,
    UsageUserTableSummary,
)
from backend.app.models.user import CurrentUser
from backend.app.services.usage_rollup import USAGE_SCHEMA, UsageTables

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_DAYS = 90
CACHE_SECONDS = 60


def clamp_days(days: int | None) -> int:
    """Clamp the requested window to 1..90 days."""
    if days is None:
        return DEFAULT_DAYS
    return min(max(1, days), MAX_DAYS)


class UsageAnalyticsService:
    """Queries `{app_catalog}.usage` summary tables for data-access usage insights.

    Args:
        sql: Warehouse executor running queries.
        workspace: Workspace client factory to trigger the usage rollup job.
        config: Application configuration.
        cache: Optional TTL cache for query results.
    """

    def __init__(
        self,
        sql: SqlExecutor,
        workspace: WorkspaceClientFactory,
        config: AppConfig,
        cache: TtlCache | None = None,
    ) -> None:
        self._sql = sql
        self._workspace = workspace
        self._config = config
        self._cache = cache or TtlCache(CACHE_SECONDS, maxsize=64)
        self._tables = UsageTables(
            catalog=config.app_catalog,
            schema=USAGE_SCHEMA,
            identity_table=f"`{config.app_catalog}`.`{config.app_schema}`.`identity_attributes`",
        )

    def overview(self, days: int | None = None) -> UsageOverview:
        """Return KPIs, trends, top tables, and sensitive columns for the time window."""
        window = clamp_days(days)
        return self._cache.get_or_load(
            ("usage-overview", window),
            lambda: self._build_overview(window),
        )

    def _build_overview(self, days: int) -> UsageOverview:
        now = datetime.now(UTC)
        try:
            last_run = self._load_last_run()
            kpis = self._load_kpis(days)
            daily_trends = self._load_daily_trends(days)
            top_tables = self._load_top_tables(days, limit=10)
            top_users = self._load_top_users(days, limit=10)
            sensitive = self._load_sensitive_breakdown(days)
            return UsageOverview(
                available=True,
                days=days,
                generated_at=now,
                kpis=kpis,
                last_run=last_run,
                daily_trends=daily_trends,
                top_tables=top_tables,
                top_users=top_users,
                sensitive_breakdown=sensitive,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Usage overview unavailable: %s", exc)
            return UsageOverview(
                available=False,
                detail=str(exc),
                days=days,
                generated_at=now,
                kpis=UsageKpis(),
            )

    def list_tables(self, days: int | None = None, q: str | None = None, limit: int = 50) -> UsageTableList:
        """List tables accessed during the time window, with optional search."""
        window = clamp_days(days)
        cap = max(1, min(limit, 200))
        needle = (q or "").strip().lower()
        items = self._load_top_tables(window, limit=cap, search=needle or None)
        return UsageTableList(tables=items, total=len(items))

    def list_users(self, days: int | None = None, q: str | None = None, limit: int = 50) -> UsageUserList:
        """List users who accessed tables during the time window, with optional search."""
        window = clamp_days(days)
        cap = max(1, min(limit, 200))
        needle = (q or "").strip().lower()
        items = self._load_top_users(window, limit=cap, search=needle or None)
        return UsageUserList(users=items, total=len(items))

    def table_detail(self, table_full_name: str, days: int | None = None) -> UsageTableDetail:
        """Drilldown view for a single table: users, columns, and recent reads."""
        window = clamp_days(days)
        name = table_full_name.strip()
        if not name:
            raise InvalidRequestError("table_full_name is required")
        return self._cache.get_or_load(
            ("usage-table-detail", name, window),
            lambda: self._build_table_detail(name, window),
        )

    def _build_table_detail(self, table_full_name: str, days: int) -> UsageTableDetail:
        params = {"table": table_full_name, "days": days}
        user_rows = self._sql.query(
            f"""
            SELECT user_name, sum(reads) AS reads, max(last_read_at) AS last_read_at
            FROM {self._tables.fqn('user_table_usage_daily')}
            WHERE table_full_name = :table
              AND event_date >= date_sub(current_date(), :days)
            GROUP BY user_name
            ORDER BY reads DESC
            """,
            params,
        )
        users = [
            UsageUserTableSummary(
                user_name=str(r.get("user_name") or ""),
                reads=int(r.get("reads") or 0),
                last_read_at=str(r.get("last_read_at") or "") or None,
            )
            for r in user_rows
        ]

        col_rows = self._sql.query(
            f"""
            SELECT column_name,
                   max(sensitivity_path) AS sensitivity_path,
                   max(access_mode) AS access_mode,
                   sum(reads) AS reads,
                   count(distinct user_name) AS distinct_users,
                   max(last_read_at) AS last_read_at
            FROM {self._tables.fqn('column_usage_daily')}
            WHERE table_full_name = :table
              AND event_date >= date_sub(current_date(), :days)
            GROUP BY column_name
            ORDER BY reads DESC
            """,
            params,
        )
        columns = [
            UsageColumnSummary(
                column_name=str(r.get("column_name") or ""),
                sensitivity_path=str(r.get("sensitivity_path") or "") or None,
                access_mode=str(r.get("access_mode") or "not_sensitive"),
                reads=int(r.get("reads") or 0),
                distinct_users=int(r.get("distinct_users") or 0),
                last_read_at=str(r.get("last_read_at") or "") or None,
            )
            for r in col_rows
        ]

        recent_rows = self._sql.query(
            f"""
            SELECT event_time, user_name, entity_type, direct_access
            FROM {self._tables.fqn('read_events')}
            WHERE table_full_name = :table
              AND event_date >= date_sub(current_date(), :days)
            ORDER BY event_time DESC
            LIMIT 25
            """,
            params,
        )
        recent = [
            UsageRecentReadEvent(
                event_time=str(r.get("event_time") or ""),
                user_name=str(r.get("user_name") or ""),
                entity_type=str(r.get("entity_type") or "") or None,
                direct_access=bool(r.get("direct_access", True)),
            )
            for r in recent_rows
        ]

        total_reads = sum(u.reads for u in users)
        distinct_users = len(users)
        last_read = max((u.last_read_at for u in users if u.last_read_at), default=None)

        return UsageTableDetail(
            table_full_name=table_full_name,
            days=days,
            reads=total_reads,
            distinct_users=distinct_users,
            last_read_at=last_read,
            users=users,
            columns=columns,
            recent_reads=recent,
        )

    def refresh(self, actor: CurrentUser) -> UsageRefreshResponse:
        """Trigger an on-demand run of the usage rollup Job."""
        job_id_raw = (self._config.usage_rollup_job_id or "").strip()
        if not job_id_raw:
            raise InvalidRequestError("USAGE_ROLLUP_JOB_ID is not configured for this deployment")
        try:
            job_id = int(job_id_raw)
        except ValueError as exc:
            raise InvalidRequestError(f"USAGE_ROLLUP_JOB_ID must be numeric, got {job_id_raw!r}") from exc

        client = self._workspace.app()
        try:
            res = client.jobs.run_now(
                job_id=job_id,
                job_parameters={
                    "trigger": "user",
                    "actor": actor.user_name,
                },
            )
            run_id = str(getattr(res, "run_id", "") or getattr(res, "number_in_job", "") or "")
            self._cache.invalidate()
            return UsageRefreshResponse(
                started=True,
                message=f"Usage rollup job started (run {run_id or 'queued'})",
                job_id=str(job_id),
                run_id=run_id or None,
            )
        except Exception as exc:
            logger.exception("Failed to trigger usage rollup job %s", job_id)
            raise UpstreamError(f"Failed to trigger usage rollup job: {exc}") from exc

    def export_csv(self, kind: str, days: int | None = None) -> str:
        """Build a CSV string for 'tables', 'users', or 'sensitive' usage."""
        window = clamp_days(days)
        out = io.StringIO()
        writer = csv.writer(out)

        normalized = (kind or "tables").strip().lower()
        if normalized == "tables":
            writer.writerow(["Table", "Catalog", "Schema", "Name", "Reads", "Distinct Users", "Last Read At"])
            for t in self._load_top_tables(window, limit=500):
                writer.writerow([
                    t.table_full_name,
                    t.catalog_name,
                    t.schema_name,
                    t.table_name,
                    t.reads,
                    t.distinct_users,
                    t.last_read_at or "",
                ])
        elif normalized == "users":
            writer.writerow(["User", "Reads", "Distinct Tables", "Last Read At"])
            for u in self._load_top_users(window, limit=500):
                writer.writerow([u.user_name, u.reads, u.distinct_tables, u.last_read_at or ""])
        elif normalized == "sensitive":
            writer.writerow([
                "Sensitivity Path",
                "Reads",
                "Clear Reads",
                "Masked Reads",
                "Exempt Reads",
                "Distinct Users",
                "Distinct Tables",
            ])
            for s in self._load_sensitive_breakdown(window):
                writer.writerow([
                    s.sensitivity_path,
                    s.reads,
                    s.clear_reads,
                    s.masked_reads,
                    s.exempt_reads,
                    s.distinct_users,
                    s.distinct_tables,
                ])
        else:
            msg = f"Unknown export kind '{kind}'. Expected 'tables', 'users', or 'sensitive'."
            raise InvalidRequestError(msg)

        return out.getvalue()

    def _load_last_run(self) -> UsageLastRun | None:
        rows = self._sql.query(
            f"""
            SELECT run_id, status, finished_at, data_as_of, window_start, window_end, read_events, catalogs
            FROM {self._tables.fqn('rollup_runs')}
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        if not rows:
            return None
        r = rows[0]
        raw_cats = str(r.get("catalogs") or "")
        catalogs = [c.strip() for c in raw_cats.split(",") if c.strip()]
        return UsageLastRun(
            run_id=str(r.get("run_id") or ""),
            status=str(r.get("status") or "unknown"),
            finished_at=str(r.get("finished_at") or "") or None,
            data_as_of=str(r.get("data_as_of") or "") or None,
            window_start=str(r.get("window_start") or "") or None,
            window_end=str(r.get("window_end") or "") or None,
            read_events=int(r.get("read_events") or 0),
            catalogs=catalogs,
        )

    def _load_kpis(self, days: int) -> UsageKpis:
        params = {"days": days}
        table_rows = self._sql.query(
            f"""
            SELECT coalesce(sum(reads), 0) AS total_reads,
                   count(distinct user_name) AS active_users,
                   count(distinct table_full_name) AS tables_accessed
            FROM {self._tables.fqn('user_table_usage_daily')}
            WHERE event_date >= date_sub(current_date(), :days)
            """,
            params,
        )
        total_reads = int((table_rows[0].get("total_reads") if table_rows else 0) or 0)
        active_users = int((table_rows[0].get("active_users") if table_rows else 0) or 0)
        tables_accessed = int((table_rows[0].get("tables_accessed") if table_rows else 0) or 0)

        col_rows = self._sql.query(
            f"""
            SELECT coalesce(sum(reads), 0) AS sensitive_reads,
                   coalesce(sum(case when access_mode = 'clear' then reads else 0 end), 0) AS clear_reads,
                   coalesce(sum(case when access_mode = 'masked' then reads else 0 end), 0) AS masked_reads,
                   coalesce(sum(case when access_mode = 'exempt' then reads else 0 end), 0) AS exempt_reads
            FROM {self._tables.fqn('column_usage_daily')}
            WHERE event_date >= date_sub(current_date(), :days)
              AND access_mode <> 'not_sensitive'
            """,
            params,
        )
        sensitive_reads = int((col_rows[0].get("sensitive_reads") if col_rows else 0) or 0)
        clear_reads = int((col_rows[0].get("clear_reads") if col_rows else 0) or 0)
        masked_reads = int((col_rows[0].get("masked_reads") if col_rows else 0) or 0)
        exempt_reads = int((col_rows[0].get("exempt_reads") if col_rows else 0) or 0)

        return UsageKpis(
            total_table_reads=total_reads,
            active_users=active_users,
            tables_accessed=tables_accessed,
            sensitive_column_reads=sensitive_reads,
            clear_reads=clear_reads,
            masked_reads=masked_reads,
            exempt_reads=exempt_reads,
        )

    def _load_daily_trends(self, days: int) -> list[UsageDailyPoint]:
        params = {"days": days}
        rows = self._sql.query(
            f"""
            WITH table_days AS (
              SELECT event_date, sum(reads) AS reads, count(distinct user_name) AS users
              FROM {self._tables.fqn('user_table_usage_daily')}
              WHERE event_date >= date_sub(current_date(), :days)
              GROUP BY event_date
            ),
            col_days AS (
              SELECT event_date,
                sum(case when access_mode = 'clear' then reads else 0 end) AS clear_reads,
                sum(case when access_mode = 'masked' then reads else 0 end) AS masked_reads
              FROM {self._tables.fqn('column_usage_daily')}
              WHERE event_date >= date_sub(current_date(), :days)
                AND access_mode <> 'not_sensitive'
              GROUP BY event_date
            )
            SELECT t.event_date, t.reads, t.users,
                   coalesce(c.clear_reads, 0) AS clear_reads,
                   coalesce(c.masked_reads, 0) AS masked_reads
            FROM table_days t
            LEFT JOIN col_days c ON c.event_date = t.event_date
            ORDER BY t.event_date ASC
            """,
            params,
        )
        return [
            UsageDailyPoint(
                date=str(r.get("event_date") or ""),
                reads=int(r.get("reads") or 0),
                distinct_users=int(r.get("users") or 0),
                clear_reads=int(r.get("clear_reads") or 0),
                masked_reads=int(r.get("masked_reads") or 0),
            )
            for r in rows
        ]

    def _load_top_tables(
        self, days: int, limit: int = 10, search: str | None = None
    ) -> list[UsageTableSummary]:
        params: dict[str, Any] = {"days": days}
        search_clause = ""
        if search:
            params["search"] = f"%{search.lower()}%"
            search_clause = "AND lower(u.table_full_name) LIKE :search"

        rows = self._sql.query(
            f"""
            SELECT u.table_full_name, u.catalog_name,
                   element_at(split(u.table_full_name, '\\\\.'), 2) AS schema_name,
                   element_at(split(u.table_full_name, '\\\\.'), 3) AS table_name,
                   sum(u.reads) AS reads,
                   count(distinct u.user_name) AS distinct_users,
                   max(u.last_read_at) AS last_read_at
            FROM {self._tables.fqn('user_table_usage_daily')} u
            WHERE u.event_date >= date_sub(current_date(), :days)
              {search_clause}
            GROUP BY u.table_full_name, u.catalog_name
            ORDER BY reads DESC
            LIMIT {int(limit)}
            """,
            params,
        )
        return [
            UsageTableSummary(
                table_full_name=str(r.get("table_full_name") or ""),
                catalog_name=str(r.get("catalog_name") or ""),
                schema_name=str(r.get("schema_name") or ""),
                table_name=str(r.get("table_name") or ""),
                reads=int(r.get("reads") or 0),
                distinct_users=int(r.get("distinct_users") or 0),
                last_read_at=str(r.get("last_read_at") or "") or None,
            )
            for r in rows
        ]

    def _load_top_users(
        self, days: int, limit: int = 10, search: str | None = None
    ) -> list[UsageUserSummary]:
        params: dict[str, Any] = {"days": days}
        search_clause = ""
        if search:
            params["search"] = f"%{search.lower()}%"
            search_clause = "AND lower(user_name) LIKE :search"

        rows = self._sql.query(
            f"""
            SELECT user_name,
                   sum(reads) AS reads,
                   count(distinct table_full_name) AS distinct_tables,
                   max(last_read_at) AS last_read_at
            FROM {self._tables.fqn('user_table_usage_daily')} u
            WHERE event_date >= date_sub(current_date(), :days)
              {search_clause}
            GROUP BY user_name
            ORDER BY reads DESC
            LIMIT {int(limit)}
            """,
            params,
        )
        return [
            UsageUserSummary(
                user_name=str(r.get("user_name") or ""),
                reads=int(r.get("reads") or 0),
                distinct_tables=int(r.get("distinct_tables") or 0),
                last_read_at=str(r.get("last_read_at") or "") or None,
            )
            for r in rows
        ]

    def _load_sensitive_breakdown(self, days: int) -> list[UsageSensitiveSummary]:
        params = {"days": days}
        rows = self._sql.query(
            f"""
            SELECT sensitivity_path,
                   sum(reads) AS reads,
                   sum(case when access_mode = 'clear' then reads else 0 end) AS clear_reads,
                   sum(case when access_mode = 'masked' then reads else 0 end) AS masked_reads,
                   sum(case when access_mode = 'exempt' then reads else 0 end) AS exempt_reads,
                   count(distinct user_name) AS distinct_users,
                   count(distinct table_full_name) AS distinct_tables
            FROM {self._tables.fqn('column_usage_daily')}
            WHERE event_date >= date_sub(current_date(), :days)
              AND access_mode <> 'not_sensitive'
              AND sensitivity_path IS NOT NULL
            GROUP BY sensitivity_path
            ORDER BY reads DESC
            """,
            params,
        )
        return [
            UsageSensitiveSummary(
                sensitivity_path=str(r.get("sensitivity_path") or ""),
                reads=int(r.get("reads") or 0),
                clear_reads=int(r.get("clear_reads") or 0),
                masked_reads=int(r.get("masked_reads") or 0),
                exempt_reads=int(r.get("exempt_reads") or 0),
                distinct_users=int(r.get("distinct_users") or 0),
                distinct_tables=int(r.get("distinct_tables") or 0),
            )
            for r in rows
        ]

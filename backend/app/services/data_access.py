"""Data-access group sync: settings, Account Admin SP readiness, dry-run, and apply.

Reads catalog/schema/table tags, current grants, and Identity assignments; plans managed
``dac_*`` groups (nesting, membership, GRANT/REVOKE). Apply mutates account groups and UC.
The Databricks Job owns the schedule; the app triggers that Job for Run now and updates its
cron/pause when settings are saved.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.iam import GroupSchema, PatchSchema
from databricks.sdk.service.jobs import CronSchedule, JobSettings, PauseStatus
from pydantic import ValidationError
from pydantic.alias_generators import to_camel

from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, NotConfiguredError, NotFoundError, UpstreamError
from backend.app.models.data_access import (
    RUN_HISTORY_LIMIT,
    RUN_HISTORY_PAGE_SIZE_DEFAULT,
    RUN_HISTORY_PAGE_SIZE_MAX,
    RUN_HISTORY_RETENTION_DAYS,
    DataAccessAnalytics,
    DataAccessPlan,
    DataAccessReadiness,
    DataAccessRun,
    DataAccessRunList,
    DataAccessRunProfile,
    DataAccessSettings,
    DataAccessSettingsView,
    DataAccessViewPage,
    MembershipAction,
    MembershipChange,
    PlannedGrant,
    PlannedGroup,
    ReadinessCheck,
    SyncRunStatus,
    SyncTrigger,
)
from backend.app.models.data_access_pending import (
    AccessStatus,
    GapItem,
    GapItemPage,
    GapKind,
    GapMeta,
    GapStatus,
    MembershipEvent,
    MembershipEventStatus,
    PendingCounts,
    PendingSummary,
    TagChangeCounts,
    TagChangePage,
)
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    IdentityAttributeAssignment,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.repositories.data_access_state import GAP_ITEM_READERS
from backend.app.services.catalog_enrollment import enrolled_catalogs_sql
from backend.app.services.data_access_analytics import build_analytics
from backend.app.services.data_access_gap import (
    build_gap,
    diff_snapshots,
    grant_key,
    group_key,
    member_key,
    nest_key,
    snapshot_from_tags,
)
from backend.app.services.data_access_planner import (
    ActualGrant,
    ActualMember,
    AssignmentRow,
    TagRow,
    group_name,
    plan_data_access,
    truncate_plan,
)
from backend.app.services.data_access_profile import (
    Progress,
    RunProfiler,
    note,
    phase,
    profiling,
    record_call,
    record_size,
    timed_call,
)
from backend.app.services.data_access_schedule import quartz_cron_for_interval_minutes, snap_interval_minutes
from backend.app.services.data_access_status import compute_access_status
from backend.app.services.identity_attributes import ASSIGNMENT_ASSIGNED, IdentityAttributeService
from backend.app.services.table_query import TableQuery, sort_and_filter
from backend.app.services.warehouse_scaling import WarehouseScaler

logger = logging.getLogger(__name__)

_JOB_WAIT = timedelta(minutes=30)


def collapse_membership_by_principal_id(
    changes: list[tuple[str, str, MembershipChange]],
) -> list[tuple[str, str, MembershipChange]]:
    """Drop conflicting ADD+REMOVE pairs that resolve to the same SCIM principal.

    Account SCIM lists members by display name while Identity assignments use userName.
    A plan can therefore contain both ``add email`` and ``remove Display Name`` for the
    same person; applying both would add then remove them. When both appear for the same
    ``(group_id, member_id)``, keep only the ADD.

    Args:
        changes: ``(group_id, member_id, change)`` triples already resolved to SCIM ids.

    Returns:
        Deduplicated triples in first-seen order (ADD preferred over REMOVE).
    """
    winners: dict[tuple[str, str], tuple[str, str, MembershipChange]] = {}
    order: list[tuple[str, str]] = []
    for group_id, member_id, change in changes:
        key = (group_id, member_id)
        existing = winners.get(key)
        if existing is None:
            winners[key] = (group_id, member_id, change)
            order.append(key)
            continue
        _, _, prior = existing
        if prior.action == MembershipAction.REMOVE and change.action == MembershipAction.ADD:
            winners[key] = (group_id, member_id, change)
        # ADD then REMOVE (or duplicate same action): keep the ADD / first winner.
    return [winners[key] for key in order]


_SETTINGS_KEY = "data_access"
_RUNS_KEY = "data_access_runs"
_LOCK_KEY = "data_access_lock"
_LOCK_STALE = timedelta(minutes=30)
# Orphan RUNNING rows (Job cancelled mid-flight) can be closed once the lock is gone.
_ORPHAN_RUNNING = timedelta(minutes=2)
_EXCLUDED_CATALOGS = ("system", "samples", "__databricks_internal")


def _run_elapsed_seconds(run: DataAccessRun) -> float | None:
    if run.duration_seconds is not None:
        return run.duration_seconds
    if run.finished_at is None:
        return None
    return max(0.0, (run.finished_at - run.started_at).total_seconds())


def _run_changes_text(run: DataAccessRun) -> str:
    if run.summary is None:
        return run.error or ""
    summary = run.summary
    return f"{summary.grants_to_add} / {summary.grants_to_revoke} / {summary.conflicts}"


# In-memory readers for the run history table. Keys match the frontend column keys.
RUN_HISTORY_READERS: dict[str, Callable[[DataAccessRun], Any]] = {
    "when": lambda run: run.started_at,
    "elapsed": _run_elapsed_seconds,
    "status": lambda run: run.status.value,
    "trigger": lambda run: run.trigger.value,
    "changes": _run_changes_text,
}


class DataAccessService:
    """Settings, readiness probes, and sync orchestration for data-access groups.

    Args:
        storage: App state (settings JSON for config and run history).
        sql: Warehouse executor running as the Account Admin SP.
        factory: Workspace clients for readiness probes, group listing, and Job trigger.
        identity_attributes: Optional Identity service for governance setup checks.
        sync_job_id: Databricks Job id that owns the schedule; None runs sync in-process
            (tests and local memory mode).
    """

    def __init__(
        self,
        storage: StorageProvider,
        sql: SqlExecutor,
        factory: WorkspaceClientFactory,
        identity_attributes: IdentityAttributeService | None = None,
        *,
        sync_job_id: str | None = None,
    ) -> None:
        self._storage = storage
        self._sql = sql
        self._factory = factory
        self._warehouse = WarehouseScaler(factory.admin, getattr(sql, "warehouse_id", None))
        self._identity = identity_attributes
        self._sync_job_id = (sync_job_id or "").strip() or None
        self._lock = threading.Lock()
        # Tests replace inventory loaders without a warehouse.
        self._tag_loader = self._load_tags
        self._grant_loader = self._load_grants
        self._group_loader = self._load_groups
        self._member_loader = self._load_members
        self._assignment_loader = self._load_assignments
        self._apply_plan = self._apply_plan_impl
        add_listener = getattr(identity_attributes, "add_listener", None)
        if callable(add_listener):
            add_listener(self.on_assignment_event)

    def get_settings(self) -> DataAccessSettingsView:
        """Return stored settings or defaults, plus UI constants.

        Returns:
            Settings view. Invalid stored JSON falls back to defaults and is logged.
        """
        settings = self._settings()
        return DataAccessSettingsView(
            settings=settings,
            apply_available=True,
            enrolled_catalogs=self.list_enrolled_catalogs(settings),
        )

    def save_settings(self, body: DataAccessSettings, actor: CurrentUser) -> DataAccessSettingsView:
        """Validate and persist settings (including preferred dry_run flag).

        Fields the request leaves out keep their stored values. A client that does not know a
        newer setting (for example, grant concurrency) therefore cannot reset it to the default.

        Args:
            body: Settings posted by an admin.
            actor: Admin performing the write.

        Returns:
            The stored settings view.
        """
        stored = self._settings().model_dump(mode="json")
        incoming = body.model_dump(mode="json", exclude_unset=True)
        settings = DataAccessSettings.model_validate({**stored, **incoming})
        self._storage.settings.put_many({_SETTINGS_KEY: settings.model_dump(mode="json")}, actor.user_name)
        self.sync_job_schedule(settings)
        logger.info("Data-access settings updated by %s", actor.user_name)
        return DataAccessSettingsView(
            settings=settings,
            apply_available=True,
            enrolled_catalogs=self.list_enrolled_catalogs(body),
        )

    def list_enrolled_catalogs(self, settings: DataAccessSettings | None = None) -> list[str]:
        """Return catalogs tagged with the configured enrollment tag.

        Args:
            settings: Settings to use; defaults to stored settings.

        Returns:
            Sorted catalog names. Empty when the warehouse is unavailable or none are tagged.
        """
        cfg = settings or self._settings()
        if not self._sql.configured:
            return []
        try:
            with timed_call("sql.enrolled_catalogs"):
                rows = self._sql.query(
                    enrolled_catalogs_sql(
                        cfg.enrollment_catalog_tag_key,
                        cfg.enrollment_catalog_tag_value,
                    )
                )
        except (NotConfiguredError, DatabricksError) as exc:
            logger.warning("Could not list enrolled catalogs: %s", exc)
            return []
        names = sorted(
            {
                str(row["catalog_name"])
                for row in rows
                if row.get("catalog_name") and str(row["catalog_name"]).lower() not in _EXCLUDED_CATALOGS
            }
        )
        return names

    def readiness(self) -> DataAccessReadiness:
        """Probe Account Admin SP and warehouse capabilities.

        Dry-run needs credentials, authentication, a warehouse, tag reads, and group list.
        Apply additionally needs group create and GRANT rights (reported now, not used yet).

        Returns:
            Rolled-up flags plus per-check details.
        """
        checks = [
            self._check_credentials(),
            self._check_authenticate(),
            self._check_warehouse(),
            self._check_read_tags(),
            self._check_list_groups(),
            self._check_create_groups(),
            self._check_grants(),
        ]
        setup_checks = self._setup_checks()
        dry_ids = {"credentials", "authenticate", "warehouse", "read_tags", "list_groups"}
        dry_run_ready = all(c.ok for c in checks if c.id in dry_ids)
        apply_ready = all(c.ok for c in checks)
        setup_ready = all(c.ok for c in setup_checks) if setup_checks else False
        return DataAccessReadiness(
            dry_run_ready=dry_run_ready,
            apply_ready=apply_ready,
            checks=checks,
            setup_checks=setup_checks,
            setup_ready=setup_ready,
        )

    def _setup_checks(self) -> list[ReadinessCheck]:
        """Identity definition checks required for sensitivity and data-access control."""
        if self._identity is None:
            return []
        access_tag = self._identity.access_tag_key()
        sensitivity = self._identity.find_sensitivity_definition()
        data_access = self._identity.find_data_access_definition(access_tag)
        return [
            ReadinessCheck(
                id="sensitivity_definition",
                label="Sensitivity definition",
                ok=sensitivity is not None,
                required_for="setup",
                detail=(
                    f"Configured as '{sensitivity.key}'"
                    if sensitivity is not None
                    else "Create Identity definition with key 'sensitivity'"
                ),
            ),
            ReadinessCheck(
                id="data_access_definition",
                label="Data-access definition",
                ok=data_access is not None,
                required_for="setup",
                detail=(
                    f"Configured as '{data_access.key}' → tag '{access_tag}'"
                    if data_access is not None
                    else f"Create Identity definition linked to governed tag '{access_tag}'"
                ),
            ),
        ]

    def list_runs(
        self,
        *,
        mode: str | None = None,
        page: int = 1,
        page_size: int = RUN_HISTORY_PAGE_SIZE_DEFAULT,
        table_query: TableQuery | None = None,
    ) -> DataAccessRunList:
        """Return paginated sync runs for the last retention window.

        Args:
            mode: Optional ``dry_run`` or ``apply`` filter.
            page: 1-based page index.
            page_size: Rows per page (clamped).
            table_query: Optional column sort and filters applied before paging.

        Returns:
            Summaries only (no full plans), newest first unless sorted.
        """
        mode_filter = self._normalize_mode_filter(mode)
        page = max(1, page)
        page_size = min(max(1, page_size), RUN_HISTORY_PAGE_SIZE_MAX)
        self._reconcile_stale_runs()
        filtered = sort_and_filter(
            self._filtered_runs(mode=mode_filter), table_query or TableQuery(), RUN_HISTORY_READERS
        )
        total = len(filtered)
        start = (page - 1) * page_size
        page_rows = filtered[start : start + page_size]
        return DataAccessRunList(
            runs=[r.model_copy(update={"plan": None}) for r in page_rows],
            total=total,
            page=page,
            page_size=page_size,
            mode=mode_filter,
            retention_days=RUN_HISTORY_RETENTION_DAYS,
        )

    def get_run(self, run_id: str) -> DataAccessRun:
        """Return one stored run including its truncated plan.

        Args:
            run_id: Identifier from a previous sync.

        Returns:
            The run.

        Raises:
            NotFoundError: If the id is unknown or outside the retention window.
        """
        for run in self._filtered_runs():
            if run.run_id == run_id:
                return run
        raise NotFoundError(f"Data-access run {run_id} was not found")

    def analytics(self, *, days: int | None = None) -> DataAccessAnalytics:
        """Aggregate stored runs into dashboard analytics for the window.

        Args:
            days: Window length in days (clamped to the retention window).

        Returns:
            KPIs, daily series, recent failures, and fingerprint stability.
        """
        self._reconcile_stale_runs()
        return build_analytics(self._filtered_runs(), days=days)

    def view_page(
        self,
        *,
        kind: str,
        mode: str | None = None,
        change: str | None = None,
        page: int = 1,
        page_size: int = 25,
        table_query: TableQuery | None = None,
    ) -> DataAccessViewPage:
        """Read one page of a data-access SQL view (Lakebase only).

        Args:
            kind: ``runs`` for ``data_access_runs_v`` or ``grants`` for ``data_access_run_grants_v``.
            mode: Optional ``dry_run`` or ``apply`` filter.
            change: Optional ``add`` or ``revoke`` filter (grants only).
            page: 1-based page index.
            page_size: Rows per page (clamped to 1..100).
            table_query: Optional column sort and filters, applied in SQL.

        Returns:
            Converted rows and the total count, or an unavailable page on other backends.
        """
        size = min(max(1, page_size), 100)
        current = max(1, page)
        source = "data_access_runs_v" if kind == "runs" else "data_access_run_grants_v"
        reader = self._storage.data_access_views
        if reader is None:
            return DataAccessViewPage(
                page=current,
                page_size=size,
                source=source,
                available=False,
                detail="The SQL views exist only on the Lakebase backend.",
            )
        mode_filter = self._normalize_mode_filter(mode)
        offset = (current - 1) * size
        if kind == "runs":
            rows, total = reader.runs_page(
                mode=mode_filter, limit=size, offset=offset, table_query=table_query
            )
        else:
            rows, total = reader.grants_page(
                mode=mode_filter, change=change, limit=size, offset=offset, table_query=table_query
            )
        return DataAccessViewPage(
            rows=[{to_camel(key): value for key, value in row.items()} for row in rows],
            total=total,
            page=current,
            page_size=size,
            source=source,
        )

    def latest_run(self, *, mode: str | None = None) -> DataAccessRun | None:
        """Return the most recent run (optionally mode-filtered) including its plan.

        Args:
            mode: Optional ``dry_run`` or ``apply`` filter.

        Returns:
            The newest matching run in the retention window, or None.
        """
        mode_filter = self._normalize_mode_filter(mode)
        runs = self._filtered_runs(mode=mode_filter)
        return runs[0] if runs else None

    def run_now(self, actor: CurrentUser, *, mode: str = "dry_run") -> DataAccessRun:
        """Run a sync as the signed-in admin via the Databricks Job (waits for completion).

        When ``DATA_ACCESS_SYNC_JOB_ID`` is unset (tests/local), runs in-process instead.

        Args:
            actor: Admin who clicked Run now.
            mode: ``dry_run`` or ``apply``.

        Returns:
            The finished run (success or failed).

        Raises:
            InvalidRequestError: If another run holds the lock or apply is not ready.
            UpstreamError: If the Job cannot be started or fails before writing a run.
        """
        if self._sync_job_id is None:
            return self.run_sync(actor=actor.user_name, trigger=SyncTrigger.USER, mode=mode)
        return self.trigger_job_and_wait(actor=actor, mode=mode)

    def trigger_job_and_wait(self, *, actor: CurrentUser, mode: str = "dry_run") -> DataAccessRun:
        """Start the data-access sync Job and wait until it finishes, then return the run.

        Args:
            actor: Admin who clicked Run now.
            mode: ``dry_run`` or ``apply``.

        Returns:
            The run written by the Job entrypoint.

        Raises:
            NotConfiguredError: If the Job id is not configured.
            InvalidRequestError: If apply is blocked by readiness.
            UpstreamError: If the Job fails or the run record is missing afterward.
        """
        if self._sync_job_id is None:
            raise NotConfiguredError("DATA_ACCESS_SYNC_JOB_ID is not set")
        mode = (mode or "dry_run").strip().lower()
        if mode not in {"dry_run", "apply"}:
            raise InvalidRequestError("mode must be dry_run or apply")
        if mode == "apply":
            readiness = self.readiness()
            if not readiness.apply_ready:
                raise InvalidRequestError(
                    "Apply is not ready — fix failing Account Admin SP / warehouse checks"
                )
            if not readiness.setup_ready:
                raise InvalidRequestError("Apply requires sensitivity and data-access Identity definitions")
        run_id = str(uuid.uuid4())
        job_id = int(self._sync_job_id)
        try:
            self._factory.app().jobs.run_now_and_wait(
                job_id,
                job_parameters={
                    "mode": mode,
                    "trigger": SyncTrigger.USER.value,
                    "actor": actor.user_name,
                    "run_id": run_id,
                },
                timeout=_JOB_WAIT,
            )
        except Exception as exc:  # noqa: BLE001 - map Job failures to a clear API error
            try:
                return self.get_run(run_id)
            except NotFoundError:
                raise UpstreamError(f"Data-access sync Job failed: {exc}") from exc
        try:
            return self.get_run(run_id)
        except NotFoundError as exc:
            raise UpstreamError(
                f"Data-access sync Job finished but run {run_id} was not found in history"
            ) from exc

    def sync_job_schedule_from_storage(self) -> None:
        """Re-apply stored interval and pause state to the Job (called on app startup).

        A bundle deploy resets the Job to the YAML schedule, so startup pushes the saved
        settings back. Failures are logged rather than raised so the app still starts.
        """
        if self._sync_job_id is None:
            return
        try:
            self.sync_job_schedule(self._settings())
        except UpstreamError:
            logger.exception("Could not re-apply data-access sync Job schedule at startup")

    def sync_job_schedule(self, settings: DataAccessSettings) -> None:
        """Push interval and pause state to the Databricks Job schedule.

        No-op when the Job id is unset (tests/local). ``schedule_mode`` stays in storage;
        scheduled ticks read it at run time.

        Args:
            settings: Settings that were just saved.

        Raises:
            UpstreamError: If the Job schedule cannot be updated.
        """
        if self._sync_job_id is None:
            return
        snapped = snap_interval_minutes(settings.interval_minutes)
        cron = quartz_cron_for_interval_minutes(settings.interval_minutes)
        pause = PauseStatus.UNPAUSED if settings.schedule_enabled else PauseStatus.PAUSED
        try:
            self._factory.app().jobs.update(
                int(self._sync_job_id),
                new_settings=JobSettings(
                    schedule=CronSchedule(
                        quartz_cron_expression=cron,
                        timezone_id="UTC",
                        pause_status=pause,
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not update data-access sync Job schedule: {exc}") from exc
        logger.info(
            "Data-access sync Job %s schedule cron=%s pause=%s (requested %sm, snapped %sm)",
            self._sync_job_id,
            cron,
            pause.value,
            settings.interval_minutes,
            snapped,
        )

    def run_sync(
        self,
        *,
        actor: str,
        trigger: SyncTrigger,
        mode: str = "dry_run",
        run_id: str | None = None,
        preflight: dict[str, float] | None = None,
    ) -> DataAccessRun:
        """Inventory, plan, optionally apply, and persist the run.

        Args:
            actor: User name or ``scheduler``.
            trigger: Manual or scheduled.
            mode: ``dry_run`` (plan only) or ``apply`` (mutate groups/grants/members).
            run_id: Optional predetermined id (Job Run now passes one so the app can wait).
            preflight: Seconds spent before this call (Job startup phases), stored on the profile.

        Returns:
            The stored run.

        Raises:
            InvalidRequestError: If a run is already in progress or apply is blocked.
        """
        mode = (mode or "dry_run").strip().lower()
        if mode not in {"dry_run", "apply"}:
            raise InvalidRequestError("mode must be dry_run or apply")
        if mode == "apply":
            readiness = self.readiness()
            if not readiness.apply_ready:
                raise InvalidRequestError(
                    "Apply is not ready — fix failing Account Admin SP / warehouse checks"
                )
            if not readiness.setup_ready:
                raise InvalidRequestError("Apply requires sensitivity and data-access Identity definitions")
        run_id = (run_id or "").strip() or str(uuid.uuid4())
        started = datetime.now(UTC)
        self._reconcile_stale_runs()
        if not self._acquire_lock(run_id, started):
            raise InvalidRequestError("A data-access sync is already running")
        running = DataAccessRun(
            run_id=run_id,
            status=SyncRunStatus.RUNNING,
            trigger=trigger,
            mode=mode,
            started_at=started,
            triggered_by=actor,
        )
        self._write_run(running)
        profiler = RunProfiler()
        for name, seconds in (preflight or {}).items():
            profiler.add_phase(name, seconds)
        try:
            with profiling(profiler):
                finished = self._run_sync_body(running, mode=mode, started=started)
                with phase("write_run"):
                    finished = finished.model_copy(update={"profile": profiler.snapshot()})
                    self._write_run(finished)
            finished = finished.model_copy(update={"profile": profiler.snapshot()})
            summary = finished.summary
            logger.info(
                "Data-access %s %s finished: add=%s revoke=%s members+=%s members-=%s conflicts=%s",
                mode,
                run_id,
                summary.grants_to_add if summary else 0,
                summary.grants_to_revoke if summary else 0,
                summary.members_to_add if summary else 0,
                summary.members_to_remove if summary else 0,
                summary.conflicts if summary else 0,
            )
            self._log_profile(mode, run_id, finished.profile)
            return finished
        except Exception as exc:  # noqa: BLE001 - persist failure, then re-raise as run record
            failed = running.model_copy(
                update={
                    "status": SyncRunStatus.FAILED,
                    "finished_at": datetime.now(UTC),
                    "error": str(exc),
                    "profile": profiler.snapshot(),
                }
            )
            self._write_run(failed)
            logger.exception("Data-access %s %s failed", mode, run_id)
            self._log_profile(mode, run_id, failed.profile)
            return failed
        finally:
            self._release_lock(run_id)

    def _run_sync_body(self, running: DataAccessRun, *, mode: str, started: datetime) -> DataAccessRun:
        """Inventory, plan, apply, and record state for one run; return the finished record."""
        settings = self._settings()
        with phase("load.tags"):
            tags = self._tag_loader(settings)
        with phase("load.grants"):
            grants = self._grant_loader(settings)
        with phase("load.groups"):
            groups = self._group_loader(settings)
        with phase("load.assignments"):
            assignments = self._assignment_loader(settings)
        with phase("load.members"):
            members = self._member_loader(settings, groups)
        record_size("tag_rows", len(tags))
        record_size("table_tag_rows", sum(1 for t in tags if t.level == "table"))
        record_size("grants_read", len(grants))
        record_size("groups_read", len(groups))
        record_size("assignments_read", len(assignments))
        record_size("members_read", len(members))
        logger.info(
            "inventory: table_tags=%d grants=%d groups=%d assignments=%d (users=%d groups=%d) members=%d",
            sum(1 for t in tags if t.level == "table"),
            len(grants),
            len(groups),
            len(assignments),
            sum(1 for a in assignments if a.principal_type == "USER"),
            sum(1 for a in assignments if a.principal_type == "GROUP"),
            len(members),
        )
        with phase("plan"):
            previous = next(
                (r for r in self._filtered_runs(mode=mode) if r.status == SyncRunStatus.SUCCESS),
                None,
            )
            plan = plan_data_access(
                settings,
                tags,
                grants,
                groups,
                previous.fingerprint if previous else None,
                previous.summary if previous else None,
                assignments=assignments,
                actual_members=members,
            )
        record_size("plan_groups", len(plan.groups))
        record_size("plan_grants_to_add", len(plan.grants_to_add))
        record_size("plan_grants_to_revoke", len(plan.grants_to_revoke))
        record_size("plan_membership_changes", len(plan.membership_changes))
        record_size("eligible_objects", plan.summary.eligible_objects)
        if mode == "apply":
            with phase("apply"):
                plan = self._apply_plan(settings, plan)
            record_size("apply_failures", len(plan.apply_failures))
        with phase("record_state"):
            self._record_state(
                settings,
                run_id=running.run_id,
                mode=mode,
                started=started,
                tags=tags,
                plan=plan,
                members=members,
            )
        plan = truncate_plan(plan)
        return running.model_copy(
            update={
                "status": SyncRunStatus.SUCCESS,
                "finished_at": datetime.now(UTC),
                "summary": plan.summary,
                "plan": plan,
                "fingerprint": plan.fingerprint,
                "error": "; ".join(plan.apply_errors) if plan.apply_errors else None,
            }
        )

    @staticmethod
    def _log_profile(mode: str, run_id: str, profile: DataAccessRunProfile | None) -> None:
        """One JSON line per run so Job output can be grepped for ``data_access_profile``."""
        if profile is None:
            return
        logger.info(
            "data_access_profile mode=%s run_id=%s %s",
            mode,
            run_id,
            json.dumps(profile.model_dump(mode="json"), sort_keys=True),
        )

    # Outstanding work, tag changes, immediate membership, and access status.

    def _record_state(
        self,
        settings: DataAccessSettings,
        *,
        run_id: str,
        mode: str,
        started: datetime,
        tags: list[TagRow],
        plan: DataAccessPlan,
        members: list[ActualMember],
    ) -> None:
        """Store the full outstanding list and the tag snapshot diff for this run (best effort).

        Membership applied by approvals while the run was in flight is not re-listed as
        outstanding, and approval-time failures stay listed until a sync retries them.
        """
        state = self._storage.data_access_state
        try:
            baseline = state.gap_meta() is None
            snapshot = snapshot_from_tags(settings, tags)
            record_size("snapshot_tables", len(snapshot))
            if not baseline:
                with phase("state.tag_diff"):
                    changes = diff_snapshots(
                        state.load_tag_snapshot(), snapshot, run_id=run_id, detected_at=started
                    )
                    state.add_tag_changes(changes)
                record_size("tag_changes", len(changes))
            with phase("state.tag_snapshot_write"):
                state.replace_tag_snapshot(snapshot)

            with phase("state.build_gap"):
                gap = build_gap(
                    plan,
                    members,
                    applied=mode == "apply",
                    failures=plan.apply_failures,
                    run_id=run_id,
                    computed_at=started,
                )
                by_key = {item.item_key: item for item in gap}
                for event in state.membership_events_since(started):
                    key = member_key(event.action, event.group_name, event.principal_name)
                    if event.status == MembershipEventStatus.APPLIED:
                        by_key.pop(key, None)
                    elif event.status == MembershipEventStatus.FAILED and key not in by_key:
                        by_key[key] = self._event_gap_item(event)
            record_size("gap_items", len(by_key))
            with phase("state.gap_write"):
                state.replace_gap(
                    list(by_key.values()),
                    GapMeta(
                        run_id=run_id,
                        mode=mode,
                        computed_at=started,
                        tables_tracked=len(snapshot),
                        tag_baseline=baseline,
                    ),
                )
            cutoff = started - timedelta(days=RUN_HISTORY_RETENTION_DAYS)
            with phase("state.prune"):
                state.prune_tag_changes(cutoff)
                state.prune_membership_events(cutoff)
        except Exception:  # noqa: BLE001 - the run itself succeeded; status views catch up next run
            logger.exception("Recording data-access outstanding work for run %s failed", run_id)

    @staticmethod
    def _event_gap_item(event: MembershipEvent) -> GapItem:
        """Outstanding item for an approval-time membership change that did not apply."""
        kind = GapKind.MEMBER_ADD if event.action == "add" else GapKind.MEMBER_REMOVE
        return GapItem(
            item_key=member_key(event.action, event.group_name, event.principal_name),
            kind=kind,
            status=GapStatus.FAILED if event.status == MembershipEventStatus.FAILED else GapStatus.PENDING,
            group_name=event.group_name,
            principal_name=event.principal_name,
            principal_type=event.principal_type,
            value=event.value,
            reason=f"assignment {event.action} by {event.actor or 'unknown'}",
            error=event.error,
            computed_at=event.recorded_at,
        )

    def _data_access_key(self, settings: DataAccessSettings) -> str | None:
        """Attribute key of the data-access Identity definition, if configured."""
        if self._identity is None:
            return None
        definition = self._identity.find_data_access_definition(settings.access_tag_key)
        return definition.key if definition is not None else None

    def on_assignment_event(
        self, event: str, assignment: IdentityAttributeAssignment, actor: CurrentUser
    ) -> None:
        """Add or remove the assignee on the path's ``dac_*`` group right away.

        Runs after an approval, admin assignment, or revoke of a data-access attribute.
        Creates the group when it does not exist yet (nesting and grants still come from
        the sync). Never raises: failures are recorded and the next sync retries them.

        Args:
            event: ``assigned`` or ``revoked``.
            assignment: The saved assignment.
            actor: Who approved, assigned, or revoked.
        """
        settings = self._settings()
        key = self._data_access_key(settings)
        if key is None or key.lower() != assignment.attribute_key.lower():
            return
        value = assignment.attribute_value.strip()
        group = group_name(settings.group_prefix, value) if value and value != "*" else None
        if group is None:
            return
        action = "add" if event == ASSIGNMENT_ASSIGNED else "remove"
        principal_type = assignment.principal_type.value
        state = self._storage.data_access_state

        def record(
            status: MembershipEventStatus,
            *,
            error: str | None = None,
            created: bool = False,
            outstanding: bool = True,
        ) -> None:
            entry = MembershipEvent(
                recorded_at=datetime.now(UTC),
                assignment_id=assignment.assignment_id,
                principal_name=assignment.principal_name,
                principal_type=principal_type,
                group_name=group,
                value=value,
                action=action,
                status=status,
                group_created=created,
                error=error,
                actor=actor.user_name,
            )
            state.record_membership_event(entry)
            keys = [
                member_key("add", group, assignment.principal_name),
                member_key("remove", group, assignment.principal_name),
            ]
            if status == MembershipEventStatus.APPLIED:
                state.delete_gap_items(keys)
            elif outstanding:
                state.upsert_gap_items([self._event_gap_item(entry)])

        if not self._factory.admin_configured:
            record(
                MembershipEventStatus.SKIPPED,
                error="Account Admin SP is not configured; the next sync applies this",
            )
            return
        if action == "remove" and self._still_assigned(assignment, group, settings, key):
            record(
                MembershipEventStatus.SKIPPED,
                error="Another active assignment still maps to this group",
                outstanding=False,
            )
            return

        created = False
        try:
            group_id = self._find_group_id(group)
            if group_id is None:
                if action == "remove":
                    record(MembershipEventStatus.APPLIED)
                    return
                group_id = self._account_scim_create_group(group)
                created = True
            errors: list[str] = []
            member_id = self._resolve_principal_id(assignment.principal_name, principal_type, errors)
            if member_id is None:
                missing = f"principal {assignment.principal_name} not found"
                raise RuntimeError(errors[-1] if errors else missing)
            if action == "add":
                self._patch_member_add(group_id, member_id)
            else:
                self._patch_member_remove(group_id, member_id)
        except Exception as exc:  # noqa: BLE001 - recorded; the sync retries
            logger.warning(
                "Immediate %s of %s on %s failed: %s", action, assignment.principal_name, group, exc
            )
            record(MembershipEventStatus.FAILED, error=str(exc), created=created)
            return
        record(MembershipEventStatus.APPLIED, created=created)
        logger.info(
            "Immediate membership %s %s on %s (group created: %s)",
            action,
            assignment.principal_name,
            group,
            created,
        )

    def _still_assigned(
        self, assignment: IdentityAttributeAssignment, group: str, settings: DataAccessSettings, key: str
    ) -> bool:
        """True when another active assignment for the same principal maps to ``group``."""
        others = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(
                principal_name=assignment.principal_name,
                principal_type=assignment.principal_type,
                attribute_key=key,
                include_inactive=False,
            )
        )
        return any(
            other.assignment_id != assignment.assignment_id
            and (group_name(settings.group_prefix, other.attribute_value) or "").lower() == group.lower()
            for other in others
        )

    def _find_group_id(self, group: str) -> str | None:
        """SCIM id of the account group named ``group``, or None."""
        escaped = group.replace('"', '\\"')
        for found in self._account_scim_list_groups(filter_expr=f'displayName eq "{escaped}"'):
            if str(found.get("displayName") or "").lower() == group.lower() and found.get("id"):
                return str(found["id"])
        return None

    def _patch_member_add(self, group_id: str, member_id: str) -> None:
        """Add one member; an existing membership is success."""
        try:
            self._account_scim_patch_group(
                group_id, [{"op": "add", "path": "members", "value": [{"value": member_id}]}]
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "already" in message or "exists" in message:
                return
            raise

    def _patch_member_remove(self, group_id: str, member_id: str) -> None:
        """Remove one member; an absent membership is success."""
        try:
            self._account_scim_patch_group(
                group_id, [{"op": "remove", "path": f'members[value eq "{member_id}"]'}]
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "not found" in message or "does not exist" in message or "no such" in message:
                return
            raise

    def _expired_since_sync(
        self, settings: DataAccessSettings, meta: GapMeta | None
    ) -> list[IdentityAttributeAssignment]:
        """Data-access assignments that expired (not revoked) after the last sync looked."""
        key = self._data_access_key(settings)
        if key is None:
            return []
        now = datetime.now(UTC)
        since = meta.computed_at if meta is not None else None
        items = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(attribute_key=key, include_inactive=True)
        )
        return sorted(
            (
                a
                for a in items
                if a.revoked_at is None
                and a.expires_at is not None
                and a.expires_at <= now
                and (since is None or a.expires_at > since)
                and (a.attribute_value or "").strip() not in {"", "*"}
            ),
            key=lambda a: a.expires_at or now,
            reverse=True,
        )

    def pending_summary(self) -> PendingSummary:
        """Counts of what the sync still has to do, plus tag changes from the last run.

        Returns:
            Outstanding counts by kind, expiries not yet seen by a sync, and tag change counts.
        """
        state = self._storage.data_access_state
        meta = state.gap_meta()
        raw = state.gap_counts()
        counts = PendingCounts(
            group_create=raw.get(GapKind.GROUP_CREATE.value, 0),
            group_nest=raw.get(GapKind.GROUP_NEST.value, 0),
            member_add=raw.get(GapKind.MEMBER_ADD.value, 0),
            member_remove=raw.get(GapKind.MEMBER_REMOVE.value, 0),
            grant_add=raw.get(GapKind.GRANT_ADD.value, 0),
            grant_revoke=raw.get(GapKind.GRANT_REVOKE.value, 0),
            failed=raw.get("failed", 0),
            expired_since_sync=len(self._expired_since_sync(self._settings(), meta)),
        )
        tag_counts = TagChangeCounts()
        if meta is not None:
            tag_counts = TagChangeCounts(**state.tag_change_counts(meta.run_id))
        total = sum(raw.get(kind.value, 0) for kind in GapKind)
        return PendingSummary(meta=meta, counts=counts, tag_changes=tag_counts, total=total)

    def pending_items(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
        table_query: TableQuery | None = None,
    ) -> GapItemPage:
        """One page of outstanding items.

        Args:
            kind: A gap kind, ``expired`` (assignments expired since the last sync), or None for all.
            status: ``pending`` or ``failed``.
            query: Substring over group, principal, securable, and value.
            page: 1-based page.
            page_size: Rows per page (1-200).
            table_query: Optional column sort and filters applied before paging.

        Returns:
            The page and total.

        Raises:
            InvalidRequestError: For an unknown kind or status.
        """
        page = max(1, page)
        page_size = min(max(1, page_size), 200)
        offset = (page - 1) * page_size
        kind = (kind or "").strip().lower() or None
        status = (status or "").strip().lower() or None
        if status and status not in {s.value for s in GapStatus}:
            raise InvalidRequestError("status must be pending or failed")
        if kind == "expired":
            return self._expired_page(query=query, page=page, page_size=page_size, table_query=table_query)
        if kind and kind not in {k.value for k in GapKind}:
            raise InvalidRequestError(f"Unknown kind '{kind}'")
        items, total = self._storage.data_access_state.gap_page(
            kind=kind,
            status=status,
            query=query,
            limit=page_size,
            offset=offset,
            table_query=table_query,
        )
        return GapItemPage(items=items, total=total, page=page, page_size=page_size)

    def _expired_page(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
        table_query: TableQuery | None = None,
    ) -> GapItemPage:
        """Expired-since-sync assignments as removal items."""
        settings = self._settings()
        meta = self._storage.data_access_state.gap_meta()
        needle = (query or "").strip().lower()
        rows: list[GapItem] = []
        for a in self._expired_since_sync(settings, meta):
            group = group_name(settings.group_prefix, a.attribute_value) or ""
            if (
                needle
                and needle not in a.principal_name.lower()
                and needle not in group.lower()
                and (needle not in a.attribute_value.lower())
            ):
                continue
            rows.append(
                GapItem(
                    item_key=f"expired|{a.assignment_id}",
                    kind=GapKind.MEMBER_REMOVE,
                    group_name=group,
                    principal_name=a.principal_name,
                    principal_type=a.principal_type.value,
                    value=a.attribute_value,
                    reason=f"assignment expired {a.expires_at:%Y-%m-%d %H:%M} UTC; removed at the next sync",
                    computed_at=a.expires_at or datetime.now(UTC),
                )
            )
        rows = sort_and_filter(rows, table_query or TableQuery(), GAP_ITEM_READERS)
        start = (page - 1) * page_size
        return GapItemPage(
            items=rows[start : start + page_size], total=len(rows), page=page, page_size=page_size
        )

    def tag_changes(
        self,
        *,
        change: str | None = None,
        scope: str = "latest",
        page: int = 1,
        page_size: int = 25,
        table_query: TableQuery | None = None,
    ) -> TagChangePage:
        """One page of tables whose data-access tagging changed, newest first unless sorted.

        Args:
            change: ``newly_eligible``, ``no_longer_eligible``, ``value_changed``, or None.
            scope: ``latest`` (last sync only) or ``all`` (retention window).
            page: 1-based page.
            page_size: Rows per page (1-200).
            table_query: Optional column sort and filters applied before paging.

        Returns:
            The page and total.

        Raises:
            InvalidRequestError: For an unknown scope.
        """
        if scope not in {"latest", "all"}:
            raise InvalidRequestError("scope must be latest or all")
        page = max(1, page)
        page_size = min(max(1, page_size), 200)
        state = self._storage.data_access_state
        run_id: str | None = None
        if scope == "latest":
            meta = state.gap_meta()
            if meta is None:
                return TagChangePage(page=page, page_size=page_size)
            run_id = meta.run_id
        items, total = state.tag_changes_page(
            change=(change or "").strip().lower() or None,
            run_id=run_id,
            limit=page_size,
            offset=(page - 1) * page_size,
            table_query=table_query,
        )
        return TagChangePage(items=items, total=total, page=page, page_size=page_size)

    def access_status(self, assignments: list[IdentityAttributeAssignment]) -> list[AccessStatus]:
        """Whether each data-access assignment gives access yet (other attributes are skipped).

        Args:
            assignments: Assignments to report on (any attribute, any state).

        Returns:
            One status per data-access assignment, in input order.
        """
        settings = self._settings()
        key = self._data_access_key(settings)
        if key is None:
            return []
        relevant = [a for a in assignments if a.attribute_key.lower() == key.lower()]
        if not relevant:
            return []
        state = self._storage.data_access_state
        meta = state.gap_meta()
        since = meta.computed_at if meta is not None else datetime.min.replace(tzinfo=UTC)
        created = {
            e.group_name.lower()
            for e in state.membership_events_since(since)
            if e.group_created and e.status == MembershipEventStatus.APPLIED
        }
        structure_values = state.gap_structure_values()
        events = state.latest_membership_events([a.assignment_id for a in relevant])
        by_principal: dict[str, list[GapItem]] = {}
        for item in state.member_gap_items(sorted({a.principal_name for a in relevant})):
            by_principal.setdefault((item.principal_name or "").lower(), []).append(item)
        now = datetime.now(UTC)
        return [
            compute_access_status(
                a,
                prefix=settings.group_prefix,
                separator=settings.hierarchy_separator,
                meta=meta,
                structure_values=structure_values,
                groups_created_since_sync=created,
                event=events.get(a.assignment_id),
                member_items=by_principal.get(a.principal_name.lower(), []),
                now=now,
            )
            for a in relevant
        ]

    @staticmethod
    def _normalize_mode_filter(mode: str | None) -> str | None:
        """Return ``dry_run`` / ``apply`` or None; reject unknown values."""
        if mode is None or not str(mode).strip():
            return None
        text = str(mode).strip().lower()
        if text not in {"dry_run", "apply"}:
            raise InvalidRequestError("mode must be dry_run or apply")
        return text

    def _retention_cutoff(self) -> datetime:
        """UTC timestamp before which runs are hidden and pruned."""
        return datetime.now(UTC) - timedelta(days=RUN_HISTORY_RETENTION_DAYS)

    def finished_runs_since(self, since: datetime) -> list[DataAccessRun]:
        """Finished sync runs (dry-run and apply) that started at or after ``since``.

        Used to attribute warehouse and job cost to each sync's start and finish window.

        Args:
            since: Earliest start time (timezone-aware).

        Returns:
            Runs with a finish time, newest first.
        """
        return [r for r in self._runs() if r.started_at >= since and r.finished_at is not None]

    def _filtered_runs(self, *, mode: str | None = None) -> list[DataAccessRun]:
        """Runs in the retention window, optionally filtered by mode, newest first."""
        cutoff = self._retention_cutoff()
        runs = [r for r in self._runs() if r.started_at >= cutoff]
        if mode:
            runs = [r for r in runs if (r.mode or "dry_run").lower() == mode]
        return runs

    def _reconcile_stale_runs(self) -> None:
        """Close RUNNING history rows left behind when a Job is cancelled mid-flight.

        ``run_sync`` writes RUNNING then SUCCESS/FAILED. A cancel kills the process before
        the final write, so the UI can show a stuck running row. Mark those failed when the
        lock is gone (or stale) and the row is older than ``_ORPHAN_RUNNING``.
        """
        now = datetime.now(UTC)
        lock = self._storage.settings.get_all().get(_LOCK_KEY) or {}
        lock_run_id = lock.get("run_id") if isinstance(lock, dict) else None
        lock_started = None
        if isinstance(lock, dict) and lock.get("started_at"):
            try:
                lock_started = datetime.fromisoformat(str(lock["started_at"]).replace("Z", "+00:00"))
            except ValueError:
                lock_started = None
        lock_stale = lock_started is None or (now - lock_started) >= _LOCK_STALE
        changed = False
        for run in self._runs():
            if run.status != SyncRunStatus.RUNNING or run.finished_at is not None:
                continue
            age = now - run.started_at
            held_by_this = lock_run_id == run.run_id and not lock_stale
            if held_by_this:
                continue
            if age < _ORPHAN_RUNNING and not lock_stale:
                continue
            self._write_run(
                run.model_copy(
                    update={
                        "status": SyncRunStatus.FAILED,
                        "finished_at": now,
                        "error": ("Cancelled or interrupted before the Job finished writing a final status"),
                    }
                )
            )
            changed = True
        if changed and (lock_stale or not lock_run_id):
            self._storage.settings.put_many({_LOCK_KEY: {}}, "system")

    def _settings(self) -> DataAccessSettings:
        """Load settings from storage, or defaults when missing/invalid."""
        raw = self._storage.settings.get_all().get(_SETTINGS_KEY)
        if not raw:
            return DataAccessSettings()
        try:
            return DataAccessSettings.model_validate(raw)
        except ValidationError:
            logger.exception("Ignoring invalid data-access settings blob")
            return DataAccessSettings()

    def _runs(self) -> list[DataAccessRun]:
        """Load stored runs, newest first."""
        raw = self._storage.settings.get_all().get(_RUNS_KEY) or []
        if not isinstance(raw, list):
            return []
        runs: list[DataAccessRun] = []
        for item in raw:
            try:
                runs.append(DataAccessRun.model_validate(item))
            except ValidationError:
                continue
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs

    def _write_run(self, run: DataAccessRun) -> None:
        """Upsert ``run`` at the front; prune by retention days and hard cap."""
        with self._lock:
            cutoff = self._retention_cutoff()
            existing = [r for r in self._runs() if r.run_id != run.run_id and r.started_at >= cutoff]
            blob = [
                run.model_dump(mode="json", exclude={"duration_seconds"}),
                *[r.model_dump(mode="json", exclude={"duration_seconds"}) for r in existing],
            ]
            self._storage.settings.put_many({_RUNS_KEY: blob[:RUN_HISTORY_LIMIT]}, "system")

    def _acquire_lock(self, run_id: str, started: datetime) -> bool:
        """Take the sync lock unless a non-stale lock is held.

        Args:
            run_id: Run that wants the lock.
            started: Timestamp written into the lock record.

        Returns:
            True when this run now holds the lock.
        """
        with self._lock:
            current = self._storage.settings.get_all().get(_LOCK_KEY) or {}
            held_at = current.get("started_at") if isinstance(current, dict) else None
            if held_at:
                try:
                    when = datetime.fromisoformat(str(held_at).replace("Z", "+00:00"))
                except ValueError:
                    when = None
                if when is not None and datetime.now(UTC) - when < _LOCK_STALE:
                    return False
            self._storage.settings.put_many(
                {_LOCK_KEY: {"run_id": run_id, "started_at": started.isoformat()}},
                "system",
            )
            return True

    def _release_lock(self, run_id: str) -> None:
        """Clear the lock if it still belongs to ``run_id``."""
        with self._lock:
            current = self._storage.settings.get_all().get(_LOCK_KEY) or {}
            if isinstance(current, dict) and current.get("run_id") == run_id:
                self._storage.settings.put_many({_LOCK_KEY: {}}, "system")

    def _load_tags(self, settings: DataAccessSettings) -> list[TagRow]:
        """Read access and review tags from information_schema."""
        keys = ", ".join(
            string_literal(k.lower()) for k in (settings.access_tag_key, settings.review_tag_key)
        )
        enrolled = self.list_enrolled_catalogs(settings)
        catalog_filter = self._catalog_sql(settings, "catalog_name", enrolled)
        rows: list[TagRow] = []
        with timed_call("sql.read_catalog_tags"):
            catalog_rows = self._sql.query(
                f"""
                SELECT catalog_name, tag_name, tag_value
                FROM system.information_schema.catalog_tags
                WHERE lower(tag_name) IN ({keys})
                  {catalog_filter}
                """
            )
        for row in catalog_rows:
            rows.append(
                TagRow(
                    level="catalog",
                    catalog=str(row["catalog_name"]),
                    schema=None,
                    table=None,
                    tag_name=str(row["tag_name"]),
                    tag_value="" if row.get("tag_value") is None else str(row["tag_value"]),
                )
            )
        with timed_call("sql.read_schema_tags"):
            schema_rows = self._sql.query(
                f"""
                SELECT catalog_name, schema_name, tag_name, tag_value
                FROM system.information_schema.schema_tags
                WHERE lower(tag_name) IN ({keys})
                  {catalog_filter}
                """
            )
        for row in schema_rows:
            rows.append(
                TagRow(
                    level="schema",
                    catalog=str(row["catalog_name"]),
                    schema=str(row["schema_name"]),
                    table=None,
                    tag_name=str(row["tag_name"]),
                    tag_value="" if row.get("tag_value") is None else str(row["tag_value"]),
                )
            )
        with timed_call("sql.read_table_tags"):
            table_rows = self._sql.query(
                f"""
                SELECT catalog_name, schema_name, table_name, tag_name, tag_value
                FROM system.information_schema.table_tags
                WHERE lower(tag_name) IN ({keys})
                  {catalog_filter}
                """
            )
        for row in table_rows:
            rows.append(
                TagRow(
                    level="table",
                    catalog=str(row["catalog_name"]),
                    schema=str(row["schema_name"]),
                    table=str(row["table_name"]),
                    tag_name=str(row["tag_name"]),
                    tag_value="" if row.get("tag_value") is None else str(row["tag_value"]),
                )
            )
        return rows

    def _load_grants(self, settings: DataAccessSettings) -> list[ActualGrant]:
        """Read direct privileges whose grantee starts with the managed prefix."""
        prefix = settings.group_prefix.lower()
        if not prefix.endswith("_"):
            prefix = f"{prefix}_"
        like = prefix.replace("_", r"\_") + "%"
        like_sql = string_literal(like)
        grants: list[ActualGrant] = []
        enrolled = self.list_enrolled_catalogs(settings)
        catalog_filter = self._catalog_sql(settings, "catalog_name", enrolled)
        table_catalog_filter = self._catalog_sql(settings, "table_catalog", enrolled)
        with timed_call("sql.read_catalog_privileges"):
            catalog_privileges = self._sql.query(
                f"""
                SELECT catalog_name, grantee, privilege_type, inherited_from
                FROM system.information_schema.catalog_privileges
                WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
                  {catalog_filter}
                """
            )
        for row in catalog_privileges:
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "catalog",
                    str(row["catalog_name"]),
                    str(row["grantee"]),
                    str(row["privilege_type"]),
                )
            )
        with timed_call("sql.read_schema_privileges"):
            schema_privileges = self._sql.query(
                f"""
                SELECT catalog_name, schema_name, grantee, privilege_type, inherited_from
                FROM system.information_schema.schema_privileges
                WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
                  {catalog_filter}
                """
            )
        for row in schema_privileges:
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "schema",
                    f"{row['catalog_name']}.{row['schema_name']}",
                    str(row["grantee"]),
                    str(row["privilege_type"]),
                )
            )
        with timed_call("sql.read_table_privileges"):
            table_privileges = self._sql.query(
                f"""
                SELECT table_catalog, table_schema, table_name, grantee, privilege_type, inherited_from
                FROM system.information_schema.table_privileges
                WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
                  {table_catalog_filter}
                """
            )
        for row in table_privileges:
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "table",
                    f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}",
                    str(row["grantee"]),
                    str(row["privilege_type"]),
                )
            )
        return grants

    @staticmethod
    def _is_inherited_privilege(inherited_from: object) -> bool:
        """True when the privilege is inherited from a parent securable.

        ``information_schema`` returns the string ``NONE`` for direct grants; that must
        not be treated as inherited (a truthy check would skip every direct grant and
        prevent REVOKE of stale ``dac_*`` privileges after a tag change).
        """
        if inherited_from is None:
            return False
        text = str(inherited_from).strip()
        return bool(text) and text.upper() != "NONE"

    # Account SCIM — Unity Catalog only recognizes account groups as GRANT principals.
    # Workspace ``groups.create`` yields WorkspaceGroup (PRINCIPAL_DOES_NOT_EXIST on GRANT).
    _ACCOUNT_SCIM_GROUPS = "/api/2.0/account/scim/v2/Groups"
    _ACCOUNT_SCIM_USERS = "/api/2.0/account/scim/v2/Users"

    def _account_scim_list_groups(
        self, *, filter_expr: str | None = None, attributes: str = "id,displayName"
    ) -> list[dict]:
        """List account groups via workspace-proxied Account SCIM."""
        admin = self._factory.admin()
        query: dict[str, str | int] = {
            "attributes": attributes,
            "startIndex": 1,
            "count": 10000,
        }
        if filter_expr:
            query["filter"] = filter_expr
        kind = "scim.list_groups_members" if "members" in attributes else "scim.list_groups"
        with timed_call(kind):
            payload = admin.api_client.do("GET", self._ACCOUNT_SCIM_GROUPS, query=query)
        resources = list((payload.get("Resources") if isinstance(payload, dict) else None) or [])
        total = payload.get("totalResults") if isinstance(payload, dict) else None
        if isinstance(total, int) and total > len(resources):
            message = (
                f"Account SCIM Groups returned {len(resources)} of {total} "
                f"(filter={filter_expr or 'none'}); later pages are not read"
            )
            logger.warning(message)
            note(message)
        return resources

    def _account_scim_find_user(self, user_name: str) -> str | None:
        """SCIM id of the account user with this ``userName``, or None.

        Account users are not all provisioned to this workspace, so the workspace users API
        misses some of them. The account SCIM filter finds every account user.
        """
        admin = self._factory.admin()
        escaped = user_name.replace('"', '\\"')
        try:
            with timed_call("scim.find_user"):
                payload = admin.api_client.do(
                    "GET",
                    self._ACCOUNT_SCIM_USERS,
                    query={
                        "filter": f'userName eq "{escaped}"',
                        "attributes": "id,userName",
                        "count": 1,
                    },
                )
        except DatabricksError:
            return None
        resources = payload.get("Resources") if isinstance(payload, dict) else None
        first = next(iter(resources or []), None)
        return str(first["id"]) if isinstance(first, dict) and first.get("id") else None

    def _account_scim_get_user(self, user_id: str) -> dict | None:
        """Fetch one account user by SCIM id (for canonical userName)."""
        admin = self._factory.admin()
        try:
            with timed_call("scim.get_user"):
                payload = admin.api_client.do(
                    "GET",
                    f"{self._ACCOUNT_SCIM_USERS}/{user_id}",
                    query={"attributes": "id,userName,displayName"},
                )
        except DatabricksError:
            return None
        return payload if isinstance(payload, dict) else None

    def _account_scim_create_group(self, display_name: str) -> str:
        """Create an account group; return its SCIM id."""
        admin = self._factory.admin()
        with timed_call("scim.create_group"):
            created = admin.api_client.do(
                "POST",
                self._ACCOUNT_SCIM_GROUPS,
                body={
                    "displayName": display_name,
                    "schemas": [GroupSchema.URN_IETF_PARAMS_SCIM_SCHEMAS_CORE_2_0_GROUP.value],
                },
            )
        gid = created.get("id") if isinstance(created, dict) else None
        if not gid:
            raise RuntimeError(f"create account group {display_name}: missing id")
        return str(gid)

    def _account_scim_patch_group(self, group_id: str, operations: list[dict]) -> None:
        """Patch an account group (add/remove members)."""
        admin = self._factory.admin()
        with timed_call("scim.patch_group"):
            admin.api_client.do(
                "PATCH",
                f"{self._ACCOUNT_SCIM_GROUPS}/{group_id}",
                body={
                    "schemas": [PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP.value],
                    "Operations": operations,
                },
            )

    def _load_groups(self, settings: DataAccessSettings) -> set[str]:
        """List account groups whose names start with the managed prefix."""
        prefix = settings.group_prefix
        names: set[str] = set()
        try:
            escaped = prefix.replace('"', '\\"')
            for group in self._account_scim_list_groups(filter_expr=f'displayName sw "{escaped}"'):
                name = group.get("displayName")
                if name:
                    names.add(str(name))
        except DatabricksError as exc:
            logger.warning("Listing managed account groups failed: %s", exc)
        return names

    def _load_assignments(self, settings: DataAccessSettings) -> list[AssignmentRow]:
        """Active Identity data-access assignments that map into ``dac_*`` groups."""
        if self._identity is None:
            return []
        definition = self._identity.find_data_access_definition(settings.access_tag_key)
        if definition is None:
            return []
        items = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(attribute_key=definition.key, include_inactive=False)
        )
        return [
            AssignmentRow(
                principal_name=item.principal_name,
                principal_type=item.principal_type.value,
                attribute_value=item.attribute_value,
            )
            for item in items
        ]

    @staticmethod
    def _member_principal_type(entry: dict, display: str, group_prefix: str) -> str:
        """USER, GROUP, or SERVICE_PRINCIPAL for one Account SCIM group member entry.

        Account SCIM members carry ``$ref`` (``Groups/{id}``, ``Users/{id}``) and usually no
        ``type``. Only when neither says anything is the display name guessed from the prefix.
        Guessing wrongly sent group members down the user path, where removals failed.
        """
        explicit = str(entry.get("type") or "").upper()
        if explicit in {"USER", "GROUP", "SERVICE_PRINCIPAL"}:
            return explicit
        ref = str(entry.get("$ref") or "").lower()
        if "/groups/" in ref or ref.startswith("groups/"):
            return "GROUP"
        if "/serviceprincipals/" in ref or ref.startswith("serviceprincipals/"):
            return "SERVICE_PRINCIPAL"
        if "/users/" in ref or ref.startswith("users/"):
            return "USER"
        prefix = group_prefix.lower() if group_prefix.endswith("_") else f"{group_prefix}_".lower()
        return "GROUP" if display.lower().startswith(prefix) else "USER"

    def _load_members(self, settings: DataAccessSettings, existing_groups: set[str]) -> list[ActualMember]:
        """Load current members of managed account groups via Account SCIM.

        Group member entries often expose ``display`` as the person's display name
        (``David Galluzzo``) while Identity assignments store ``userName``
        (``david.galluzzo@databricks.com``). Resolve USER members by SCIM id to
        ``userName`` so the planner does not add-by-email then remove-by-display-name.
        """
        if not existing_groups or not self._factory.admin_configured:
            return []
        members: list[ActualMember] = []
        by_name = {name.lower(): name for name in existing_groups}
        user_names: dict[str, str] = {}
        try:
            escaped = settings.group_prefix.replace('"', '\\"')
            listed = self._account_scim_list_groups(
                filter_expr=f'displayName sw "{escaped}"',
                attributes="id,displayName,members",
            )
            for group in listed:
                name = str(group.get("displayName") or "")
                if name.lower() not in by_name:
                    continue
                for entry in group.get("members") or []:
                    if not isinstance(entry, dict):
                        continue
                    display = entry.get("display") or entry.get("value")
                    if not display:
                        continue
                    member_id = str(entry.get("value") or "").strip()
                    principal_type = self._member_principal_type(entry, str(display), settings.group_prefix)
                    principal_name = str(display)
                    if principal_type == "USER" and member_id:
                        if member_id not in user_names:
                            user = self._account_scim_get_user(member_id)
                            user_names[member_id] = (
                                str(user.get("userName") or display) if user else str(display)
                            )
                        principal_name = user_names[member_id]
                    members.append(
                        ActualMember(
                            group_name=str(name),
                            principal_name=principal_name,
                            principal_type=principal_type,
                        )
                    )
        except DatabricksError as exc:
            logger.warning("Listing managed group members failed: %s", exc)
        return members

    def _apply_plan_impl(self, settings: DataAccessSettings, plan: DataAccessPlan) -> DataAccessPlan:
        """Create/nest account groups, sync membership, GRANT/REVOKE. Collects per-step errors."""
        errors: list[str] = []
        failures: dict[str, str] = {}
        logger.info(
            "apply plan: groups_to_create=%d nests=%d membership=%d grants_add=%d grants_revoke=%d",
            sum(1 for g in plan.groups if not g.exists),
            sum(1 for g in plan.groups if g.parent_group),
            len(plan.membership_changes),
            len(plan.grants_to_add),
            len(plan.grants_to_revoke),
        )
        with phase("apply.groups"):
            group_ids = self._ensure_groups(plan.groups, errors, failures)
        with phase("apply.nest"):
            self._nest_groups(plan.groups, group_ids, errors, failures)
        with phase("apply.membership"):
            self._apply_membership(
                plan.membership_changes,
                group_ids,
                errors,
                failures,
                concurrency=settings.membership_concurrency,
            )
        statements = len(plan.grants_to_add) + len(plan.grants_to_revoke)
        with (
            phase("apply.grants"),
            self._warehouse.capacity_for(
                statements,
                enabled=settings.warehouse_autoscale,
                sync_max_clusters=settings.warehouse_max_clusters,
                baseline_clusters=settings.warehouse_baseline_clusters,
                concurrency_cap=settings.grant_concurrency,
            ) as capacity,
        ):
            self._apply_grants(
                plan.grants_to_add,
                plan.grants_to_revoke,
                errors,
                failures,
                concurrency=capacity.concurrency,
            )
        return plan.model_copy(update={"apply_errors": errors, "apply_failures": failures})

    def _ensure_groups(
        self,
        groups: list[PlannedGroup],
        errors: list[str],
        failures: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Create missing account groups; return display-name(lower) → SCIM id map."""
        ids: dict[str, str] = {}
        try:
            for group in self._account_scim_list_groups():
                name = group.get("displayName")
                gid = group.get("id")
                if name and gid:
                    ids[str(name).lower()] = str(gid)
        except DatabricksError as exc:
            errors.append(f"list account groups: {exc}")
        for planned in groups:
            key = planned.name.lower()
            if key in ids:
                continue
            try:
                ids[key] = self._account_scim_create_group(planned.name)
            except Exception as exc:  # noqa: BLE001
                try:
                    found = next(
                        (
                            g
                            for g in self._account_scim_list_groups(
                                filter_expr=f'displayName eq "{planned.name.replace(chr(34), "")}"'
                            )
                            if str(g.get("displayName") or "").lower() == key
                        ),
                        None,
                    )
                    if found and found.get("id"):
                        ids[key] = str(found["id"])
                        continue
                except Exception:  # noqa: BLE001
                    pass
                errors.append(f"create group {planned.name}: {exc}")
                if failures is not None:
                    failures[group_key(GapKind.GROUP_CREATE, planned.name)] = str(exc)
        return ids

    def _nest_groups(
        self,
        groups: list[PlannedGroup],
        group_ids: dict[str, str],
        errors: list[str],
        failures: dict[str, str] | None = None,
    ) -> None:
        """Nest each parent ``dac_*`` group into its child (idempotent ADD).

        Links the inventory already shows as present (``nest_exists``) are skipped so a
        stable hierarchy does not re-PATCH Account SCIM on every apply.
        """
        for planned in groups:
            if not planned.parent_group or planned.nest_exists:
                continue
            key = nest_key(planned.name, planned.parent_group)
            child_id = group_ids.get(planned.name.lower())
            parent_id = group_ids.get(planned.parent_group.lower())
            if not child_id or not parent_id:
                errors.append(f"nest {planned.parent_group} → {planned.name}: missing group id")
                if failures is not None:
                    failures[key] = "missing group id"
                continue
            try:
                self._account_scim_patch_group(
                    child_id,
                    [{"op": "add", "path": "members", "value": [{"value": parent_id}]}],
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if "already" in message or "exists" in message or "409" in message:
                    continue
                errors.append(f"nest {planned.parent_group} → {planned.name}: {exc}")
                if failures is not None:
                    failures[key] = str(exc)

    def _apply_membership(
        self,
        changes: list[MembershipChange],
        group_ids: dict[str, str],
        errors: list[str],
        failures: dict[str, str] | None = None,
        *,
        concurrency: int = 1,
    ) -> None:
        """Add/remove principals on managed account groups via Account SCIM patch.

        Resolves each change to a SCIM id first, then collapses ADD+REMOVE pairs that
        refer to the same person under different names (email vs display name). Resolve and
        patch both run with a thread pool; patches that share a group stay sequential so two
        edits never race on the same Account SCIM group.
        """
        sink: dict[str, str] = failures if failures is not None else {}
        if not changes:
            return
        workers = max(1, min(concurrency, len(changes)))
        record_size("membership_concurrency", workers)

        resolved: list[tuple[str, str, MembershipChange]] = []
        resolve_progress = Progress("membership.resolve", len(changes))
        cache: dict[tuple[str, str], str | None] = {}
        cache_lock = threading.Lock()

        def resolve_one(
            change: MembershipChange,
        ) -> tuple[MembershipChange, str | None, str | None, Exception | None, float]:
            """Return (change, group_id, member_id, resolve_error, seconds)."""
            group_id = group_ids.get(change.group.lower())
            if not group_id:
                return change, None, None, RuntimeError("missing group"), 0.0
            cache_key = ((change.principal_type or "USER").upper(), change.principal_name.lower())
            with cache_lock:
                if cache_key in cache:
                    cached = cache[cache_key]
                    if cached is None:
                        return (
                            change,
                            group_id,
                            None,
                            RuntimeError(f"resolve principal {change.principal_name}: not found"),
                            0.0,
                        )
                    return change, group_id, cached, None, 0.0
            local_errors: list[str] = []
            started = time.perf_counter()
            member_id = self._lookup_principal_id(
                change.principal_name, (change.principal_type or "USER").upper(), local_errors
            )
            seconds = time.perf_counter() - started
            with cache_lock:
                cache[cache_key] = member_id
            if member_id is None:
                message = local_errors[-1] if local_errors else "principal not found"
                return change, group_id, None, RuntimeError(message), seconds
            return change, group_id, member_id, None, seconds

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="member-resolve") as pool:
            futures = [pool.submit(resolve_one, change) for change in changes]
            for future in as_completed(futures):
                change, group_id, member_id, resolve_error, seconds = future.result()
                kind = (change.principal_type or "USER").lower()
                if seconds > 0:
                    record_call(f"scim.resolve_{kind}", seconds)
                key = member_key(change.action, change.group, change.principal_name)
                if resolve_error is not None and group_id is None:
                    errors.append(f"membership {change.action.value} {change.principal_name}: missing group")
                    sink[key] = "missing group"
                elif resolve_error is not None:
                    errors.append(str(resolve_error))
                    sink[key] = str(resolve_error)
                else:
                    assert group_id is not None and member_id is not None
                    resolved.append((group_id, member_id, change))
                resolve_progress.tick()

        collapsed = collapse_membership_by_principal_id(resolved)
        if not collapsed:
            return

        by_group: dict[str, list[tuple[str, MembershipChange]]] = {}
        for group_id, member_id, change in collapsed:
            by_group.setdefault(group_id, []).append((member_id, change))
        patch_progress = Progress("membership.patch", len(collapsed))
        patch_workers = max(1, min(workers, len(by_group)))

        def patch_one_group(
            group_id: str, items: list[tuple[str, MembershipChange]]
        ) -> list[tuple[MembershipChange, Exception | None, float]]:
            """Apply every patch for one group in order; return per-change outcomes."""
            outcomes: list[tuple[MembershipChange, Exception | None, float]] = []
            for member_id, change in items:
                started = time.perf_counter()
                try:
                    if change.action == MembershipAction.ADD:
                        self._account_scim_patch_group(
                            group_id,
                            [{"op": "add", "path": "members", "value": [{"value": member_id}]}],
                        )
                    else:
                        self._account_scim_patch_group(
                            group_id,
                            [{"op": "remove", "path": f'members[value eq "{member_id}"]'}],
                        )
                    outcomes.append((change, None, time.perf_counter() - started))
                except Exception as exc:  # noqa: BLE001 - one failed patch must not stop the rest
                    message = str(exc).lower()
                    if change.action == MembershipAction.ADD and (
                        "already" in message or "exists" in message
                    ):
                        outcomes.append((change, None, time.perf_counter() - started))
                    else:
                        outcomes.append((change, exc, time.perf_counter() - started))
            return outcomes

        with ThreadPoolExecutor(max_workers=patch_workers, thread_name_prefix="member-patch") as pool:
            futures = {
                pool.submit(patch_one_group, group_id, items): group_id
                for group_id, items in by_group.items()
            }
            for future in as_completed(futures):
                for change, exc, seconds in future.result():
                    record_call("scim.patch_group", seconds)
                    if exc is not None:
                        errors.append(
                            f"membership {change.action.value} {change.principal_name} "
                            f"on {change.group}: {exc}"
                        )
                        sink[member_key(change.action, change.group, change.principal_name)] = str(exc)
                    patch_progress.tick()

    def _resolve_principal_id(self, name: str, principal_type: str, errors: list[str]) -> str | None:
        """Resolve a user/group/SP display name to a SCIM id."""
        kind = (principal_type or "USER").upper()
        with timed_call(f"scim.resolve_{kind.lower()}"):
            return self._lookup_principal_id(name, kind, errors)

    def _lookup_principal_id(self, name: str, kind: str, errors: list[str]) -> str | None:
        """Look up one principal's SCIM id for ``_resolve_principal_id``."""
        admin = self._factory.admin()
        escaped = name.replace('"', '\\"')
        try:
            if kind == "GROUP":
                found = next(
                    (g for g in self._account_scim_list_groups(filter_expr=f'displayName eq "{escaped}"')),
                    None,
                )
                if found and found.get("id"):
                    return str(found["id"])
                errors.append(f"resolve principal {name}: account group not found")
                return None
            if kind == "SERVICE_PRINCIPAL":
                sps = admin.service_principals.list(
                    filter=f'applicationId eq "{escaped}" or displayName eq "{escaped}"',
                    attributes="id,applicationId,displayName",
                )
                found = next(iter(sps), None)
                return str(found.id) if found and found.id else None
            account_user = self._account_scim_find_user(name)
            if account_user:
                return account_user
            # Fallback for users the account knows but the search above missed.
            users = admin.users.list(
                filter=f'userName eq "{escaped}" or displayName eq "{escaped}"',
                attributes="id,userName,displayName",
            )
            found = next(iter(users), None)
            if found and found.id:
                return str(found.id)
            errors.append(f"resolve principal {name}: not found")
            return None
        except Exception as exc:  # noqa: BLE001
            errors.append(f"resolve principal {name}: {exc}")
            return None

    def _apply_grants(
        self,
        to_add: list[PlannedGrant],
        to_revoke: list[PlannedGrant],
        errors: list[str],
        failures: dict[str, str] | None = None,
        *,
        concurrency: int = 1,
    ) -> None:
        """Issue GRANT / REVOKE SQL for planned privileges, up to ``concurrency`` at a time."""
        sink: dict[str, str] = failures if failures is not None else {}
        jobs: list[tuple[str, GapKind, PlannedGrant]] = [
            ("GRANT", GapKind.GRANT_ADD, grant) for grant in to_add
        ] + [("REVOKE", GapKind.GRANT_REVOKE, grant) for grant in to_revoke]
        if not jobs:
            return
        workers = max(1, min(concurrency, len(jobs)))
        record_size("grant_concurrency", workers)
        progress = Progress("grants", len(jobs))

        def run(job: tuple[str, GapKind, PlannedGrant]) -> tuple[Exception | None, float]:
            verb, _kind, grant = job
            started = time.perf_counter()
            try:
                self._sql.execute(self._grant_sql(verb, grant))
                return None, time.perf_counter() - started
            except Exception as exc:  # noqa: BLE001 - one failed grant must not stop the rest
                return exc, time.perf_counter() - started

        # Statements are independent per securable and principal, so run them concurrently. The
        # warehouse queues whatever it cannot run yet. Results are handled on this thread.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grant") as pool:
            futures = {pool.submit(run, job): job for job in jobs}
            for future in as_completed(futures):
                verb, kind, grant = futures[future]
                exc, seconds = future.result()
                record_call(f"sql.{verb.lower()}_{grant.securable_type.lower()}", seconds)
                if exc is not None:
                    errors.append(f"{verb} {grant.privilege} on {grant.full_name}: {exc}")
                    sink[grant_key(kind, grant)] = str(exc)
                progress.tick()

    @staticmethod
    def _grant_sql(verb: str, grant: PlannedGrant) -> str:
        """Build GRANT/REVOKE SQL for one planned privilege."""
        parts = grant.full_name.split(".")
        quoted_name = ".".join(quote_identifier(p) for p in parts)
        principal = f"`{grant.principal.replace('`', '')}`"
        securable = grant.securable_type.upper()
        if verb == "GRANT":
            return f"GRANT {grant.privilege} ON {securable} {quoted_name} TO {principal}"
        return f"REVOKE {grant.privilege} ON {securable} {quoted_name} FROM {principal}"

    def load_tags_public(self, settings: DataAccessSettings | None = None) -> list[TagRow]:
        """Expose tag inventory for Attribute Explorer (same loader as sync)."""
        return self._tag_loader(settings or self._settings())

    def _catalog_sql(
        self,
        settings: DataAccessSettings,
        column: str,
        enrolled: list[str] | None = None,
    ) -> str:
        """Return an AND-clause limiting ``column`` to enrollment-tagged, non-system catalogs.

        Fail closed: when no catalogs carry the enrollment tag, the clause matches nothing.
        """
        del settings  # Perimeter is enrollment tags only; kept for call-site consistency.
        excluded = " AND ".join(f"lower({column}) <> {string_literal(name)}" for name in _EXCLUDED_CATALOGS)
        clause = f"AND {excluded}" if excluded else ""
        catalogs = enrolled if enrolled is not None else []
        if not catalogs:
            return f"{clause} AND 1 = 0"
        allowed = ", ".join(string_literal(c.lower()) for c in catalogs)
        return f"{clause} AND lower({column}) IN ({allowed})"

    def _check_credentials(self) -> ReadinessCheck:
        """Return whether Account Admin SP credentials are present."""
        ok = self._factory.admin_configured
        return ReadinessCheck(
            id="credentials",
            label="Account Admin SP credentials",
            ok=ok,
            required_for="dry_run",
            detail="Present" if ok else "client_id / client_secret are missing",
        )

    def _check_authenticate(self) -> ReadinessCheck:
        """Call current-user as the Account Admin SP."""
        if not self._factory.admin_configured:
            return ReadinessCheck(
                id="authenticate",
                label="Authenticate as Account Admin SP",
                ok=False,
                required_for="dry_run",
                detail="Skipped; credentials are missing",
            )
        try:
            me = self._factory.admin().current_user.me()
            name = getattr(me, "user_name", None) or getattr(me, "display_name", None) or "ok"
            return ReadinessCheck(
                id="authenticate",
                label="Authenticate as Account Admin SP",
                ok=True,
                required_for="dry_run",
                detail=str(name),
            )
        except DatabricksError as exc:
            return ReadinessCheck(
                id="authenticate",
                label="Authenticate as Account Admin SP",
                ok=False,
                required_for="dry_run",
                detail=str(exc),
            )

    def _check_warehouse(self) -> ReadinessCheck:
        """Return whether a SQL warehouse is configured."""
        ok = self._sql.configured
        return ReadinessCheck(
            id="warehouse",
            label="SQL warehouse",
            ok=ok,
            required_for="dry_run",
            detail="Configured" if ok else "DATABRICKS_WAREHOUSE_ID is not set",
        )

    def _check_read_tags(self) -> ReadinessCheck:
        """SELECT from catalog_tags to confirm tag inventory works."""
        if not self._sql.configured:
            return ReadinessCheck(
                id="read_tags",
                label="Read Unity Catalog tags",
                ok=False,
                required_for="dry_run",
                detail="Skipped; SQL warehouse is not configured",
            )
        try:
            self._sql.query("SELECT catalog_name FROM system.information_schema.catalog_tags LIMIT 1")
            return ReadinessCheck(
                id="read_tags",
                label="Read Unity Catalog tags",
                ok=True,
                required_for="dry_run",
                detail="information_schema.catalog_tags is readable",
            )
        except (NotConfiguredError, DatabricksError) as exc:
            return ReadinessCheck(
                id="read_tags",
                label="Read Unity Catalog tags",
                ok=False,
                required_for="dry_run",
                detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - surface any warehouse/SQL failure
            return ReadinessCheck(
                id="read_tags",
                label="Read Unity Catalog tags",
                ok=False,
                required_for="dry_run",
                detail=str(exc),
            )

    def _check_list_groups(self) -> ReadinessCheck:
        """List groups as the Account Admin SP (needed to know which dac_ groups exist)."""
        if not self._factory.admin_configured:
            return ReadinessCheck(
                id="list_groups",
                label="List account groups",
                ok=False,
                required_for="dry_run",
                detail="Skipped; credentials are missing",
            )
        try:
            next(iter(self._factory.admin().groups.list(attributes="id,displayName")), None)
            return ReadinessCheck(
                id="list_groups",
                label="List account groups",
                ok=True,
                required_for="dry_run",
                detail="Group directory is readable",
            )
        except (DatabricksError, AttributeError, Exception) as exc:  # noqa: BLE001
            return ReadinessCheck(
                id="list_groups",
                label="List account groups",
                ok=False,
                required_for="dry_run",
                detail=str(exc),
            )

    def _check_create_groups(self) -> ReadinessCheck:
        """Probe whether the SP can create groups without creating one.

        Databricks does not expose a dedicated "can create" call; a successful list plus
        Account Admin credentials is treated as likely, and apply mode stays locked anyway.
        """
        if not self._factory.admin_configured:
            return ReadinessCheck(
                id="create_groups",
                label="Create account groups",
                ok=False,
                required_for="apply",
                detail="Skipped; credentials are missing",
            )
        return ReadinessCheck(
            id="create_groups",
            label="Create account groups",
            ok=True,
            required_for="apply",
            detail="Not exercised in dry-run; Account Admin SP is assumed able to create groups",
        )

    def _check_grants(self) -> ReadinessCheck:
        """Read grants on the app catalog as a GRANT/REVOKE proxy check."""
        if not self._sql.configured:
            return ReadinessCheck(
                id="grants",
                label="Read and manage Unity Catalog grants",
                ok=False,
                required_for="apply",
                detail="Skipped; SQL warehouse is not configured",
            )
        try:
            self._sql.query("SELECT grantee FROM system.information_schema.catalog_privileges LIMIT 1")
            return ReadinessCheck(
                id="grants",
                label="Read and manage Unity Catalog grants",
                ok=True,
                required_for="apply",
                detail="Privilege views are readable; GRANT/REVOKE is not issued in dry-run",
            )
        except Exception as exc:  # noqa: BLE001 - surface warehouse/SQL failure
            return ReadinessCheck(
                id="grants",
                label="Read and manage Unity Catalog grants",
                ok=False,
                required_for="apply",
                detail=str(exc),
            )

"""Data-access group sync: settings, Account Admin SP readiness, dry-run, and apply.

Reads catalog/schema/table tags, current grants, and Identity assignments; plans managed
``dac_*`` groups (nesting, membership, GRANT/REVOKE). Apply mutates account groups and UC.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.iam import GroupSchema, PatchSchema
from pydantic import ValidationError

from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, NotConfiguredError
from backend.app.models.data_access import (
    MIN_SYNC_INTERVAL_MINUTES,
    RUN_HISTORY_LIMIT,
    RUN_HISTORY_PAGE_SIZE_DEFAULT,
    RUN_HISTORY_PAGE_SIZE_MAX,
    RUN_HISTORY_RETENTION_DAYS,
    DataAccessPlan,
    DataAccessReadiness,
    DataAccessRun,
    DataAccessRunList,
    DataAccessSettings,
    DataAccessSettingsView,
    MembershipAction,
    MembershipChange,
    PlannedGrant,
    PlannedGroup,
    ReadinessCheck,
    SyncRunStatus,
    SyncTrigger,
)
from backend.app.models.identity_attribute import AttributeAssignmentFilter
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.data_access_planner import (
    ActualGrant,
    ActualMember,
    AssignmentRow,
    TagRow,
    plan_data_access,
)
from backend.app.services.identity_attributes import IdentityAttributeService

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "data_access"
_RUNS_KEY = "data_access_runs"
_LOCK_KEY = "data_access_lock"
_LOCK_STALE = timedelta(minutes=30)
_EXCLUDED_CATALOGS = ("system", "samples", "__databricks_internal")


class DataAccessService:
    """Settings, readiness probes, and sync orchestration for data-access groups.

    Args:
        storage: App state (settings JSON for config and run history).
        sql: Warehouse executor running as the Account Admin SP.
        factory: Workspace clients for readiness probes and group listing.
        identity_attributes: Optional Identity service for governance setup checks.
    """

    def __init__(
        self,
        storage: StorageProvider,
        sql: SqlExecutor,
        factory: WorkspaceClientFactory,
        identity_attributes: IdentityAttributeService | None = None,
    ) -> None:
        self._storage = storage
        self._sql = sql
        self._factory = factory
        self._identity = identity_attributes
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Tests replace inventory loaders without a warehouse.
        self._tag_loader = self._load_tags
        self._grant_loader = self._load_grants
        self._group_loader = self._load_groups
        self._member_loader = self._load_members
        self._assignment_loader = self._load_assignments
        self._apply_plan = self._apply_plan_impl

    def get_settings(self) -> DataAccessSettingsView:
        """Return stored settings or defaults, plus UI constants.

        Returns:
            Settings view. Invalid stored JSON falls back to defaults and is logged.
        """
        return DataAccessSettingsView(settings=self._settings(), apply_available=True)

    def save_settings(self, body: DataAccessSettings, actor: CurrentUser) -> DataAccessSettingsView:
        """Validate and persist settings (including preferred dry_run flag).

        Args:
            body: Settings posted by an admin.
            actor: Admin performing the write.

        Returns:
            The stored settings view.
        """
        self._storage.settings.put_many({_SETTINGS_KEY: body.model_dump(mode="json")}, actor.user_name)
        logger.info("Data-access settings updated by %s", actor.user_name)
        return DataAccessSettingsView(settings=body, apply_available=True)

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
    ) -> DataAccessRunList:
        """Return paginated sync runs for the last retention window.

        Args:
            mode: Optional ``dry_run`` or ``apply`` filter.
            page: 1-based page index.
            page_size: Rows per page (clamped).

        Returns:
            Summaries only (no full plans), newest first.
        """
        mode_filter = self._normalize_mode_filter(mode)
        page = max(1, page)
        page_size = min(max(1, page_size), RUN_HISTORY_PAGE_SIZE_MAX)
        filtered = self._filtered_runs(mode=mode_filter)
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
        """Run a sync as the signed-in admin.

        Args:
            actor: Admin who clicked Run now.
            mode: ``dry_run`` or ``apply``.

        Returns:
            The finished run (success or failed).

        Raises:
            InvalidRequestError: If another run holds the lock or apply is not ready.
        """
        return self.run_sync(actor=actor.user_name, trigger=SyncTrigger.USER, mode=mode)

    def run_sync(
        self, *, actor: str, trigger: SyncTrigger, mode: str = "dry_run"
    ) -> DataAccessRun:
        """Inventory, plan, optionally apply, and persist the run.

        Args:
            actor: User name or ``scheduler``.
            trigger: Manual or scheduled.
            mode: ``dry_run`` (plan only) or ``apply`` (mutate groups/grants/members).

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
                raise InvalidRequestError(
                    "Apply requires sensitivity and data-access Identity definitions"
                )
        run_id = str(uuid.uuid4())
        started = datetime.now(UTC)
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
        try:
            settings = self._settings()
            tags = self._tag_loader(settings)
            grants = self._grant_loader(settings)
            groups = self._group_loader(settings)
            assignments = self._assignment_loader(settings)
            members = self._member_loader(settings, groups)
            previous = next(
                (
                    r
                    for r in self._filtered_runs(mode=mode)
                    if r.status == SyncRunStatus.SUCCESS
                ),
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
            if mode == "apply":
                plan = self._apply_plan(settings, plan)
            finished = running.model_copy(
                update={
                    "status": SyncRunStatus.SUCCESS,
                    "finished_at": datetime.now(UTC),
                    "summary": plan.summary,
                    "plan": plan,
                    "fingerprint": plan.fingerprint,
                    "error": "; ".join(plan.apply_errors) if plan.apply_errors else None,
                }
            )
            self._write_run(finished)
            logger.info(
                "Data-access %s %s finished: add=%s revoke=%s members+=%s members-=%s conflicts=%s",
                mode,
                run_id,
                plan.summary.grants_to_add,
                plan.summary.grants_to_revoke,
                plan.summary.members_to_add,
                plan.summary.members_to_remove,
                plan.summary.conflicts,
            )
            return finished
        except Exception as exc:  # noqa: BLE001 - persist failure, then re-raise as run record
            failed = running.model_copy(
                update={
                    "status": SyncRunStatus.FAILED,
                    "finished_at": datetime.now(UTC),
                    "error": str(exc),
                }
            )
            self._write_run(failed)
            logger.exception("Data-access %s %s failed", mode, run_id)
            return failed
        finally:
            self._release_lock(run_id)

    def start_scheduler(self) -> None:
        """Start the background dry-run loop if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, name="data-access-sync", daemon=True)
        self._thread.start()

    def stop_scheduler(self) -> None:
        """Signal the scheduler thread to exit and wait briefly."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _scheduler_loop(self) -> None:
        """Sleep for the interval, then dry-run or apply per settings when enabled.

        Apply mode still plans first (inventory → plan → mutate) in one run — the same
        path as Live → Run apply. Readiness failures and lock contention skip the tick.
        """
        while not self._stop.wait(self._interval_seconds()):
            settings = self._settings()
            if not settings.schedule_enabled:
                logger.debug("Scheduled data-access sync paused (schedule_enabled=false)")
                continue
            mode = (settings.schedule_mode.value if settings.schedule_mode else "dry_run").lower()
            if mode not in {"dry_run", "apply"}:
                mode = "dry_run"
            try:
                self.run_sync(actor="scheduler", trigger=SyncTrigger.SCHEDULE, mode=mode)
            except InvalidRequestError as exc:
                logger.info("Skipping scheduled data-access %s: %s", mode, exc)
            except Exception:
                logger.exception("Scheduled data-access %s failed", mode)

    def _interval_seconds(self) -> float:
        """Return the current scheduler sleep in seconds (at least the minimum)."""
        minutes = max(self._settings().interval_minutes, MIN_SYNC_INTERVAL_MINUTES)
        return float(minutes * 60)

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

    def _filtered_runs(self, *, mode: str | None = None) -> list[DataAccessRun]:
        """Runs in the retention window, optionally filtered by mode, newest first."""
        cutoff = self._retention_cutoff()
        runs = [r for r in self._runs() if r.started_at >= cutoff]
        if mode:
            runs = [r for r in runs if (r.mode or "dry_run").lower() == mode]
        return runs

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
            existing = [
                r
                for r in self._runs()
                if r.run_id != run.run_id and r.started_at >= cutoff
            ]
            blob = [run.model_dump(mode="json"), *[r.model_dump(mode="json") for r in existing]]
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
        catalog_filter = self._catalog_sql(settings, "catalog_name")
        rows: list[TagRow] = []
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
        schema_filter = self._catalog_sql(settings, "catalog_name")
        schema_rows = self._sql.query(
            f"""
            SELECT catalog_name, schema_name, tag_name, tag_value
            FROM system.information_schema.schema_tags
            WHERE lower(tag_name) IN ({keys})
              {schema_filter}
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
        table_rows = self._sql.query(
            f"""
            SELECT catalog_name, schema_name, table_name, tag_name, tag_value
            FROM system.information_schema.table_tags
            WHERE lower(tag_name) IN ({keys})
              {self._catalog_sql(settings, "catalog_name")}
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
        catalog_filter = self._catalog_sql(settings, "catalog_name")
        for row in self._sql.query(
            f"""
            SELECT catalog_name, grantee, privilege_type, inherited_from
            FROM system.information_schema.catalog_privileges
            WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
              {catalog_filter}
            """
        ):
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "catalog",
                    str(row["catalog_name"]),
                    str(row["grantee"]),
                    str(row["privilege_type"]).upper(),
                )
            )
        for row in self._sql.query(
            f"""
            SELECT catalog_name, schema_name, grantee, privilege_type, inherited_from
            FROM system.information_schema.schema_privileges
            WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
              {self._catalog_sql(settings, "catalog_name")}
            """
        ):
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "schema",
                    f"{row['catalog_name']}.{row['schema_name']}",
                    str(row["grantee"]),
                    str(row["privilege_type"]).upper(),
                )
            )
        for row in self._sql.query(
            f"""
            SELECT table_catalog, table_schema, table_name, grantee, privilege_type, inherited_from
            FROM system.information_schema.table_privileges
            WHERE lower(grantee) LIKE {like_sql} ESCAPE '\\\\'
              {self._catalog_sql(settings, "table_catalog")}
            """
        ):
            if self._is_inherited_privilege(row.get("inherited_from")):
                continue
            grants.append(
                ActualGrant(
                    "table",
                    f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}",
                    str(row["grantee"]),
                    str(row["privilege_type"]).upper(),
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
        payload = admin.api_client.do("GET", self._ACCOUNT_SCIM_GROUPS, query=query)
        resources = payload.get("Resources") if isinstance(payload, dict) else None
        return list(resources or [])

    def _account_scim_create_group(self, display_name: str) -> str:
        """Create an account group; return its SCIM id."""
        admin = self._factory.admin()
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
            for group in self._account_scim_list_groups(
                filter_expr=f'displayName sw "{escaped}"'
            ):
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

    def _load_members(
        self, settings: DataAccessSettings, existing_groups: set[str]
    ) -> list[ActualMember]:
        """Load current members of managed account groups via Account SCIM."""
        if not existing_groups or not self._factory.admin_configured:
            return []
        members: list[ActualMember] = []
        by_name = {name.lower(): name for name in existing_groups}
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
                    raw_type = str(entry.get("type") or "UNKNOWN").upper()
                    if raw_type in {"USER", "GROUP", "SERVICE_PRINCIPAL"}:
                        principal_type = raw_type
                    else:
                        principal_type = (
                            "GROUP"
                            if str(display).lower().startswith(
                                settings.group_prefix.lower()
                                if settings.group_prefix.endswith("_")
                                else f"{settings.group_prefix}_".lower()
                            )
                            else "USER"
                        )
                    members.append(
                        ActualMember(
                            group_name=str(name),
                            principal_name=str(display),
                            principal_type=principal_type,
                        )
                    )
        except DatabricksError as exc:
            logger.warning("Listing managed group members failed: %s", exc)
        return members

    def _apply_plan_impl(self, settings: DataAccessSettings, plan: DataAccessPlan) -> DataAccessPlan:
        """Create/nest account groups, sync membership, GRANT/REVOKE. Collects per-step errors."""
        errors: list[str] = []
        group_ids = self._ensure_groups(plan.groups, errors)
        self._nest_groups(plan.groups, group_ids, errors)
        self._apply_membership(plan.membership_changes, group_ids, errors)
        self._apply_grants(plan.grants_to_add, plan.grants_to_revoke, errors)
        return plan.model_copy(update={"apply_errors": errors})

    def _ensure_groups(
        self, groups: list[PlannedGroup], errors: list[str]
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
        return ids

    def _nest_groups(
        self,
        groups: list[PlannedGroup],
        group_ids: dict[str, str],
        errors: list[str],
    ) -> None:
        """Nest each parent ``dac_*`` group into its child (idempotent ADD)."""
        for planned in groups:
            if not planned.parent_group:
                continue
            child_id = group_ids.get(planned.name.lower())
            parent_id = group_ids.get(planned.parent_group.lower())
            if not child_id or not parent_id:
                errors.append(
                    f"nest {planned.parent_group} → {planned.name}: missing group id"
                )
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

    def _apply_membership(
        self,
        changes: list[MembershipChange],
        group_ids: dict[str, str],
        errors: list[str],
    ) -> None:
        """Add/remove principals on managed account groups via Account SCIM patch."""
        for change in changes:
            group_id = group_ids.get(change.group.lower())
            if not group_id:
                errors.append(f"membership {change.action.value} {change.principal_name}: missing group")
                continue
            member_id = self._resolve_principal_id(
                change.principal_name, change.principal_type, errors
            )
            if not member_id:
                continue
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
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if change.action == MembershipAction.ADD and (
                    "already" in message or "exists" in message
                ):
                    continue
                errors.append(
                    f"membership {change.action.value} {change.principal_name} "
                    f"on {change.group}: {exc}"
                )

    def _resolve_principal_id(
        self, name: str, principal_type: str, errors: list[str]
    ) -> str | None:
        """Resolve a user/group/SP display name to a SCIM id."""
        admin = self._factory.admin()
        kind = (principal_type or "USER").upper()
        escaped = name.replace('"', '\\"')
        try:
            if kind == "GROUP":
                found = next(
                    (
                        g
                        for g in self._account_scim_list_groups(
                            filter_expr=f'displayName eq "{escaped}"'
                        )
                    ),
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
    ) -> None:
        """Issue GRANT / REVOKE SQL for planned privileges."""
        for grant in to_add:
            sql = self._grant_sql("GRANT", grant)
            try:
                self._sql.execute(sql)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"GRANT {grant.privilege} on {grant.full_name}: {exc}")
        for grant in to_revoke:
            sql = self._grant_sql("REVOKE", grant)
            try:
                self._sql.execute(sql)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"REVOKE {grant.privilege} on {grant.full_name}: {exc}")

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

    def _catalog_sql(self, settings: DataAccessSettings, column: str) -> str:
        """Return an AND-clause limiting ``column`` to allowed, non-system catalogs."""
        excluded = " AND ".join(f"lower({column}) <> {string_literal(name)}" for name in _EXCLUDED_CATALOGS)
        clause = f"AND {excluded}" if excluded else ""
        if not settings.allowed_catalogs:
            return clause
        allowed = ", ".join(string_literal(c.lower()) for c in settings.allowed_catalogs)
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

"""Data-access group sync: settings, Account Admin SP readiness, and dry-run runs.

Reads catalog/schema/table tags and current grants, plans managed ``dac_*`` groups, and
stores the report. Never creates groups or issues GRANT/REVOKE while apply mode is locked.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from databricks.sdk.errors import DatabricksError
from pydantic import ValidationError

from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, NotConfiguredError
from backend.app.models.data_access import (
    MIN_SYNC_INTERVAL_MINUTES,
    RUN_HISTORY_LIMIT,
    DataAccessReadiness,
    DataAccessRun,
    DataAccessSettings,
    DataAccessSettingsView,
    ReadinessCheck,
    SyncRunStatus,
    SyncTrigger,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.data_access_planner import ActualGrant, TagRow, plan_data_access
from backend.app.services.identity_attributes import IdentityAttributeService

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "data_access"
_RUNS_KEY = "data_access_runs"
_LOCK_KEY = "data_access_lock"
_LOCK_STALE = timedelta(minutes=30)
_EXCLUDED_CATALOGS = ("system", "samples", "__databricks_internal")


class DataAccessService:
    """Settings, readiness probes, and dry-run orchestration for data-access groups.

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

    def get_settings(self) -> DataAccessSettingsView:
        """Return stored settings or defaults, plus UI constants.

        Returns:
            Settings view. Invalid stored JSON falls back to defaults and is logged.
        """
        return DataAccessSettingsView(settings=self._settings())

    def save_settings(self, body: DataAccessSettings, actor: CurrentUser) -> DataAccessSettingsView:
        """Validate and persist settings. Apply mode cannot be enabled yet.

        Args:
            body: Settings posted by an admin.
            actor: Admin performing the write.

        Returns:
            The stored settings view.

        Raises:
            InvalidRequestError: If apply is requested or validation fails.
        """
        payload = body.model_copy(update={"dry_run": True})
        if not body.dry_run:
            raise InvalidRequestError("Apply mode is not available yet; dry-run only")
        self._storage.settings.put_many({_SETTINGS_KEY: payload.model_dump(mode="json")}, actor.user_name)
        logger.info("Data-access settings updated by %s", actor.user_name)
        return DataAccessSettingsView(settings=payload)

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

    def list_runs(self) -> list[DataAccessRun]:
        """Return recent sync runs, newest first, without full plans.

        Returns:
            Up to ``RUN_HISTORY_LIMIT`` runs.
        """
        return [r.model_copy(update={"plan": None}) for r in self._runs()]

    def latest_run(self) -> DataAccessRun | None:
        """Return the most recent run including its truncated plan.

        Returns:
            The newest run, or None when none have been stored.
        """
        runs = self._runs()
        return runs[0] if runs else None

    def run_now(self, actor: CurrentUser) -> DataAccessRun:
        """Run a dry-run sync as the signed-in admin.

        Args:
            actor: Admin who clicked Run now.

        Returns:
            The finished run (success or failed).

        Raises:
            InvalidRequestError: If another run holds the lock.
        """
        return self.run_sync(actor=actor.user_name, trigger=SyncTrigger.USER)

    def run_sync(self, *, actor: str, trigger: SyncTrigger) -> DataAccessRun:
        """Execute one dry-run: inventory, plan, persist, never mutate Unity Catalog.

        Args:
            actor: User name or ``scheduler``.
            trigger: Manual or scheduled.

        Returns:
            The stored run.

        Raises:
            InvalidRequestError: If a run is already in progress.
        """
        run_id = str(uuid.uuid4())
        started = datetime.now(UTC)
        if not self._acquire_lock(run_id, started):
            raise InvalidRequestError("A data-access sync is already running")
        running = DataAccessRun(
            run_id=run_id,
            status=SyncRunStatus.RUNNING,
            trigger=trigger,
            started_at=started,
            triggered_by=actor,
        )
        self._write_run(running)
        try:
            settings = self._settings()
            tags = self._tag_loader(settings)
            grants = self._grant_loader(settings)
            groups = self._group_loader(settings)
            previous = next((r for r in self._runs() if r.status == SyncRunStatus.SUCCESS), None)
            plan = plan_data_access(
                settings,
                tags,
                grants,
                groups,
                previous.fingerprint if previous else None,
                previous.summary if previous else None,
            )
            finished = running.model_copy(
                update={
                    "status": SyncRunStatus.SUCCESS,
                    "finished_at": datetime.now(UTC),
                    "summary": plan.summary,
                    "plan": plan,
                    "fingerprint": plan.fingerprint,
                }
            )
            self._write_run(finished)
            logger.info(
                "Data-access dry-run %s finished: add=%s revoke=%s conflicts=%s",
                run_id,
                plan.summary.grants_to_add,
                plan.summary.grants_to_revoke,
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
            logger.exception("Data-access dry-run %s failed", run_id)
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
        """Sleep for the configured interval, then dry-run, until stopped."""
        while not self._stop.wait(self._interval_seconds()):
            try:
                self.run_sync(actor="scheduler", trigger=SyncTrigger.SCHEDULE)
            except InvalidRequestError:
                logger.info("Skipping scheduled data-access dry-run; another run holds the lock")
            except Exception:
                logger.exception("Scheduled data-access dry-run failed")

    def _interval_seconds(self) -> float:
        """Return the current scheduler sleep in seconds (at least the minimum)."""
        minutes = max(self._settings().interval_minutes, MIN_SYNC_INTERVAL_MINUTES)
        return float(minutes * 60)

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
        """Upsert ``run`` at the front of history, dropping the oldest past the cap."""
        with self._lock:
            existing = [r for r in self._runs() if r.run_id != run.run_id]
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
            if row.get("inherited_from"):
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
            if row.get("inherited_from"):
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
            if row.get("inherited_from"):
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

    def _load_groups(self, settings: DataAccessSettings) -> set[str]:
        """List workspace/account groups whose names start with the managed prefix."""
        prefix = settings.group_prefix
        names: set[str] = set()
        try:
            escaped = prefix.replace('"', '\\"')
            listed = self._factory.admin().groups.list(
                filter=f'displayName sw "{escaped}"', attributes="id,displayName"
            )
            for group in listed:
                name = getattr(group, "display_name", None) or getattr(group, "displayName", None)
                if name:
                    names.add(str(name))
        except DatabricksError as exc:
            logger.warning("Listing managed groups failed: %s", exc)
        return names

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

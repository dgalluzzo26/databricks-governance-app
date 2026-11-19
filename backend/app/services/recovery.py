"""Recovery export and import: snapshot app-owned state as YAML for Git-backed disaster recovery.

``build_preview`` / ``push`` write a snapshot to Git. ``import_from_git`` / ``recover`` read that
snapshot back and upsert it into app storage (for seeding a new app in another workspace). The
joinable salt is reported as configured but never exported or restored, and secret-looking keys
are redacted. The Git token is read from the admin secret scope at push/import time only; it is
never stored, logged, or returned.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml
from pydantic import ValidationError

from backend.app.clients.git_publisher import GitPublisher, GitPublishError, PullOutcome
from backend.app.clients.secrets import admin_secret_scope, decode_secret_value
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.access_request import CategoryApprover
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    IdentityAttributeAssignment,
)
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.models.policy_exception import PolicyException
from backend.app.models.recovery import (
    RecoveryApplyCounts,
    RecoveryApplyRequest,
    RecoveryApplyResult,
    RecoveryFile,
    RecoveryGitSettings,
    RecoveryImportPreview,
    RecoveryManifestSummary,
    RecoveryOccupancy,
    RecoveryPreview,
    RecoveryPushResult,
)
from backend.app.models.subscription import Subscription
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.mask_rules import JOINABLE_SALT_KEY
from backend.app.services.tasks import step, task_summary

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
SETTINGS_KEY = "recovery_git"
SCHEDULE_STATE_KEY = "recovery_git_schedule"
LOCK_KEY = "recovery_git_lock"
DATA_ACCESS_SETTINGS_KEY = "data_access"
APP_SETTINGS_KEY = "app_runtime"
REDACTED = "<redacted>"
_SECRET_KEY_HINT = re.compile(r"secret|token|password|credential", re.IGNORECASE)

# Writable Git target fields persisted under SETTINGS_KEY (status lives under SCHEDULE_STATE_KEY).
_WRITABLE_SETTINGS_KEYS = (
    "repo_url",
    "branch",
    "path_prefix",
    "username",
    "token_secret_key",
    "schedule_enabled",
    "interval_hours",
)

_LOCK_STALE = timedelta(minutes=15)
_TICK_SECONDS = 60.0

_SCHEDULER_ACTOR = CurrentUser(
    user_name="scheduler",
    email="governance-app@local",
    display_name="Recovery scheduler",
    is_admin=True,
)

EXPECTED_FILES = (
    "manifest.yaml",
    "settings/data_access.yaml",
    "identity/attribute_definitions.yaml",
    "identity/assignments.yaml",
    "approvals/category_approvers.yaml",
    "masking/rules.yaml",
    "masking/config.yaml",
    "exceptions/policy_exceptions.yaml",
    "settings/app.yaml",
    "subscriptions/subscriptions.yaml",
)


def _redact(value: Any) -> Any:
    """Replace values under secret-looking keys, recursively."""
    if isinstance(value, dict):
        return {k: (REDACTED if _SECRET_KEY_HINT.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _strip_redacted(value: Any) -> Any:
    """Drop secret-looking keys whose values were redacted so recover never writes placeholders."""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, child in value.items():
            if _SECRET_KEY_HINT.search(str(key)) and child == REDACTED:
                continue
            out[key] = _strip_redacted(child)
        return out
    if isinstance(value, list):
        return [_strip_redacted(v) for v in value]
    return value


def _to_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


class RecoveryService:
    """Reads app state, builds recovery files, and stores the Git target settings.

    Attributes:
        config: Deployment configuration (catalog, schema, secret scope, storage backend).
    """

    def __init__(
        self,
        config: AppConfig,
        storage: StorageProvider,
        workspace: WorkspaceClientFactory,
        publisher: GitPublisher | None = None,
        *,
        tick_seconds: float = _TICK_SECONDS,
    ) -> None:
        self._config = config
        self._storage = storage
        self._workspace = workspace
        self._publisher = publisher or GitPublisher()
        self._tick_seconds = tick_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def get_settings(self) -> RecoveryGitSettings:
        """Return the saved Git target settings merged with schedule status, or defaults."""
        raw = self._storage.settings.get_all().get(SETTINGS_KEY)
        merged: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        merged.update(self._schedule_state())
        return RecoveryGitSettings.model_validate(merged)

    def save_settings(self, body: RecoveryGitSettings, actor: CurrentUser) -> RecoveryGitSettings:
        """Persist the writable Git target and schedule settings. Validation happens in the model."""
        cleaned = RecoveryGitSettings.model_validate(
            {key: getattr(body, key) for key in _WRITABLE_SETTINGS_KEYS}
        )
        stored = cleaned.model_dump(mode="json", include=set(_WRITABLE_SETTINGS_KEYS))
        self._storage.settings.put_many({SETTINGS_KEY: stored}, actor.user_name)
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type="recovery.settings_saved",
                actor=actor.user_name,
                payload={
                    "repo_url": cleaned.repo_url,
                    "branch": cleaned.branch,
                    "path_prefix": cleaned.path_prefix,
                    "schedule_enabled": cleaned.schedule_enabled,
                    "interval_hours": cleaned.interval_hours,
                },
                created_at=datetime.now(UTC),
            )
        )
        return self.get_settings()

    def build_preview(self, actor: CurrentUser) -> RecoveryPreview:
        """Build the files a recovery push would commit, without writing anything.

        Returns:
            The preview with files in a stable order so repeated runs produce identical diffs.
        """
        generated_at = datetime.now(UTC)
        git = self.get_settings()
        files, warnings = self._collect(generated_at, actor, git)
        target = f"{git.repo_url}@{git.branch}:{git.path_prefix}" if git.repo_url else ""
        if not git.repo_url:
            warnings.insert(0, "No Git repository is configured; this is a preview only.")
        return RecoveryPreview(
            generated_at=generated_at,
            generated_by=actor.user_name,
            target=target,
            git=git,
            files=files,
            warnings=warnings,
        )

    def push(self, actor: CurrentUser) -> RecoveryPushResult:
        """Commit the current export to the configured Git branch.

        Replaces the files under ``path_prefix`` with the export and pushes one commit when
        anything changed. Only the saved settings are used, so save them before pushing.

        Args:
            actor: Admin running the push; recorded as the commit author and in the audit log.

        Returns:
            The commit reference and whether a commit was pushed.

        Raises:
            InvalidRequestError: Repository, path, or token secret is not configured or readable.
            UpstreamError: A Git step failed (the message never contains the token).
        """
        git = self.get_settings()
        if not git.repo_url:
            raise InvalidRequestError("Save a Git repository URL on this page before pushing.")
        if not git.path_prefix:
            raise InvalidRequestError("Save a path in the repository before pushing.")

        generated_at = datetime.now(UTC)
        files, _warnings = self._collect(generated_at, actor, git)
        token = self._read_token(git.token_secret_key)
        message = f"Recovery export {generated_at:%Y-%m-%d %H:%M:%S} UTC by {actor.user_name}"
        try:
            with step(f"Commit {len(files)} files and push to {git.branch}"):
                outcome = self._publisher.push_files(
                    repo_url=git.repo_url,
                    branch=git.branch,
                    path_prefix=git.path_prefix,
                    files={f.path: f.content for f in files},
                    username=git.username or "governance-app",
                    token=token,
                    author_name=git.username or "governance-app",
                    author_email=getattr(actor, "email", None) or actor.user_name,
                    message=message,
                )
        except GitPublishError as exc:
            logger.warning("Recovery push to %s@%s failed: %s", git.repo_url, git.branch, exc)
            raise UpstreamError(str(exc)) from exc

        pushed_at = datetime.now(UTC)
        task_summary(
            f"Pushed {outcome.commit_sha[:8]}"
            if outcome.committed and outcome.commit_sha
            else "No changes to push"
        )
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type="recovery.export_pushed",
                actor=actor.user_name,
                payload={
                    "repo_url": git.repo_url,
                    "branch": git.branch,
                    "path_prefix": git.path_prefix,
                    "commit_sha": outcome.commit_sha,
                    "committed": outcome.committed,
                    "files": len(files),
                    "scheduled": actor.user_name == _SCHEDULER_ACTOR.user_name,
                },
                created_at=pushed_at,
            )
        )
        return RecoveryPushResult(
            pushed_at=pushed_at,
            pushed_by=actor.user_name,
            target=f"{git.repo_url}@{git.branch}:{git.path_prefix}",
            commit_sha=outcome.commit_sha,
            committed=outcome.committed,
            branch_created=outcome.branch_created,
            files=len(files),
        )

    def start_scheduler(self) -> None:
        """Start the background schedule loop (idempotent)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop, name="recovery-git-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Recovery Git schedule loop started (tick=%.0fs)", self._tick_seconds)

    def stop_scheduler(self) -> None:
        """Stop the background schedule loop if it is running."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def run_scheduled_push_if_due(self, *, now: datetime | None = None) -> RecoveryPushResult | None:
        """Push once when the schedule is due; no-op when disabled, not due, or the lease is held.

        Args:
            now: Clock override for tests; defaults to current UTC.

        Returns:
            The push result when this instance ran a push, otherwise None.
        """
        clock = now or datetime.now(UTC)
        settings = self.get_settings()
        if not self._schedule_is_due(settings, clock):
            return None

        run_id = str(uuid.uuid4())
        if not self._acquire_lock(run_id, clock):
            return None

        self._write_schedule_state(
            {
                "last_scheduled_attempt_at": clock.isoformat(),
                "last_scheduled_error": None,
            }
        )
        try:
            # Re-check after the lease so a peer that just succeeded is not duplicated.
            settings = self.get_settings()
            if not self._schedule_is_due(settings, clock):
                return None
            result = self.push(_SCHEDULER_ACTOR)
            self._write_schedule_state(
                {
                    "last_scheduled_success_at": result.pushed_at.isoformat(),
                    "last_scheduled_attempt_at": clock.isoformat(),
                    "last_scheduled_error": None,
                    "last_scheduled_commit_sha": result.commit_sha,
                    "last_scheduled_committed": result.committed,
                }
            )
            return result
        except Exception as exc:  # noqa: BLE001 - record and keep the loop alive
            logger.warning("Scheduled recovery push failed: %s", exc)
            self._write_schedule_state(
                {
                    "last_scheduled_attempt_at": clock.isoformat(),
                    "last_scheduled_error": str(exc),
                }
            )
            return None
        finally:
            self._release_lock(run_id)

    def import_from_git(self, actor: CurrentUser) -> RecoveryImportPreview:
        """Fetch the recovery snapshot from Git and return it without writing app state.

        Args:
            actor: Admin running the import.

        Returns:
            Parsed files, occupancy of the current app, and warnings.

        Raises:
            InvalidRequestError: Repository/path/token missing, or the snapshot is invalid.
            UpstreamError: A Git step failed (the message never contains the token).
        """
        git = self._require_git_target()
        pull = self._pull(git)
        files, warnings, manifest, _parsed = self._parse_snapshot(pull.files)
        occupancy = self._occupancy()
        if occupancy.app_has_state:
            warnings.insert(
                0,
                "This app already has governance state. Recover will upsert/merge by key and keep "
                f"local rows that are not in the snapshot. ({occupancy.summary})",
            )
        warnings.append(
            "Recover writes app storage only. Re-run identity UC sync, mask-rule sync, and "
            "data-access apply afterward so Unity Catalog matches."
        )
        return RecoveryImportPreview(
            imported_at=datetime.now(UTC),
            imported_by=actor.user_name,
            source=f"{git.repo_url}@{git.branch}:{git.path_prefix}",
            commit_sha=pull.commit_sha,
            files=files,
            warnings=warnings,
            occupancy=occupancy,
            manifest=manifest,
        )

    def recover(self, actor: CurrentUser, body: RecoveryApplyRequest) -> RecoveryApplyResult:
        """Pull the snapshot again and upsert it into app storage.

        Requires ``body.confirm``. Does not trust a prior browser import payload: always re-reads
        Git. Local rows not present in the snapshot are kept.

        Args:
            actor: Admin running the recover.
            body: Must set ``confirm`` to True.

        Returns:
            Counts written per section and follow-up warnings.

        Raises:
            InvalidRequestError: Missing confirm, bad Git settings/token, or invalid snapshot.
            UpstreamError: A Git step failed.
        """
        if not body.confirm:
            raise InvalidRequestError("Recover requires confirm=true after reviewing the import.")

        git = self._require_git_target()
        occupancy = self._occupancy()
        pull = self._pull(git)
        _files, parse_warnings, _manifest, parsed = self._parse_snapshot(pull.files)
        counts = self._apply_snapshot(parsed)
        applied_at = datetime.now(UTC)

        warnings = list(parse_warnings)
        warnings.append(
            "Re-run identity UC sync, mask-rule sync, and data-access apply so Unity Catalog matches."
        )
        if counts.subscriptions:
            warnings.append(
                "Recovered subscriptions keep their status and time window. Run the data-access sync "
                "job (or Sync now on each subscription) to recreate their groups, members and grants here."
            )
        salt_snapshot = parsed.get("masking/config.yaml") or {}
        salt_was_configured = bool((salt_snapshot.get("joinable_salt") or {}).get("configured"))
        if salt_was_configured and not self._storage.mask_rules.get_config(JOINABLE_SALT_KEY):
            warnings.insert(
                0,
                "The joinable salt was configured in the snapshot but is not set here. Set it "
                "manually; it is never restored from Git.",
            )

        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type="recovery.imported",
                actor=actor.user_name,
                payload={
                    "repo_url": git.repo_url,
                    "branch": git.branch,
                    "path_prefix": git.path_prefix,
                    "commit_sha": pull.commit_sha,
                    "counts": counts.model_dump(mode="json"),
                    "app_had_state": occupancy.app_has_state,
                },
                created_at=applied_at,
            )
        )
        return RecoveryApplyResult(
            applied_at=applied_at,
            applied_by=actor.user_name,
            source=f"{git.repo_url}@{git.branch}:{git.path_prefix}",
            commit_sha=pull.commit_sha,
            counts=counts,
            warnings=warnings,
            app_had_state=occupancy.app_has_state,
        )

    def _require_git_target(self) -> RecoveryGitSettings:
        git = self.get_settings()
        if not git.repo_url:
            raise InvalidRequestError("Save a Git repository URL on this page before importing.")
        if not git.path_prefix:
            raise InvalidRequestError("Save a path in the repository before importing.")
        return git

    @step("Pull snapshot from Git")
    def _pull(self, git: RecoveryGitSettings) -> PullOutcome:
        token = self._read_token(git.token_secret_key)
        try:
            return self._publisher.pull_files(
                repo_url=git.repo_url,
                branch=git.branch,
                path_prefix=git.path_prefix,
                username=git.username or "governance-app",
                token=token,
            )
        except GitPublishError as exc:
            logger.warning("Recovery pull from %s@%s failed: %s", git.repo_url, git.branch, exc)
            raise UpstreamError(str(exc)) from exc

    @step("Check current app state")
    def _occupancy(self) -> RecoveryOccupancy:
        definitions = len(self._storage.identity_attributes.list_definitions())
        assignments = len(
            self._storage.identity_attributes.list_assignments(
                AttributeAssignmentFilter(include_inactive=False)
            )
        )
        approvers = len(self._storage.category_approvers.list_approvers())
        mask_rules = len(self._storage.mask_rules.list_rules())
        policy_exceptions = len(
            [e for e in self._storage.policy_exceptions.list_exceptions() if not e.system]
        )
        subscriptions = len(self._storage.subscriptions.list_subscriptions())
        data_access = self._storage.settings.get_all().get(DATA_ACCESS_SETTINGS_KEY)
        data_access_configured = isinstance(data_access, dict) and bool(data_access)
        parts: list[str] = []
        if definitions:
            parts.append(f"{definitions} definition{'s' if definitions != 1 else ''}")
        if assignments:
            parts.append(f"{assignments} assignment{'s' if assignments != 1 else ''}")
        if approvers:
            parts.append(f"{approvers} approver{'s' if approvers != 1 else ''}")
        if mask_rules:
            parts.append(f"{mask_rules} mask rule{'s' if mask_rules != 1 else ''}")
        if policy_exceptions:
            parts.append(f"{policy_exceptions} policy exception{'s' if policy_exceptions != 1 else ''}")
        if subscriptions:
            parts.append(f"{subscriptions} subscription{'s' if subscriptions != 1 else ''}")
        if data_access_configured:
            parts.append("data-access settings present")
        app_has_state = bool(parts)
        return RecoveryOccupancy(
            app_has_state=app_has_state,
            definitions=definitions,
            assignments=assignments,
            approvers=approvers,
            mask_rules=mask_rules,
            policy_exceptions=policy_exceptions,
            subscriptions=subscriptions,
            data_access_configured=data_access_configured,
            summary=", ".join(parts) if parts else "empty",
        )

    @step("Validate snapshot files")
    def _parse_snapshot(
        self, raw_files: dict[str, str]
    ) -> tuple[list[RecoveryFile], list[str], RecoveryManifestSummary, dict[str, Any]]:
        """Validate YAML envelopes and return display files plus parsed payloads keyed by path."""
        if "manifest.yaml" not in raw_files:
            raise InvalidRequestError("The snapshot is missing manifest.yaml.")

        parsed: dict[str, Any] = {}
        files: list[RecoveryFile] = []
        warnings: list[str] = []
        for path in EXPECTED_FILES:
            content = raw_files.get(path)
            if content is None:
                warnings.append(f"Missing file {path}; that section will be skipped on recover.")
                continue
            try:
                payload = yaml.safe_load(content) or {}
            except yaml.YAMLError as exc:
                raise InvalidRequestError(f"Could not parse {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise InvalidRequestError(f"{path} must be a YAML mapping.")
            version = payload.get("version")
            if version is not None and version != FORMAT_VERSION:
                raise InvalidRequestError(
                    f"{path} has unsupported version {version!r}; expected {FORMAT_VERSION}."
                )
            parsed[path] = payload
            records = int(payload.get("count") or 0) if "count" in payload else 0
            files.append(RecoveryFile(path=path, records=records, content=content))

        for path in sorted(raw_files):
            if path in EXPECTED_FILES:
                continue
            warnings.append(f"Ignoring unexpected file {path}.")
            files.append(RecoveryFile(path=path, records=0, content=raw_files[path]))

        manifest_raw = parsed.get("manifest.yaml") or {}
        manifest = RecoveryManifestSummary(
            version=manifest_raw.get("version"),
            generated_at=str(manifest_raw["generated_at"]) if manifest_raw.get("generated_at") else None,
            generated_by=str(manifest_raw["generated_by"]) if manifest_raw.get("generated_by") else None,
        )
        salt = (parsed.get("masking/config.yaml") or {}).get("joinable_salt") or {}
        if salt.get("configured"):
            warnings.append(
                "The joinable salt value is not in the snapshot. Keep a copy outside Git; recover "
                "cannot restore it."
            )
        return files, warnings, manifest, parsed

    @step("Write snapshot to app storage")
    def _apply_snapshot(self, parsed: dict[str, Any]) -> RecoveryApplyCounts:
        counts = RecoveryApplyCounts()
        try:
            definitions = self._items(parsed, "identity/attribute_definitions.yaml", "attribute_definitions")
            for item in definitions:
                self._storage.identity_attributes.upsert_definition(AttributeDefinition.model_validate(item))
                counts.definitions += 1

            assignments = self._items(parsed, "identity/assignments.yaml", "assignments")
            for item in assignments:
                self._storage.identity_attributes.add_assignment(
                    IdentityAttributeAssignment.model_validate(item)
                )
                counts.assignments += 1

            approvers = self._items(parsed, "approvals/category_approvers.yaml", "category_approvers")
            for item in approvers:
                self._storage.category_approvers.upsert_approver(CategoryApprover.model_validate(item))
                counts.approvers += 1

            rules = self._items(parsed, "masking/rules.yaml", "mask_rules")
            for item in rules:
                self._storage.mask_rules.upsert_rule(MaskRuleDefinition.model_validate(item))
                counts.mask_rules += 1

            exceptions = self._items(parsed, "exceptions/policy_exceptions.yaml", "policy_exceptions")
            for item in exceptions:
                exception = PolicyException.model_validate(item)
                if exception.system:
                    continue
                self._storage.policy_exceptions.upsert_exception(exception)
                counts.policy_exceptions += 1

            for item in self._items(parsed, "subscriptions/subscriptions.yaml", "subscriptions"):
                self._restore_subscription(Subscription.model_validate(item))
                counts.subscriptions += 1

            app_file = parsed.get("settings/app.yaml") or {}
            app_settings = app_file.get("app_settings")
            if isinstance(app_settings, dict) and app_settings:
                self._storage.settings.put_many({APP_SETTINGS_KEY: _strip_redacted(app_settings)}, "recovery")
                counts.app_settings = 1

            data_access_file = parsed.get("settings/data_access.yaml") or {}
            data_access = data_access_file.get("data_access")
            if isinstance(data_access, dict) and data_access:
                cleaned = _strip_redacted(data_access)
                if cleaned:
                    self._storage.settings.put_many({DATA_ACCESS_SETTINGS_KEY: cleaned}, "recovery")
                    counts.data_access = 1
        except ValidationError as exc:
            raise InvalidRequestError(f"Snapshot failed validation: {exc.errors()[0]['msg']}") from exc
        return counts

    def _restore_subscription(self, snapshot: Subscription) -> None:
        """Upsert one subscription, keeping the local ID when one with the same name exists.

        ``last_sync`` describes grants in the source workspace, so it is never restored; the
        next subscription sync rebuilds the group, members and grants here.
        """
        repo = self._storage.subscriptions
        local = repo.get_by_name(snapshot.name)
        subscription_id = local.subscription_id if local is not None else snapshot.subscription_id
        repo.save_subscription(
            snapshot.model_copy(
                update={
                    "subscription_id": subscription_id,
                    "last_sync": local.last_sync if local is not None else None,
                }
            )
        )

    @staticmethod
    def _items(parsed: dict[str, Any], path: str, key: str) -> list[dict[str, Any]]:
        payload = parsed.get(path)
        if not payload:
            return []
        items = payload.get(key) or []
        if not isinstance(items, list):
            raise InvalidRequestError(f"{path}: '{key}' must be a list.")
        out: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise InvalidRequestError(f"{path}: item {index} must be a mapping.")
            out.append(item)
        return out

    def _scheduler_loop(self) -> None:
        """Wake periodically and push when the configured interval has elapsed."""
        while not self._stop.wait(self._tick_seconds):
            try:
                self.run_scheduled_push_if_due()
            except Exception:  # noqa: BLE001 - never kill the daemon on a tick failure
                logger.exception("Recovery Git schedule tick failed")

    @staticmethod
    def _schedule_is_due(settings: RecoveryGitSettings, now: datetime) -> bool:
        """Whether a scheduled push should run for these settings at ``now``."""
        if not settings.schedule_enabled:
            return False
        if not settings.repo_url or not settings.path_prefix:
            return False
        if settings.last_scheduled_success_at is None:
            return True
        return now - settings.last_scheduled_success_at >= timedelta(hours=settings.interval_hours)

    def _schedule_state(self) -> dict[str, Any]:
        """Last-run fields stored under ``SCHEDULE_STATE_KEY``."""
        raw = self._storage.settings.get_all().get(SCHEDULE_STATE_KEY)
        if not isinstance(raw, dict):
            return {}
        keys = (
            "last_scheduled_success_at",
            "last_scheduled_attempt_at",
            "last_scheduled_error",
            "last_scheduled_commit_sha",
            "last_scheduled_committed",
        )
        return {key: raw[key] for key in keys if key in raw}

    def _write_schedule_state(self, patch: dict[str, Any]) -> None:
        """Merge ``patch`` into the persisted schedule status blob."""
        current = self._storage.settings.get_all().get(SCHEDULE_STATE_KEY)
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(patch)
        self._storage.settings.put_many({SCHEDULE_STATE_KEY: merged}, "system")

    def _acquire_lock(self, run_id: str, started: datetime) -> bool:
        """Take the schedule lease unless a non-stale lock is held.

        Args:
            run_id: Id written into the lock record.
            started: Timestamp written into the lock record.

        Returns:
            True when this caller now holds the lease.
        """
        with self._lock:
            current = self._storage.settings.get_all().get(LOCK_KEY) or {}
            held_at = current.get("started_at") if isinstance(current, dict) else None
            if held_at:
                try:
                    when = datetime.fromisoformat(str(held_at).replace("Z", "+00:00"))
                except ValueError:
                    when = None
                if when is not None and datetime.now(UTC) - when < _LOCK_STALE:
                    return False
            self._storage.settings.put_many(
                {LOCK_KEY: {"run_id": run_id, "started_at": started.isoformat()}},
                "system",
            )
            return True

    def _release_lock(self, run_id: str) -> None:
        """Clear the schedule lease if it still belongs to ``run_id``."""
        with self._lock:
            current = self._storage.settings.get_all().get(LOCK_KEY) or {}
            if isinstance(current, dict) and current.get("run_id") == run_id:
                self._storage.settings.put_many({LOCK_KEY: {}}, "system")

    @step("Read Git token secret")
    def _read_token(self, secret_key: str) -> str:
        """Read the Git token from the admin secret scope. The value is returned to the caller only.

        Raises:
            InvalidRequestError: The key is missing, empty, or not readable by the app.
        """
        scope = admin_secret_scope()
        try:
            resp = self._workspace.app().secrets.get_secret(scope=scope, key=secret_key)
        except Exception as exc:  # noqa: BLE001 - SDK raises several not-found / permission types
            logger.info("Git token %s/%s not readable: %s", scope, secret_key, type(exc).__name__)
            raise InvalidRequestError(
                f"Could not read the Git token from secret scope '{scope}', key '{secret_key}'. "
                "Add it with the Databricks CLI and make sure the app service principal can read the scope."
            ) from None
        token = decode_secret_value(getattr(resp, "value", None))
        if not token:
            raise InvalidRequestError(f"The Git token under key '{secret_key}' in scope '{scope}' is empty.")
        return token

    @step("Collect governance state")
    def _collect(
        self, generated_at: datetime, actor: CurrentUser, git: RecoveryGitSettings
    ) -> tuple[list[RecoveryFile], list[str]]:
        definitions = sorted(self._storage.identity_attributes.list_definitions(), key=lambda d: d.key)
        assignments = sorted(
            self._storage.identity_attributes.list_assignments(
                AttributeAssignmentFilter(include_inactive=False)
            ),
            key=lambda a: (a.principal_type, a.principal_name, a.attribute_key, a.attribute_value),
        )
        approvers = sorted(
            self._storage.category_approvers.list_approvers(),
            key=lambda a: (a.attribute_key, a.category_path, a.principal_name),
        )
        rules = sorted(self._storage.mask_rules.list_rules(), key=lambda r: r.rule_key)
        exceptions = sorted(
            (e for e in self._storage.policy_exceptions.list_exceptions() if not e.system),
            key=lambda e: e.principal_name,
        )
        salt_configured = bool(self._storage.mask_rules.get_config(JOINABLE_SALT_KEY))
        all_settings = self._storage.settings.get_all()
        data_access = all_settings.get(DATA_ACCESS_SETTINGS_KEY) or {}
        app_settings = all_settings.get(APP_SETTINGS_KEY) or {}
        subscriptions = sorted(self._storage.subscriptions.list_subscriptions(), key=lambda s: s.name)

        def envelope(name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
            return {"version": FORMAT_VERSION, "count": len(items), name: items}

        manifest = {
            "version": FORMAT_VERSION,
            "generated_at": generated_at.isoformat(),
            "generated_by": actor.user_name,
            "storage_backend": self._config.storage_backend.value,
            "app_catalog": self._config.app_catalog,
            "app_schema": self._config.app_schema,
            "git": {
                "repo_url": git.repo_url,
                "branch": git.branch,
                "path_prefix": git.path_prefix,
            },
            "files": [
                "manifest.yaml",
                "settings/data_access.yaml",
                "identity/attribute_definitions.yaml",
                "identity/assignments.yaml",
                "approvals/category_approvers.yaml",
                "masking/rules.yaml",
                "masking/config.yaml",
                "exceptions/policy_exceptions.yaml",
                "settings/app.yaml",
                "subscriptions/subscriptions.yaml",
            ],
        }
        raw_files: list[tuple[str, int, dict[str, Any]]] = [
            ("manifest.yaml", 0, manifest),
            (
                "settings/data_access.yaml",
                0,
                {"version": FORMAT_VERSION, "data_access": _redact(data_access)},
            ),
            (
                "identity/attribute_definitions.yaml",
                len(definitions),
                envelope("attribute_definitions", [_dump(d) for d in definitions]),
            ),
            (
                "identity/assignments.yaml",
                len(assignments),
                envelope("assignments", [_dump(a) for a in assignments]),
            ),
            (
                "approvals/category_approvers.yaml",
                len(approvers),
                envelope("category_approvers", [_dump(a) for a in approvers]),
            ),
            ("masking/rules.yaml", len(rules), envelope("mask_rules", [_dump(r) for r in rules])),
            (
                "masking/config.yaml",
                0,
                {
                    "version": FORMAT_VERSION,
                    "joinable_salt": {"configured": salt_configured, "value": REDACTED},
                },
            ),
            (
                "exceptions/policy_exceptions.yaml",
                len(exceptions),
                envelope("policy_exceptions", [_dump(e) for e in exceptions]),
            ),
            ("settings/app.yaml", 0, {"version": FORMAT_VERSION, "app_settings": _redact(app_settings)}),
            (
                "subscriptions/subscriptions.yaml",
                len(subscriptions),
                envelope(
                    "subscriptions",
                    [s.model_dump(mode="json", exclude={"last_sync", "phase"}) for s in subscriptions],
                ),
            ),
        ]
        files = [
            RecoveryFile(path=path, records=records, content=_to_yaml(payload))
            for path, records, payload in raw_files
        ]

        warnings = [
            "Access requests, approval history and audit events are not exported in this version.",
            "Only active identity assignments are exported; revoked and expired rows are omitted.",
            "Policy exceptions for the app and admin service principals are regenerated by sync "
            "and are omitted.",
        ]
        if salt_configured:
            warnings.append(
                "The joinable salt is not exported (value redacted). Keep a copy of it outside Git: "
                "joinable hashes cannot be reproduced without it."
            )
        else:
            warnings.append("No joinable salt is configured; joinable masks will fail until one is set.")
        return files, warnings

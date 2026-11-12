"""Recovery export: snapshot app-owned state as YAML for Git-backed disaster recovery.

``build_preview`` returns the files an export would commit so an admin can inspect them without
writing anything. ``push`` commits the same files to the configured Git branch. The joinable
salt is reported as configured but never exported, and secret-looking keys are redacted. The Git
token is read from the admin secret scope at push time only; it is never stored, logged, or
returned.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import yaml

from backend.app.clients.git_publisher import GitPublisher, GitPublishError
from backend.app.clients.secrets import admin_secret_scope, decode_secret_value
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import AttributeAssignmentFilter
from backend.app.models.recovery import (
    RecoveryFile,
    RecoveryGitSettings,
    RecoveryPreview,
    RecoveryPushResult,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.mask_rules import JOINABLE_SALT_KEY

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
SETTINGS_KEY = "recovery_git"
DATA_ACCESS_SETTINGS_KEY = "data_access"
REDACTED = "<redacted>"
_SECRET_KEY_HINT = re.compile(r"secret|token|password|credential", re.IGNORECASE)


def _redact(value: Any) -> Any:
    """Replace values under secret-looking keys, recursively."""
    if isinstance(value, dict):
        return {k: (REDACTED if _SECRET_KEY_HINT.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
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
    ) -> None:
        self._config = config
        self._storage = storage
        self._workspace = workspace
        self._publisher = publisher or GitPublisher()

    def get_settings(self) -> RecoveryGitSettings:
        """Return the saved Git target settings, or defaults when none are saved."""
        raw = self._storage.settings.get_all().get(SETTINGS_KEY)
        if not isinstance(raw, dict):
            return RecoveryGitSettings()
        return RecoveryGitSettings.model_validate(raw)

    def save_settings(self, body: RecoveryGitSettings, actor: CurrentUser) -> RecoveryGitSettings:
        """Persist the Git target settings. Validation happens in the model."""
        self._storage.settings.put_many({SETTINGS_KEY: body.model_dump(mode="json")}, actor.user_name)
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type="recovery.settings_saved",
                actor=actor.user_name,
                payload={"repo_url": body.repo_url, "branch": body.branch, "path_prefix": body.path_prefix},
                created_at=datetime.now(UTC),
            )
        )
        return body

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
            outcome = self._publisher.push_files(
                repo_url=git.repo_url,
                branch=git.branch,
                path_prefix=git.path_prefix,
                files={f.path: f.content for f in files},
                username=git.username or "governance-app",
                token=token,
                author_name=git.username or "governance-app",
                author_email=actor.user_name,
                message=message,
            )
        except GitPublishError as exc:
            logger.warning("Recovery push to %s@%s failed: %s", git.repo_url, git.branch, exc)
            raise UpstreamError(str(exc)) from exc

        pushed_at = datetime.now(UTC)
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
        data_access = self._storage.settings.get_all().get(DATA_ACCESS_SETTINGS_KEY) or {}

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

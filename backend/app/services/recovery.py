"""Recovery export: snapshot app-owned state as YAML for Git-backed disaster recovery.

Phase 1 is preview-only. ``build_preview`` returns the files a future push would commit so an
admin can inspect them. Nothing is written to Git, and no secret values are read: the joinable
salt is reported as configured but never exported, and secret-looking keys are redacted.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import yaml

from backend.app.core.config import AppConfig
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import AttributeAssignmentFilter
from backend.app.models.recovery import RecoveryFile, RecoveryGitSettings, RecoveryPreview
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

    def __init__(self, config: AppConfig, storage: StorageProvider) -> None:
        self._config = config
        self._storage = storage

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

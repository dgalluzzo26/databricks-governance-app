"""Models for the recovery export: Git target settings and the dry-run preview.

The Git token is never stored here: ``token_secret_key`` names a key in the admin secret scope
that holds it, and is only read at push or import time.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel

GIT_BRANCH_PATTERN = r"^[A-Za-z0-9._/\-]{1,100}$"
GIT_PATH_PATTERN = r"^[A-Za-z0-9._/\-]{0,200}$"
SECRET_KEY_PATTERN = r"^[A-Za-z0-9._\-]{0,128}$"


class RecoveryGitSettings(ApiModel):
    """Where recovery exports would be committed.

    Attributes:
        repo_url: HTTPS clone URL of the Git repository; empty when not configured.
        branch: Branch to commit to.
        path_prefix: Directory inside the repository that holds the export files.
        username: Git user name used for authentication (not a secret).
        token_secret_key: Key in the admin secret scope holding the Git token.
    """

    repo_url: str = Field(default="", max_length=512)
    branch: str = Field(default="main", pattern=GIT_BRANCH_PATTERN)
    path_prefix: str = Field(default="governance-export", pattern=GIT_PATH_PATTERN)
    username: str = Field(default="", max_length=256)
    token_secret_key: str = Field(default="git-token", pattern=SECRET_KEY_PATTERN)

    @field_validator("repo_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        value = value.strip()
        if value and (not value.startswith("https://") or any(c.isspace() for c in value)):
            raise ValueError("repo_url must be an https:// URL without whitespace")
        return value

    @field_validator("branch", "path_prefix")
    @classmethod
    def _no_traversal(cls, value: str) -> str:
        if ".." in value or value.startswith("/"):
            raise ValueError("path must be relative and must not contain '..'")
        return value


class RecoveryFile(ApiModel):
    """One file the export would write.

    Attributes:
        path: Path relative to ``path_prefix``.
        records: Number of records in the file (0 for manifests and singletons).
        content: Full YAML text of the file.
    """

    path: str
    records: int
    content: str


class RecoveryPreview(ApiModel):
    """Result of a dry-run export.

    Attributes:
        generated_at: When the snapshot was taken.
        generated_by: Admin who ran the preview.
        target: Human-readable destination, e.g. ``repo@branch:prefix``; empty when unset.
        git: Git settings in effect for the preview.
        files: Files that a push would commit.
        warnings: Things the operator should know before pushing.
    """

    generated_at: datetime
    generated_by: str
    target: str
    git: RecoveryGitSettings
    files: list[RecoveryFile]
    warnings: list[str]


class RecoveryPushResult(ApiModel):
    """Result of pushing the export to Git.

    Attributes:
        pushed_at: When the push finished.
        pushed_by: Admin who ran the push.
        target: Human-readable destination, e.g. ``repo@branch:prefix``.
        commit_sha: Commit at the branch tip after the push.
        committed: False when the branch already matched the export and nothing was pushed.
        branch_created: True when the push created the branch.
        files: Number of files written under the path prefix.
    """

    pushed_at: datetime
    pushed_by: str
    target: str
    commit_sha: str
    committed: bool
    branch_created: bool
    files: int


class RecoveryOccupancy(ApiModel):
    """How much app-owned state already exists before a recover.

    Attributes:
        app_has_state: True when any counted section is non-empty.
        definitions: Number of identity attribute definitions.
        assignments: Number of active identity assignments.
        approvers: Number of category / dimension approvers.
        mask_rules: Number of mask rules.
        policy_exceptions: Number of non-system policy exceptions.
        data_access_configured: True when a data-access settings blob is stored.
        summary: Short human-readable summary for the UI warning.
    """

    app_has_state: bool
    definitions: int = 0
    assignments: int = 0
    approvers: int = 0
    mask_rules: int = 0
    policy_exceptions: int = 0
    data_access_configured: bool = False
    summary: str = ""


class RecoveryManifestSummary(ApiModel):
    """Fields from the imported ``manifest.yaml`` that are useful to show.

    Attributes:
        version: Export format version.
        generated_at: When the snapshot was produced, if present.
        generated_by: Who produced the snapshot, if present.
    """

    version: int | None = None
    generated_at: str | None = None
    generated_by: str | None = None


class RecoveryImportPreview(ApiModel):
    """Result of importing a recovery snapshot from Git without applying it.

    Attributes:
        imported_at: When the import finished.
        imported_by: Admin who ran the import.
        source: Human-readable source, e.g. ``repo@branch:prefix``.
        commit_sha: Commit that was read.
        files: Parsed recovery files with YAML content.
        warnings: Things the operator should know before recovering.
        occupancy: Current app state counts used for the non-empty warning.
        manifest: Summary from ``manifest.yaml`` when present.
    """

    imported_at: datetime
    imported_by: str
    source: str
    commit_sha: str
    files: list[RecoveryFile]
    warnings: list[str]
    occupancy: RecoveryOccupancy
    manifest: RecoveryManifestSummary = Field(default_factory=RecoveryManifestSummary)


class RecoveryApplyRequest(ApiModel):
    """Body for applying an imported recovery snapshot.

    Attributes:
        confirm: Must be True; recovers are rejected without an explicit confirmation.
    """

    confirm: bool = False


class RecoveryApplyCounts(ApiModel):
    """How many rows were written per section during recover."""

    definitions: int = 0
    assignments: int = 0
    approvers: int = 0
    mask_rules: int = 0
    policy_exceptions: int = 0
    data_access: int = 0


class RecoveryApplyResult(ApiModel):
    """Result of applying a recovery snapshot into app storage.

    Attributes:
        applied_at: When the recover finished.
        applied_by: Admin who ran the recover.
        source: Human-readable source, e.g. ``repo@branch:prefix``.
        commit_sha: Commit that was applied.
        counts: Rows written per section.
        warnings: Salt / follow-up reminders.
        app_had_state: Whether the app already had state before the recover.
    """

    applied_at: datetime
    applied_by: str
    source: str
    commit_sha: str
    counts: RecoveryApplyCounts
    warnings: list[str]
    app_had_state: bool

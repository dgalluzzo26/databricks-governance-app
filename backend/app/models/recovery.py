"""Models for the recovery export: Git target settings and the dry-run preview.

The Git token is never stored here: ``token_secret_key`` names a key in the admin secret scope
that holds it, and is only read at push time.
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

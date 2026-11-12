"""Tests for the git CLI publisher, run against a local bare repository (no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.clients.git_publisher import GitPublisher, GitPublishError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI not installed")

PREFIX = "governance-export"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A bare repository whose main branch has a file outside the export prefix and a stale export file."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("hello\n", encoding="utf-8")
    (seed / PREFIX).mkdir()
    (seed / PREFIX / "stale.yaml").write_text("old\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", str(bare), "main")
    return bare


def _publish(
    repo: Path,
    files: dict[str, str],
    *,
    branch: str = "main",
    prefix: str = PREFIX,
    message: str = "Recovery export",
) -> object:
    return GitPublisher().push_files(
        repo_url=str(repo),
        branch=branch,
        path_prefix=prefix,
        files=files,
        username="svc-git",
        token="",
        author_name="Ana Lopez",
        author_email="ana@example.com",
        message=message,
    )


def test_push_replaces_export_directory_and_keeps_other_files(remote: Path) -> None:
    outcome = _publish(remote, {"manifest.yaml": "version: 1\n", "identity/a.yaml": "a: 1\n"})

    assert outcome.committed is True
    assert outcome.branch_created is False
    assert _git(remote, "show", f"main:{PREFIX}/manifest.yaml") == "version: 1"
    assert _git(remote, "show", f"main:{PREFIX}/identity/a.yaml") == "a: 1"
    assert _git(remote, "show", "main:README.md") == "hello"
    listed = _git(remote, "ls-tree", "-r", "--name-only", "main")
    assert f"{PREFIX}/stale.yaml" not in listed.splitlines()
    assert _git(remote, "log", "-1", "--format=%an <%ae>|%s", "main") == (
        "Ana Lopez <ana@example.com>|Recovery export"
    )


def test_unchanged_export_pushes_nothing(remote: Path) -> None:
    files = {"manifest.yaml": "version: 1\n"}
    first = _publish(remote, files)
    commits_before = _git(remote, "rev-list", "--count", "main")

    second = _publish(remote, files)

    assert second.committed is False
    assert second.commit_sha == first.commit_sha
    assert _git(remote, "rev-list", "--count", "main") == commits_before


def test_changed_export_adds_one_commit(remote: Path) -> None:
    _publish(remote, {"manifest.yaml": "version: 1\n"})
    commits_before = int(_git(remote, "rev-list", "--count", "main"))

    outcome = _publish(remote, {"manifest.yaml": "version: 2\n"}, message="second export")

    assert outcome.committed is True
    assert int(_git(remote, "rev-list", "--count", "main")) == commits_before + 1
    assert _git(remote, "show", f"main:{PREFIX}/manifest.yaml") == "version: 2"
    assert _git(remote, "log", "-1", "--format=%s", "main") == "second export"


def test_missing_branch_is_created_from_export_only(remote: Path) -> None:
    outcome = _publish(remote, {"manifest.yaml": "version: 1\n"}, branch="recovery")

    assert outcome.committed is True
    assert outcome.branch_created is True
    assert _git(remote, "ls-tree", "--name-only", "recovery") == PREFIX
    assert _git(remote, "show", f"recovery:{PREFIX}/manifest.yaml") == "version: 1"


@pytest.mark.parametrize(
    ("prefix", "files"),
    [
        ("", {"manifest.yaml": "x"}),
        ("governance-export", {"../escape.yaml": "x"}),
        ("governance-export", {"/abs.yaml": "x"}),
    ],
)
def test_unsafe_prefix_or_path_is_rejected_before_any_write(
    remote: Path, prefix: str, files: dict[str, str]
) -> None:
    with pytest.raises(GitPublishError):
        _publish(remote, files, prefix=prefix)
    assert _git(remote, "rev-list", "--count", "main") == "1"


def test_failure_message_does_not_echo_the_token(tmp_path: Path) -> None:
    with pytest.raises(GitPublishError) as excinfo:
        GitPublisher().push_files(
            repo_url=str(tmp_path / "missing.git"),
            branch="main",
            path_prefix=PREFIX,
            files={"manifest.yaml": "x"},
            username="svc-git",
            token="s3cret-token-value",
            author_name="Ana",
            author_email="ana@example.com",
            message="m",
        )
    assert "s3cret-token-value" not in str(excinfo.value)

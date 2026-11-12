"""Commit a set of files to one directory of a Git branch, using the ``git`` command-line client.

The publisher works in a throwaway directory: it fetches only the branch tip (depth 1), replaces
the files under the export directory, commits when something changed, and pushes. Credentials
never appear in the remote URL or in process arguments: HTTPS auth is sent as an
``http.extraHeader`` supplied through ``GIT_CONFIG_*`` environment variables for each command.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 180
REDACTED = "<redacted>"
_SUBCOMMANDS = frozenset(
    {"init", "remote", "ls-remote", "fetch", "checkout", "rm", "add", "diff", "rev-parse", "commit", "push"}
)


class GitPublishError(Exception):
    """A Git step failed. The message never contains the token."""


@dataclass(frozen=True)
class PushOutcome:
    """Result of a publish.

    Attributes:
        commit_sha: Commit at the branch tip after the publish.
        committed: False when the files already matched the branch, so nothing was pushed.
        branch_created: True when the branch did not exist and was created by the push.
    """

    commit_sha: str
    committed: bool
    branch_created: bool


class GitPublisher:
    """Writes files under ``path_prefix`` on ``branch`` and pushes the commit.

    Only files under ``path_prefix`` are replaced; anything else on the branch is left alone.
    Files under the prefix that are no longer in ``files`` are removed, so the directory mirrors
    the export exactly.
    """

    def push_files(
        self,
        *,
        repo_url: str,
        branch: str,
        path_prefix: str,
        files: Mapping[str, str],
        username: str,
        token: str,
        author_name: str,
        author_email: str,
        message: str,
    ) -> PushOutcome:
        """Commit ``files`` under ``path_prefix`` and push to ``branch``.

        Args:
            repo_url: Repository clone URL (https in production).
            branch: Branch to commit to; created when it does not exist.
            path_prefix: Relative directory that owns the exported files. Must not be empty.
            files: Relative path (inside the prefix) to UTF-8 text content.
            username: Username for HTTPS basic auth.
            token: Personal access token or equivalent, sent only as an HTTP header.
            author_name: Commit author name.
            author_email: Commit author email.
            message: Commit message.

        Returns:
            The commit at the branch tip and whether a new commit was pushed.

        Raises:
            GitPublishError: git is missing, a step failed, or the path is unsafe.
        """
        git_bin = shutil.which("git")
        if git_bin is None:
            raise GitPublishError("The git command is not available in this app runtime; cannot push.")
        prefix = _safe_prefix(path_prefix)
        relative_files = {_safe_relative(path): content for path, content in files.items()}

        secrets = _secret_values(username, token)
        env = _git_environment(username, token, repo_url)
        head_ref = f"refs/heads/{branch}"

        with tempfile.TemporaryDirectory(prefix="recovery-git-") as tmp:
            work = Path(tmp)
            runner = _Runner(git_bin, work, env, secrets)
            runner("init", "-q")
            runner("remote", "add", "origin", repo_url)

            branch_created = not runner("ls-remote", "--heads", "origin", head_ref)
            if branch_created:
                runner("checkout", "-q", "--orphan", branch)
            else:
                runner("fetch", "-q", "--depth", "1", "origin", head_ref)
                runner("checkout", "-q", "-B", branch, "FETCH_HEAD")

            runner("rm", "-r", "-q", "--ignore-unmatch", "--", prefix)
            for relative, content in relative_files.items():
                target = work / prefix / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            runner("add", "--all", "--", prefix)

            staged = runner("diff", "--cached", "--name-only")
            if not staged:
                head = runner("rev-parse", "HEAD")
                logger.info("Recovery push: %s/%s already matches the export", branch, prefix)
                return PushOutcome(commit_sha=head, committed=False, branch_created=False)

            runner(
                "-c",
                f"user.name={_one_line(author_name)}",
                "-c",
                f"user.email={_one_line(author_email)}",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                _one_line(message),
            )
            runner("push", "-q", "origin", f"HEAD:{head_ref}")
            commit = runner("rev-parse", "HEAD")
        logger.info("Recovery push: committed %s to %s under %s", commit[:12], branch, prefix)
        return PushOutcome(commit_sha=commit, committed=True, branch_created=branch_created)


class _Runner:
    """Runs ``git`` in one work tree and returns stdout, scrubbing secrets from errors."""

    def __init__(self, git_bin: str, cwd: Path, env: dict[str, str], secrets: tuple[str, ...]) -> None:
        self._git_bin = git_bin
        self._cwd = cwd
        self._env = env
        self._secrets = secrets

    def __call__(self, *args: str) -> str:
        """Run ``git <args>`` and return stripped stdout; raise on a non-zero exit."""
        try:
            proc = subprocess.run(  # noqa: S603 - fixed git binary, arguments built here
                [self._git_bin, *args],
                cwd=self._cwd,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitPublishError(f"git {args[0] if args else ''} timed out") from exc
        if proc.returncode != 0:
            detail = _scrub(proc.stderr or proc.stdout or "", self._secrets).strip()
            step = next((a for a in args if a in _SUBCOMMANDS), "command")
            raise GitPublishError(f"git {step} failed (exit {proc.returncode}): {detail[:500]}")
        return proc.stdout.strip()


def _git_environment(username: str, token: str, repo_url: str) -> dict[str, str]:
    """Build the environment for git: no prompts, no inherited GIT_* state, header auth over HTTPS."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    pairs: list[tuple[str, str]] = [("credential.helper", "")]
    if token and repo_url.startswith("https://"):
        encoded = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        pairs.append(("http.extraHeader", f"Authorization: Basic {encoded}"))
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for index, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _secret_values(username: str, token: str) -> tuple[str, ...]:
    """Strings that must never appear in error output: the token and its encoded header."""
    values = [token]
    if token:
        values.append(base64.b64encode(f"{username}:{token}".encode()).decode("ascii"))
    return tuple(v for v in values if v)


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _safe_prefix(path_prefix: str) -> str:
    prefix = path_prefix.strip().strip("/")
    if not prefix:
        raise GitPublishError("A path in the repository is required; the export never writes to its root.")
    return _safe_relative(prefix)


def _safe_relative(path: str) -> str:
    parts = Path(path).parts
    if not path or Path(path).is_absolute() or any(part in ("..", "") for part in parts):
        raise GitPublishError(f"Unsafe path in export: {path!r}")
    return path

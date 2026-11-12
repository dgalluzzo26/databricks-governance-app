"""Tests for the recovery export preview, Git target settings, and push."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.clients.git_publisher import GitPublishError, PushOutcome
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.access_request import CategoryApprover
from backend.app.models.identity_attribute import AttributeDefinition, IdentityAttributeAssignment
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.models.policy_exception import PolicyException
from backend.app.models.principal import PrincipalKind
from backend.app.models.recovery import RecoveryGitSettings
from backend.app.services.mask_rules import JOINABLE_SALT_KEY
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin

NOW = datetime.now(UTC)


def _seed(container: ServiceContainer) -> None:
    storage = container.storage
    storage.identity_attributes.upsert_definition(AttributeDefinition(key="sensitivity"))
    storage.identity_attributes.upsert_definition(AttributeDefinition(key="zone"))
    storage.identity_attributes.add_assignment(
        IdentityAttributeAssignment(
            assignment_id="a-active",
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="finance.budget",
            created_at=NOW,
        )
    )
    storage.identity_attributes.add_assignment(
        IdentityAttributeAssignment(
            assignment_id="a-expired",
            principal_name="old@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="sensitivity",
            attribute_value="hr.payroll",
            created_at=NOW,
            expires_at=NOW - timedelta(days=1),
        )
    )
    storage.category_approvers.upsert_approver(
        CategoryApprover(
            approver_id="ap1",
            category_path="finance.budget",
            principal_name="steward@example.com",
            principal_type=PrincipalKind.USER,
            created_at=NOW,
        )
    )
    storage.mask_rules.upsert_rule(MaskRuleDefinition(rule_key="full", handler="full"))
    storage.mask_rules.put_config(JOINABLE_SALT_KEY, "super-secret-salt", "tester")
    storage.policy_exceptions.upsert_exception(
        PolicyException(
            exception_id="e-user",
            principal_name="vip@example.com",
            principal_type=PrincipalKind.USER,
            created_at=NOW,
        )
    )
    storage.policy_exceptions.upsert_exception(
        PolicyException(
            exception_id="e-sys",
            principal_name="app-sp",
            principal_type=PrincipalKind.SERVICE_PRINCIPAL,
            created_at=NOW,
            system=True,
        )
    )
    storage.settings.put_many(
        {"data_access": {"access_tag_key": "access", "client_secret": "leaky-value"}}, "tester"
    )


def _files(preview) -> dict[str, str]:  # noqa: ANN001
    return {f.path: f.content for f in preview.files}


def test_preview_files_are_stable_and_exclude_secrets(container: ServiceContainer) -> None:
    _seed(container)
    preview = container.recovery.build_preview(_admin())  # type: ignore[arg-type]
    files = _files(preview)

    assert list(files) == [
        "manifest.yaml",
        "settings/data_access.yaml",
        "identity/attribute_definitions.yaml",
        "identity/assignments.yaml",
        "approvals/category_approvers.yaml",
        "masking/rules.yaml",
        "masking/config.yaml",
        "exceptions/policy_exceptions.yaml",
    ]
    all_text = "\n".join(files.values())
    assert "super-secret-salt" not in all_text
    assert "leaky-value" not in all_text
    assert "<redacted>" in all_text

    config = yaml.safe_load(files["masking/config.yaml"])
    assert config["joinable_salt"] == {"configured": True, "value": "<redacted>"}

    assignments = yaml.safe_load(files["identity/assignments.yaml"])
    assert assignments["count"] == 1
    assert assignments["assignments"][0]["attribute_value"] == "finance.budget"

    exceptions = yaml.safe_load(files["exceptions/policy_exceptions.yaml"])
    assert [e["principal_name"] for e in exceptions["policy_exceptions"]] == ["vip@example.com"]

    assert any("joinable salt is not exported" in w.lower() for w in preview.warnings)
    assert preview.target == ""
    assert preview.warnings[0].startswith("No Git repository is configured")


def test_preview_is_deterministic_across_runs(container: ServiceContainer) -> None:
    _seed(container)
    first = _files(container.recovery.build_preview(_admin()))  # type: ignore[arg-type]
    second = _files(container.recovery.build_preview(_admin()))  # type: ignore[arg-type]
    for path in first:
        if path == "manifest.yaml":
            continue  # carries generated_at
        assert first[path] == second[path], path


def test_settings_default_then_round_trip(container: ServiceContainer) -> None:
    assert container.recovery.get_settings().branch == "main"
    body = RecoveryGitSettings(
        repo_url="https://github.com/acme/gov-backup.git",
        branch="recovery",
        path_prefix="exports/dev",
        username="svc-git",
        token_secret_key="git-token-dev",
    )
    container.recovery.save_settings(body, _admin())  # type: ignore[arg-type]
    assert container.recovery.get_settings() == body

    preview = container.recovery.build_preview(_admin())  # type: ignore[arg-type]
    assert preview.target == "https://github.com/acme/gov-backup.git@recovery:exports/dev"
    assert "git-token-dev" in yaml.safe_dump(preview.git.model_dump(mode="json"))
    events = container.storage.audit.list_recent(5)
    assert any(e.event_type == "recovery.settings_saved" for e in events)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repo_url": "http://github.com/acme/x.git"},
        {"repo_url": "https://github.com/acme/x git"},
        {"path_prefix": "../escape"},
        {"path_prefix": "/abs"},
        {"branch": "bad branch"},
        {"token_secret_key": "bad/key"},
    ],
)
def test_settings_reject_unsafe_values(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        RecoveryGitSettings(**kwargs)


def test_preview_api_is_admin_only(client: TestClient, container: ServiceContainer) -> None:
    _seed(container)
    denied = client.post("/api/recovery/export/preview", headers=headers_for("analyst@example.com"))
    assert denied.status_code == 403

    allowed = client.post("/api/recovery/export/preview", headers=headers_for("admin@example.com"))
    assert allowed.status_code == 200
    body = allowed.json()
    assert {f["path"] for f in body["files"]} >= {"manifest.yaml", "masking/config.yaml"}
    assert "generatedAt" in body

    assert client.get("/api/recovery/settings", headers=headers_for("analyst@example.com")).status_code == 403


class _FakeSecrets:
    """Stands in for ``workspace.app().secrets``; values are stored base64-encoded like the API."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.calls: list[tuple[str, str]] = []

    def get_secret(self, scope: str, key: str) -> SimpleNamespace:
        self.calls.append((scope, key))
        if key not in self._values:
            raise RuntimeError("secret does not exist")
        return SimpleNamespace(value=base64.b64encode(self._values[key].encode()).decode())


class _FakePublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error

    def push_files(self, **kwargs) -> PushOutcome:  # noqa: ANN003
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return PushOutcome(commit_sha="c0ffee1234", committed=True, branch_created=False)


def _configure_git(container: ServiceContainer, secret_key: str = "git-token-dev") -> None:
    container.recovery.save_settings(
        RecoveryGitSettings(
            repo_url="https://github.com/acme/gov-backup.git",
            branch="main",
            path_prefix="exports/dev",
            username="svc-git",
            token_secret_key=secret_key,
        ),
        _admin(),  # type: ignore[arg-type]
    )


def _wire_push(container: ServiceContainer, secrets: _FakeSecrets, publisher: _FakePublisher) -> None:
    container.recovery._workspace = SimpleNamespace(app=lambda: SimpleNamespace(secrets=secrets))  # noqa: SLF001
    container.recovery._publisher = publisher  # noqa: SLF001


def test_push_reads_token_at_push_time_and_commits_export(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    secrets = _FakeSecrets({"git-token-dev": "tok-value-123"})
    publisher = _FakePublisher()
    _wire_push(container, secrets, publisher)

    result = container.recovery.push(_admin())  # type: ignore[arg-type]

    assert secrets.calls == [("databricks-governance-app", "git-token-dev")]
    (call,) = publisher.calls
    assert call["repo_url"] == "https://github.com/acme/gov-backup.git"
    assert call["branch"] == "main"
    assert call["path_prefix"] == "exports/dev"
    assert call["username"] == "svc-git"
    assert call["token"] == "tok-value-123"
    assert call["author_email"] == "admin@example.com"
    assert "manifest.yaml" in call["files"]
    assert "super-secret-salt" not in json.dumps(call["files"])

    assert result.commit_sha == "c0ffee1234"
    assert result.committed is True
    assert result.branch_created is False
    assert result.files == len(call["files"])
    assert result.target == "https://github.com/acme/gov-backup.git@main:exports/dev"
    assert "tok-value-123" not in result.model_dump_json()

    events = [e for e in container.storage.audit.list_recent(10) if e.event_type == "recovery.export_pushed"]
    assert len(events) == 1
    assert events[0].payload["commit_sha"] == "c0ffee1234"
    assert "tok-value-123" not in json.dumps(events[0].payload)


def test_push_requires_repository_and_path(container: ServiceContainer) -> None:
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({}), publisher)
    with pytest.raises(InvalidRequestError, match="repository URL"):
        container.recovery.push(_admin())  # type: ignore[arg-type]

    container.recovery.save_settings(
        RecoveryGitSettings(repo_url="https://github.com/acme/x.git", path_prefix=""),
        _admin(),  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidRequestError, match="path"):
        container.recovery.push(_admin())  # type: ignore[arg-type]
    assert publisher.calls == []


def test_push_stops_when_token_secret_is_missing(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container, secret_key="git-token-missing")
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({}), publisher)

    with pytest.raises(InvalidRequestError, match="git-token-missing"):
        container.recovery.push(_admin())  # type: ignore[arg-type]
    assert publisher.calls == []


def test_push_maps_git_failure_to_upstream_error(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    publisher = _FakePublisher(error=GitPublishError("git push failed (exit 1): rejected"))
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok-value-123"}), publisher)

    with pytest.raises(UpstreamError, match="rejected"):
        container.recovery.push(_admin())  # type: ignore[arg-type]
    events = container.storage.audit.list_recent(10)
    assert not any(e.event_type == "recovery.export_pushed" for e in events)


def test_push_api_is_admin_only_and_returns_commit(client: TestClient, container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok-value-123"}), _FakePublisher())

    denied = client.post("/api/recovery/export/push", headers=headers_for("analyst@example.com"))
    assert denied.status_code == 403

    allowed = client.post("/api/recovery/export/push", headers=headers_for("admin@example.com"))
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["commitSha"] == "c0ffee1234"
    assert body["committed"] is True
    assert "tok-value-123" not in allowed.text

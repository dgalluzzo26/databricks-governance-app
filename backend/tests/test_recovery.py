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

from backend.app.clients.git_publisher import GitPublishError, PullOutcome, PushOutcome
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.access_request import CategoryApprover
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    IdentityAttributeAssignment,
)
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.models.policy_exception import PolicyException
from backend.app.models.principal import PrincipalKind
from backend.app.models.recovery import RecoveryApplyRequest, RecoveryGitSettings
from backend.app.models.subscription import (
    ResourceSelector,
    SecurableKind,
    Subscription,
    SubscriptionResource,
    SubscriptionStatus,
    SubscriptionSubject,
    SubscriptionSyncState,
)
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
        "settings/app.yaml",
        "subscriptions/subscriptions.yaml",
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
        {"interval_hours": 0},
        {"interval_hours": 25},
    ],
)
def test_settings_reject_unsafe_values(kwargs: dict[str, object]) -> None:
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
    def __init__(
        self,
        error: Exception | None = None,
        *,
        pull_files: dict[str, str] | None = None,
        pull_sha: str = "c0ffee1234",
    ) -> None:
        self.calls: list[dict] = []
        self.pull_calls: list[dict] = []
        self._error = error
        self._pull_files = pull_files
        self._pull_sha = pull_sha

    def push_files(self, **kwargs) -> PushOutcome:  # noqa: ANN003
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return PushOutcome(commit_sha="c0ffee1234", committed=True, branch_created=False)

    def pull_files(self, **kwargs) -> PullOutcome:  # noqa: ANN003
        self.pull_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._pull_files is None:
            raise GitPublishError("no pull fixture")
        return PullOutcome(commit_sha=self._pull_sha, files=dict(self._pull_files))


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


def _snapshot_files(*, salt_configured: bool = True) -> dict[str, str]:
    """Minimal valid recovery snapshot with one row in each list section."""
    return {
        "manifest.yaml": yaml.safe_dump(
            {
                "version": 1,
                "generated_at": "2026-11-11T12:00:00+00:00",
                "generated_by": "admin@example.com",
                "files": ["manifest.yaml"],
            }
        ),
        "settings/data_access.yaml": yaml.safe_dump(
            {
                "version": 1,
                "data_access": {"access_tag_key": "access", "client_secret": "<redacted>"},
            }
        ),
        "identity/attribute_definitions.yaml": yaml.safe_dump(
            {
                "version": 1,
                "count": 1,
                "attribute_definitions": [
                    {"key": "sensitivity", "description": "from snapshot", "governed_tag_keys": []}
                ],
            }
        ),
        "identity/assignments.yaml": yaml.safe_dump(
            {
                "version": 1,
                "count": 1,
                "assignments": [
                    {
                        "assignment_id": "snap-a1",
                        "principal_name": "restored@example.com",
                        "principal_type": "USER",
                        "attribute_key": "sensitivity",
                        "attribute_value": "finance.budget",
                        "created_at": "2026-11-11T12:00:00+00:00",
                    }
                ],
            }
        ),
        "approvals/category_approvers.yaml": yaml.safe_dump(
            {
                "version": 1,
                "count": 1,
                "category_approvers": [
                    {
                        "approver_id": "snap-ap1",
                        "category_path": "finance.budget",
                        "principal_name": "steward@example.com",
                        "principal_type": "USER",
                        "created_at": "2026-11-11T12:00:00+00:00",
                    }
                ],
            }
        ),
        "masking/rules.yaml": yaml.safe_dump(
            {
                "version": 1,
                "count": 1,
                "mask_rules": [{"rule_key": "full", "handler": "full"}],
            }
        ),
        "masking/config.yaml": yaml.safe_dump(
            {
                "version": 1,
                "joinable_salt": {"configured": salt_configured, "value": "<redacted>"},
            }
        ),
        "exceptions/policy_exceptions.yaml": yaml.safe_dump(
            {
                "version": 1,
                "count": 1,
                "policy_exceptions": [
                    {
                        "exception_id": "snap-e1",
                        "principal_name": "vip@example.com",
                        "principal_type": "USER",
                        "created_at": "2026-11-11T12:00:00+00:00",
                        "system": False,
                    }
                ],
            }
        ),
    }


def test_import_parses_snapshot_and_flags_occupancy(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    publisher = _FakePublisher(pull_files=_snapshot_files())
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    preview = container.recovery.import_from_git(_admin())  # type: ignore[arg-type]

    assert preview.commit_sha == "c0ffee1234"
    assert preview.manifest.generated_by == "admin@example.com"
    assert {f.path for f in preview.files} >= {"manifest.yaml", "identity/assignments.yaml"}
    assert preview.occupancy.app_has_state is True
    assert "assignment" in preview.occupancy.summary
    assert any("already has governance state" in w for w in preview.warnings)
    assert publisher.pull_calls and publisher.pull_calls[0]["token"] == "tok"


def test_recover_requires_confirm_and_upserts_into_empty_app(container: ServiceContainer) -> None:
    _configure_git(container)
    publisher = _FakePublisher(pull_files=_snapshot_files(salt_configured=True))
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    with pytest.raises(InvalidRequestError, match="confirm"):
        container.recovery.recover(_admin(), RecoveryApplyRequest(confirm=False))  # type: ignore[arg-type]

    result = container.recovery.recover(_admin(), RecoveryApplyRequest(confirm=True))  # type: ignore[arg-type]

    assert result.app_had_state is False
    assert result.counts.definitions == 1
    assert result.counts.assignments == 1
    assert result.counts.approvers == 1
    assert result.counts.mask_rules == 1
    assert result.counts.policy_exceptions == 1
    assert result.counts.data_access == 1
    assert any("joinable salt" in w.lower() for w in result.warnings)

    defs = {d.key for d in container.storage.identity_attributes.list_definitions()}
    assert "sensitivity" in defs
    assignments = container.storage.identity_attributes.list_assignments(
        AttributeAssignmentFilter(include_inactive=False)
    )
    assert [a.assignment_id for a in assignments] == ["snap-a1"]

    data_access = container.storage.settings.get_all()["data_access"]
    assert data_access["access_tag_key"] == "access"
    assert "client_secret" not in data_access
    assert "<redacted>" not in json.dumps(data_access)

    events = [e for e in container.storage.audit.list_recent(10) if e.event_type == "recovery.imported"]
    assert len(events) == 1
    assert events[0].payload["commit_sha"] == "c0ffee1234"


def test_recover_keeps_local_extras_and_skips_system_exceptions(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    files = _snapshot_files()
    files["exceptions/policy_exceptions.yaml"] = yaml.safe_dump(
        {
            "version": 1,
            "count": 2,
            "policy_exceptions": [
                {
                    "exception_id": "snap-e1",
                    "principal_name": "restored-vip@example.com",
                    "principal_type": "USER",
                    "created_at": "2026-11-11T12:00:00+00:00",
                    "system": False,
                },
                {
                    "exception_id": "system:app",
                    "principal_name": "app-sp",
                    "principal_type": "SERVICE_PRINCIPAL",
                    "created_at": "2026-11-11T12:00:00+00:00",
                    "system": True,
                },
            ],
        }
    )
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), _FakePublisher(pull_files=files))

    result = container.recovery.recover(_admin(), RecoveryApplyRequest(confirm=True))  # type: ignore[arg-type]

    assert result.app_had_state is True
    local_ids = {
        a.assignment_id
        for a in container.storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(include_inactive=True)
        )
    }
    assert "a-active" in local_ids
    assert "snap-a1" in local_ids
    exceptions = {e.exception_id for e in container.storage.policy_exceptions.list_exceptions()}
    assert "snap-e1" in exceptions
    assert "system:app" not in exceptions
    assert "e-sys" in exceptions
    assert "e-user" in exceptions


def test_import_and_recover_api_are_admin_only(client: TestClient, container: ServiceContainer) -> None:
    _configure_git(container)
    _wire_push(
        container,
        _FakeSecrets({"git-token-dev": "tok"}),
        _FakePublisher(pull_files=_snapshot_files(salt_configured=False)),
    )

    assert client.post("/api/recovery/import", headers=headers_for("analyst@example.com")).status_code == 403
    assert (
        client.post(
            "/api/recovery/recover",
            headers=headers_for("analyst@example.com"),
            json={"confirm": True},
        ).status_code
        == 403
    )

    imported = client.post("/api/recovery/import", headers=headers_for("admin@example.com"))
    assert imported.status_code == 200
    assert imported.json()["commitSha"] == "c0ffee1234"
    assert "occupancy" in imported.json()

    recovered = client.post(
        "/api/recovery/recover",
        headers=headers_for("admin@example.com"),
        json={"confirm": True},
    )
    assert recovered.status_code == 200
    assert recovered.json()["counts"]["definitions"] == 1

    rejected = client.post(
        "/api/recovery/recover",
        headers=headers_for("admin@example.com"),
        json={"confirm": False},
    )
    assert rejected.status_code == 422


def _enable_schedule(container: ServiceContainer, *, interval_hours: int = 24) -> None:
    current = container.recovery.get_settings()
    container.recovery.save_settings(
        current.model_copy(update={"schedule_enabled": True, "interval_hours": interval_hours}),
        _admin(),  # type: ignore[arg-type]
    )


def test_scheduled_push_skips_when_disabled_or_unconfigured(container: ServiceContainer) -> None:
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    assert container.recovery.run_scheduled_push_if_due() is None
    assert publisher.calls == []

    _configure_git(container)
    assert container.recovery.run_scheduled_push_if_due() is None
    assert publisher.calls == []


def test_scheduled_push_runs_when_due_and_audits_as_scheduler(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    _enable_schedule(container, interval_hours=24)
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok-value-123"}), publisher)

    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    result = container.recovery.run_scheduled_push_if_due(now=now)

    assert result is not None
    assert result.pushed_by == "scheduler"
    assert result.committed is True
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["author_email"] == "governance-app@local"

    settings = container.recovery.get_settings()
    assert settings.last_scheduled_success_at is not None
    assert settings.last_scheduled_commit_sha == "c0ffee1234"
    assert settings.last_scheduled_committed is True
    assert settings.last_scheduled_error is None

    events = [e for e in container.storage.audit.list_recent(20) if e.event_type == "recovery.export_pushed"]
    assert events
    assert events[0].actor == "scheduler"
    assert events[0].payload.get("scheduled") is True


def test_scheduled_push_respects_interval_hours(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    _enable_schedule(container, interval_hours=1)
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    # Use wall clock: push records last_scheduled_success_at from result.pushed_at.
    first_at = datetime.now(UTC)
    assert container.recovery.run_scheduled_push_if_due(now=first_at) is not None
    assert len(publisher.calls) == 1
    success_at = container.recovery.get_settings().last_scheduled_success_at
    assert success_at is not None

    # Still within the 1-hour window.
    assert container.recovery.run_scheduled_push_if_due(now=success_at + timedelta(minutes=30)) is None
    assert len(publisher.calls) == 1

    # Due again after the interval.
    assert container.recovery.run_scheduled_push_if_due(now=success_at + timedelta(hours=1)) is not None
    assert len(publisher.calls) == 2

    _enable_schedule(container, interval_hours=24)
    latest = container.recovery.get_settings().last_scheduled_success_at
    assert latest is not None
    assert container.recovery.run_scheduled_push_if_due(now=latest + timedelta(hours=23)) is None
    assert container.recovery.run_scheduled_push_if_due(now=latest + timedelta(hours=24)) is not None
    assert len(publisher.calls) == 3


def test_scheduled_push_lease_blocks_second_instance(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    _enable_schedule(container)
    publisher = _FakePublisher()
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    # Use wall clock so the lease is not treated as stale by _LOCK_STALE.
    now = datetime.now(UTC)
    held = container.recovery._acquire_lock("peer-run", now)  # noqa: SLF001
    assert held is True

    assert container.recovery.run_scheduled_push_if_due(now=now) is None
    assert publisher.calls == []

    container.recovery._release_lock("peer-run")  # noqa: SLF001
    assert container.recovery.run_scheduled_push_if_due(now=now) is not None
    assert len(publisher.calls) == 1


def test_scheduled_push_records_error_without_advancing_success(container: ServiceContainer) -> None:
    _seed(container)
    _configure_git(container)
    _enable_schedule(container)
    publisher = _FakePublisher(error=GitPublishError("git push failed (exit 1): rejected"))
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher)

    now = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    assert container.recovery.run_scheduled_push_if_due(now=now) is None

    settings = container.recovery.get_settings()
    assert settings.last_scheduled_success_at is None
    assert settings.last_scheduled_error is not None
    assert "rejected" in settings.last_scheduled_error
    # Still due after a failure so the next tick retries.
    publisher_ok = _FakePublisher()
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), publisher_ok)
    assert container.recovery.run_scheduled_push_if_due(now=now + timedelta(minutes=5)) is not None
    assert container.recovery.get_settings().last_scheduled_error is None


def _subscription(name: str, subscription_id: str, *, synced: bool) -> Subscription:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    return Subscription(
        subscription_id=subscription_id,
        name=name,
        display_name=name.replace("_", " ").title(),
        group_name=f"dac_subscription_{name}",
        status=SubscriptionStatus.APPROVED,
        starts_at=now,
        expires_at=now + timedelta(days=30),
        subjects=[SubscriptionSubject(principal_name="ana@example.com", principal_type=PrincipalKind.USER)],
        resources=[
            SubscriptionResource(
                selector=ResourceSelector.OBJECT,
                securable_kind=SecurableKind.SCHEMA,
                full_name="dga_people.employees",
            )
        ],
        created_by="admin@example.com",
        created_at=now,
        updated_at=now,
        last_sync=SubscriptionSyncState(synced_at=now, active=True, matched_objects=1) if synced else None,
    )


def test_subscriptions_and_app_settings_round_trip(container: ServiceContainer) -> None:
    container.storage.subscriptions.save_subscription(_subscription("ia_build", "sub-src", synced=True))
    container.storage.settings.put_many(
        {"app_runtime": {"subscriptions_require_admin_approval": False, "subscription_max_days": 90}}, "t"
    )
    files = _files(container.recovery.build_preview(_admin()))  # type: ignore[arg-type]

    exported = yaml.safe_load(files["subscriptions/subscriptions.yaml"])
    assert exported["count"] == 1
    assert exported["subscriptions"][0]["name"] == "ia_build"
    assert "last_sync" not in exported["subscriptions"][0]
    assert yaml.safe_load(files["settings/app.yaml"])["app_settings"]["subscription_max_days"] == 90

    container.storage.subscriptions.delete_subscription("sub-src")
    container.storage.subscriptions.save_subscription(_subscription("ia_build", "sub-local", synced=True))
    container.storage.settings.put_many({"app_runtime": {}}, "t")
    _configure_git(container)
    pulled = {path: content for path, content in files.items()}
    _wire_push(container, _FakeSecrets({"git-token-dev": "tok"}), _FakePublisher(pull_files=pulled))

    result = container.recovery.recover(_admin(), RecoveryApplyRequest(confirm=True))  # type: ignore[arg-type]

    assert result.counts.subscriptions == 1
    assert result.counts.app_settings == 1
    assert any("subscription" in w.lower() for w in result.warnings)
    restored = container.storage.subscriptions.list_subscriptions()
    assert [(s.subscription_id, s.name) for s in restored] == [("sub-local", "ia_build")]
    assert restored[0].last_sync is not None
    assert container.storage.settings.get_all()["app_runtime"]["subscription_max_days"] == 90

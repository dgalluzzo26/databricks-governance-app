from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.models.data_access import DataAccessSettings
from backend.app.services.data_access_planner import (
    ActualGrant,
    ActualMember,
    AssignmentRow,
    TagRow,
    group_name,
    plan_data_access,
)
from backend.tests.conftest import headers_for


def _tag(level: str, catalog: str, schema: str | None, table: str | None, key: str, value: str) -> TagRow:
    return TagRow(level, catalog, schema, table, key, value)


def _reviewed(level: str, catalog: str, schema: str | None, table: str | None, value: str) -> list[TagRow]:
    return [
        _tag(level, catalog, schema, table, "data_access_control", value),
        _tag(level, catalog, schema, table, "clr_review_status", "reviewed"),
    ]


def test_group_name_slugifies_hierarchy() -> None:
    assert group_name("dac_", "finance.budget") == "dac_finance_budget"


def test_plan_grants_select_only_on_tables() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "public", None, "public"),
        *_reviewed("table", "sandbox", "finance", "budget", "finance.budget"),
        *_reviewed("table", "sandbox", "finance", "forecast", "finance"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    add = {(g.securable_type, g.full_name, g.principal, g.privilege) for g in plan.grants_to_add}
    # Schema tags create groups for nesting but never get SELECT.
    assert ("schema", "sandbox.public", "dac_public", "SELECT") not in add
    assert ("table", "sandbox.finance.budget", "dac_finance_budget", "SELECT") in add
    assert ("table", "sandbox.finance.forecast", "dac_finance", "SELECT") in add
    assert ("schema", "sandbox.finance", "dac_finance_budget", "USE SCHEMA") in add
    assert ("catalog", "sandbox", "dac_finance_budget", "USE CATALOG") in add
    parents = {g.value: g.parent_group for g in plan.groups}
    assert parents["finance.budget"] == "dac_finance"
    assert "public" in parents
    assert plan.summary.eligible_objects == 3


def test_plan_skips_unreviewed_objects() -> None:
    tags = [_tag("table", "sandbox", "finance", "secrets", "data_access_control", "finance")]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    assert plan.summary.eligible_objects == 0
    assert plan.summary.skipped_unreviewed == 1
    assert plan.grants_to_add == []


def test_schema_tag_mismatch_reports_conflict_without_schema_select() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "finance", None, "finance"),
        *_reviewed("table", "sandbox", "finance", "contracts", "finance.contracts"),
        *_reviewed("table", "sandbox", "finance", "other", "hr"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    assert plan.summary.conflicts >= 1
    assert not any(
        g.securable_type == "schema" and g.privilege == "SELECT" for g in plan.grants_to_add
    )
    assert any(g.full_name.endswith(".contracts") and g.privilege == "SELECT" for g in plan.grants_to_add)
    assert any(g.full_name.endswith(".other") and g.privilege == "SELECT" for g in plan.grants_to_add)
    # Nested parent group still planned from schema tag value.
    assert any(g.value == "finance" for g in plan.groups)


def test_plan_revokes_stale_managed_grants() -> None:
    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    actual = [
        ActualGrant("table", "sandbox.finance.old", "dac_finance_budget", "SELECT"),
        ActualGrant("table", "sandbox.finance.budget", "other_group", "SELECT"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, actual, {"dac_finance_budget"})
    assert any(g.full_name == "sandbox.finance.old" for g in plan.grants_to_revoke)
    assert all(g.principal != "other_group" for g in plan.grants_to_revoke)
    assert plan.summary.groups_existing == 1
    assert plan.summary.groups_to_create == 0


def test_plan_revokes_previous_group_after_tag_change() -> None:
    """Retagging a table from public → non-public must revoke dac_public SELECT."""
    tags = [*_reviewed("table", "sandbox", "default", "us_states", "non-public")]
    actual = [
        ActualGrant("table", "sandbox.default.us_states", "dac_public", "SELECT"),
        ActualGrant("table", "sandbox.default.us_states", "dac_non_public", "SELECT"),
        ActualGrant("catalog", "sandbox", "dac_public", "USE CATALOG"),
        ActualGrant("schema", "sandbox.default", "dac_public", "USE SCHEMA"),
        ActualGrant("catalog", "sandbox", "dac_non_public", "USE CATALOG"),
        ActualGrant("schema", "sandbox.default", "dac_non_public", "USE SCHEMA"),
    ]
    plan = plan_data_access(
        DataAccessSettings(),
        tags,
        actual,
        {"dac_public", "dac_non_public"},
    )
    revoked = {(g.principal, g.privilege, g.full_name) for g in plan.grants_to_revoke}
    assert ("dac_public", "SELECT", "sandbox.default.us_states") in revoked
    assert ("dac_public", "USE CATALOG", "sandbox") in revoked
    assert ("dac_public", "USE SCHEMA", "sandbox.default") in revoked
    assert ("dac_non_public", "SELECT", "sandbox.default.us_states") not in revoked


def test_inherited_from_none_is_direct_grant() -> None:
    from backend.app.services.data_access import DataAccessService

    assert DataAccessService._is_inherited_privilege(None) is False  # noqa: SLF001
    assert DataAccessService._is_inherited_privilege("NONE") is False  # noqa: SLF001
    assert DataAccessService._is_inherited_privilege("none") is False  # noqa: SLF001
    assert DataAccessService._is_inherited_privilege("") is False  # noqa: SLF001
    assert DataAccessService._is_inherited_privilege("SCHEMA") is True  # noqa: SLF001


def test_plan_reports_drift_against_previous_fingerprint() -> None:
    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    first = plan_data_access(DataAccessSettings(), tags, [], set())
    second = plan_data_access(
        DataAccessSettings(),
        tags,
        [],
        set(),
        previous_fingerprint=first.fingerprint,
        previous_summary=first.summary,
    )
    assert second.drift_since_last_run == ["Desired state matches the last successful dry-run"]
    extra = [*tags, *_reviewed("table", "sandbox", "finance", "contracts", "finance.contracts")]
    third = plan_data_access(
        DataAccessSettings(),
        extra,
        [],
        set(),
        previous_fingerprint=first.fingerprint,
        previous_summary=first.summary,
    )
    assert any("changed" in note for note in third.drift_since_last_run)


def test_plan_membership_add_and_remove() -> None:
    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    assignments = [
        AssignmentRow("ana@example.com", "USER", "finance.budget"),
        AssignmentRow("finance-owners", "GROUP", "finance.budget"),
    ]
    actual_members = [
        ActualMember("dac_finance_budget", "stale@example.com", "USER"),
        ActualMember("dac_finance_budget", "dac_finance", "GROUP"),
    ]
    plan = plan_data_access(
        DataAccessSettings(),
        tags,
        [],
        {"dac_finance_budget", "dac_finance"},
        assignments=assignments,
        actual_members=actual_members,
    )
    adds = {(m.principal_name, m.action.value) for m in plan.membership_changes}
    assert ("ana@example.com", "add") in adds
    assert ("finance-owners", "add") in adds
    assert ("stale@example.com", "remove") in adds
    # Nested parent group is never removed.
    assert ("dac_finance", "remove") not in adds
    assert plan.summary.members_to_add == 2
    assert plan.summary.members_to_remove == 1


def test_settings_round_trip_allows_dry_run_flag(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    body = client.get("/api/data-access/settings", headers=admin).json()
    assert body["minIntervalMinutes"] == 5
    assert body["settings"]["accessTagKey"] == "data_access_control"
    assert body["applyAvailable"] is True

    saved = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**body["settings"], "intervalMinutes": 20, "allowedCatalogs": ["sandbox"]},
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["intervalMinutes"] == 20

    too_small = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**body["settings"], "intervalMinutes": 1},
    )
    assert too_small.status_code == 422

    apply_pref = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**body["settings"], "dryRun": False},
    )
    assert apply_pref.status_code == 200
    assert apply_pref.json()["settings"]["dryRun"] is False


def test_data_access_forbidden_for_non_admin(client: TestClient) -> None:
    user = headers_for("analyst@example.com")
    assert client.get("/api/data-access/settings", headers=user).status_code == 403
    assert client.post("/api/data-access/runs", headers=user).status_code == 403


def test_dry_run_uses_injected_inventory(client: TestClient, container) -> None:
    settings = DataAccessSettings()
    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    container.data_access._tag_loader = lambda _settings: tags  # noqa: SLF001
    container.data_access._grant_loader = lambda _settings: []  # noqa: SLF001
    container.data_access._group_loader = lambda _settings: set()  # noqa: SLF001
    container.data_access._member_loader = lambda _settings, _groups: []  # noqa: SLF001
    container.data_access._assignment_loader = lambda _settings: []  # noqa: SLF001
    del settings

    run = client.post(
        "/api/data-access/runs",
        headers=headers_for("admin@example.com"),
        json={"mode": "dry_run"},
    ).json()
    assert run["status"] == "success"
    assert run["mode"] == "dry_run"
    assert run["plan"]["summary"]["eligibleObjects"] == 1
    assert run["plan"]["grantsToAdd"]
    admin = headers_for("admin@example.com")
    latest = client.get("/api/data-access/runs/latest?mode=dry_run", headers=admin).json()
    assert latest["runId"] == run["runId"]
    history = client.get("/api/data-access/runs?mode=dry_run&page=1", headers=admin).json()
    assert history["total"] == 1
    assert history["retentionDays"] == 30
    assert history["runs"][0]["runId"] == run["runId"]
    assert history["runs"][0]["plan"] is None
    apply_only = client.get("/api/data-access/runs?mode=apply", headers=admin).json()
    assert apply_only["total"] == 0


def test_settings_schedule_mode_and_pause(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    current = client.get("/api/data-access/settings", headers=admin).json()["settings"]
    saved = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**current, "scheduleEnabled": False, "scheduleMode": "apply"},
    )
    assert saved.status_code == 200
    body = saved.json()["settings"]
    assert body["scheduleEnabled"] is False
    assert body["scheduleMode"] == "apply"


def test_apply_runs_when_readiness_forced(client: TestClient, container) -> None:
    from backend.app.models.data_access import DataAccessPlan, DataAccessReadiness, ReadinessCheck

    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    container.data_access._tag_loader = lambda _settings: tags  # noqa: SLF001
    container.data_access._grant_loader = lambda _settings: []  # noqa: SLF001
    container.data_access._group_loader = lambda _settings: set()  # noqa: SLF001
    container.data_access._member_loader = lambda _settings, _groups: []  # noqa: SLF001
    container.data_access._assignment_loader = lambda _settings: [  # noqa: SLF001
        AssignmentRow("ana@example.com", "USER", "finance.budget"),
    ]
    container.data_access.readiness = lambda: DataAccessReadiness(  # type: ignore[method-assign]
        dry_run_ready=True,
        apply_ready=True,
        checks=[
            ReadinessCheck(id="credentials", label="c", ok=True, required_for="dry_run"),
        ],
        setup_checks=[],
        setup_ready=True,
    )
    applied: list[str] = []

    def _fake_apply(settings, plan):  # noqa: ANN001
        applied.append("yes")
        return plan

    container.data_access._apply_plan = _fake_apply  # noqa: SLF001

    run = client.post(
        "/api/data-access/runs",
        headers=headers_for("admin@example.com"),
        json={"mode": "apply"},
    ).json()
    assert run["status"] == "success"
    assert run["mode"] == "apply"
    assert applied == ["yes"]
    assert run["plan"]["summary"]["membersToAdd"] == 1
    del DataAccessPlan


def test_readiness_reports_checks(client: TestClient) -> None:
    body = client.get("/api/data-access/readiness", headers=headers_for("admin@example.com")).json()
    ids = {c["id"] for c in body["checks"]}
    assert {"credentials", "authenticate", "warehouse", "read_tags", "list_groups"} <= ids
    assert body["applyReady"] is False
    setup_ids = {c["id"] for c in body["setupChecks"]}
    assert setup_ids == {"sensitivity_definition", "data_access_definition"}
    assert body["setupReady"] is False

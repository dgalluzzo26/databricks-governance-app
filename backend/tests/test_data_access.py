from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.models.data_access import ConflictPolicy, DataAccessSettings
from backend.app.services.data_access_planner import ActualGrant, TagRow, group_name, plan_data_access
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


def test_plan_grants_at_tagged_level_when_both_tags_present() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "public", None, "public"),
        *_reviewed("table", "sandbox", "finance", "budget", "finance.budget"),
        *_reviewed("table", "sandbox", "finance", "forecast", "finance"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    add = {(g.securable_type, g.full_name, g.principal, g.privilege) for g in plan.grants_to_add}
    assert ("schema", "sandbox.public", "dac_public", "SELECT") in add
    assert ("schema", "sandbox.public", "dac_public", "USE SCHEMA") in add
    assert ("catalog", "sandbox", "dac_public", "USE CATALOG") in add
    assert ("table", "sandbox.finance.budget", "dac_finance_budget", "SELECT") in add
    parents = {g.value: g.parent_group for g in plan.groups}
    assert parents["finance.budget"] == "dac_finance"
    assert plan.summary.eligible_objects == 3


def test_plan_skips_unreviewed_objects() -> None:
    tags = [_tag("table", "sandbox", "finance", "secrets", "data_access_control", "finance")]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    assert plan.summary.eligible_objects == 0
    assert plan.summary.skipped_unreviewed == 1
    assert plan.grants_to_add == []


def test_hold_policy_omits_schema_grant_when_child_has_different_value() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "finance", None, "finance"),
        *_reviewed("table", "sandbox", "finance", "contracts", "finance.contracts"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    assert plan.summary.conflicts == 1
    schema_select = [
        g for g in plan.grants_to_add if g.securable_type == "schema" and g.privilege == "SELECT"
    ]
    assert schema_select == []
    assert any(g.full_name.endswith(".contracts") for g in plan.grants_to_add)


def test_warn_policy_keeps_ancestor_grant_and_reports_conflict() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "finance", None, "finance"),
        *_reviewed("table", "sandbox", "finance", "contracts", "finance.contracts"),
    ]
    settings = DataAccessSettings(conflict_policy=ConflictPolicy.WARN)
    plan = plan_data_access(settings, tags, [], set())
    assert plan.summary.conflicts == 1
    assert any(g.securable_type == "schema" and g.privilege == "SELECT" for g in plan.grants_to_add)


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


def test_settings_round_trip_and_rejects_apply(client: TestClient) -> None:
    admin = headers_for("admin@example.com")
    body = client.get("/api/data-access/settings", headers=admin).json()
    assert body["minIntervalMinutes"] == 5
    assert body["settings"]["accessTagKey"] == "data_access_control"
    assert body["applyAvailable"] is False

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

    apply = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**body["settings"], "dryRun": False},
    )
    assert apply.status_code == 422


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
    del settings

    run = client.post("/api/data-access/runs", headers=headers_for("admin@example.com")).json()
    assert run["status"] == "success"
    assert run["mode"] == "dry_run"
    assert run["plan"]["summary"]["eligibleObjects"] == 1
    assert run["plan"]["grantsToAdd"]
    latest = client.get("/api/data-access/runs/latest", headers=headers_for("admin@example.com")).json()
    assert latest["runId"] == run["runId"]


def test_readiness_reports_checks(client: TestClient) -> None:
    body = client.get("/api/data-access/readiness", headers=headers_for("admin@example.com")).json()
    ids = {c["id"] for c in body["checks"]}
    assert {"credentials", "authenticate", "warehouse", "read_tags", "list_groups"} <= ids
    assert body["applyReady"] is False
    setup_ids = {c["id"] for c in body["setupChecks"]}
    assert setup_ids == {"sensitivity_definition", "data_access_definition"}
    assert body["setupReady"] is False

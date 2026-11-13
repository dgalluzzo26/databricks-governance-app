from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import DatabricksError
from fastapi.testclient import TestClient

from backend.app.core.errors import InvalidRequestError, NotConfiguredError, NotFoundError
from backend.app.models.data_access import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DataAccessPlan,
    DataAccessPlanSummary,
    DataAccessReadiness,
    DataAccessRun,
    DataAccessSettings,
    MembershipAction,
    MembershipChange,
    PlannedGrant,
    PlannedGroup,
    SyncRunStatus,
    SyncTrigger,
)
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services.data_access import DataAccessService, collapse_membership_by_principal_id
from backend.app.services.data_access_gap import GapKind, grant_key
from backend.app.services.data_access_planner import (
    ActualGrant,
    ActualMember,
    AssignmentRow,
    TagRow,
    group_name,
    normalize_privilege,
    plan_data_access,
)
from backend.app.services.data_access_profile import RunProfiler, profiling
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


def test_plan_marks_existing_nests_from_actual_members() -> None:
    tags = [
        *_reviewed("table", "sandbox", "finance", "root", "finance"),
        *_reviewed("table", "sandbox", "finance", "budget", "finance.budget"),
    ]
    members = [
        ActualMember(group_name="dac_finance_budget", principal_name="dac_finance", principal_type="GROUP"),
    ]
    plan = plan_data_access(
        DataAccessSettings(), tags, [], {"dac_finance", "dac_finance_budget"}, actual_members=members
    )
    by_name = {g.name: g for g in plan.groups}
    assert by_name["dac_finance_budget"].parent_group == "dac_finance"
    assert by_name["dac_finance_budget"].nest_exists is True
    assert by_name["dac_finance"].nest_exists is False


def test_plan_skips_unreviewed_objects() -> None:
    tags = [_tag("table", "sandbox", "finance", "secrets", "data_access_control", "finance")]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    assert plan.summary.eligible_objects == 0
    assert plan.summary.skipped_unreviewed == 1
    assert plan.grants_to_add == []


def test_schema_tag_mismatch_does_not_conflict_when_grants_are_table_only() -> None:
    tags = [
        *_reviewed("schema", "sandbox", "finance", None, "finance"),
        *_reviewed("table", "sandbox", "finance", "contracts", "finance.contracts"),
        *_reviewed("table", "sandbox", "finance", "other", "hr"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, [], set())
    # Catalog/schema tags only seed groups; SELECT is table-only so mismatches are not conflicts.
    assert plan.summary.conflicts == 0
    assert plan.conflicts == []
    assert not any(g.securable_type == "schema" and g.privilege == "SELECT" for g in plan.grants_to_add)
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


def test_normalize_privilege_maps_information_schema_underscores() -> None:
    assert normalize_privilege("USE_CATALOG") == "USE CATALOG"
    assert normalize_privilege("use_schema") == "USE SCHEMA"
    assert normalize_privilege("SELECT") == "SELECT"
    assert normalize_privilege("  USE   CATALOG ") == "USE CATALOG"


def test_plan_treats_use_catalog_underscore_as_already_granted() -> None:
    """information_schema returns USE_CATALOG; planner desires USE CATALOG — must match."""
    tags = [*_reviewed("table", "sandbox", "finance", "budget", "finance.budget")]
    actual = [
        ActualGrant("catalog", "sandbox", "dac_finance_budget", "USE_CATALOG"),
        ActualGrant("schema", "sandbox.finance", "dac_finance_budget", "USE_SCHEMA"),
        ActualGrant("table", "sandbox.finance.budget", "dac_finance_budget", "SELECT"),
    ]
    plan = plan_data_access(DataAccessSettings(), tags, actual, {"dac_finance_budget"})
    assert plan.grants_to_add == []
    assert plan.grants_to_revoke == []
    assert plan.summary.grants_unchanged == 3


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


def test_plan_membership_matches_email_not_display_name_once_canonical() -> None:
    """After member load resolves display name → userName, no add/remove churn."""
    tags = [*_reviewed("table", "sandbox", "default", "t1", "public")]
    assignments = [AssignmentRow("david.galluzzo@databricks.com", "USER", "public")]
    actual_members = [
        ActualMember("dac_public", "david.galluzzo@databricks.com", "USER"),
    ]
    plan = plan_data_access(
        DataAccessSettings(),
        tags,
        [],
        {"dac_public"},
        assignments=assignments,
        actual_members=actual_members,
    )
    assert plan.membership_changes == []


def test_collapse_membership_keeps_add_when_email_and_display_name_conflict() -> None:
    from backend.app.models.data_access import MembershipAction, MembershipChange
    from backend.app.services.data_access import collapse_membership_by_principal_id

    add = MembershipChange(
        group="dac_public",
        principal_name="david.galluzzo@databricks.com",
        principal_type="USER",
        action=MembershipAction.ADD,
        reason="Identity assignment public",
    )
    remove = MembershipChange(
        group="dac_public",
        principal_name="David Galluzzo",
        principal_type="USER",
        action=MembershipAction.REMOVE,
        reason="no active Identity assignment for this group",
    )
    # Same SCIM user id for both names.
    collapsed = collapse_membership_by_principal_id([("g1", "user-1", add), ("g1", "user-1", remove)])
    assert len(collapsed) == 1
    assert collapsed[0][2].action == MembershipAction.ADD
    assert collapsed[0][2].principal_name == "david.galluzzo@databricks.com"

    collapsed_rev = collapse_membership_by_principal_id([("g1", "user-1", remove), ("g1", "user-1", add)])
    assert len(collapsed_rev) == 1
    assert collapsed_rev[0][2].action == MembershipAction.ADD


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
    assert body["settings"]["enrollmentCatalogTagKey"] == "data_access_controlled"
    assert body["settings"]["enrollmentCatalogTagValue"] == "true"
    assert body["applyAvailable"] is True
    assert "enrolledCatalogs" in body

    saved = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={
            **body["settings"],
            "intervalMinutes": 20,
            "enrollmentCatalogTagKey": "data_access_controlled",
            "enrollmentCatalogTagValue": "true",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["intervalMinutes"] == 20
    assert saved.json()["settings"]["enrollmentCatalogTagKey"] == "data_access_controlled"

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
    assert history["runs"][0]["durationSeconds"] is not None
    fetched = client.get(f"/api/data-access/runs/{run['runId']}", headers=admin)
    assert fetched.status_code == 200
    assert fetched.json()["plan"]["grantsToAdd"]
    assert fetched.json()["durationSeconds"] is not None
    missing = client.get("/api/data-access/runs/does-not-exist", headers=admin)
    assert missing.status_code == 404
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


def test_apply_receives_full_plan_but_stored_run_is_truncated(client: TestClient, container) -> None:
    from backend.app.models.data_access import PLAN_ITEM_LIMIT, DataAccessReadiness

    table_count = PLAN_ITEM_LIMIT + 10
    tags = [
        row
        for i in range(table_count)
        for row in _reviewed("table", "sandbox", "finance", f"t{i}", "finance")
    ]
    container.data_access._tag_loader = lambda _settings: tags  # noqa: SLF001
    container.data_access._grant_loader = lambda _settings: []  # noqa: SLF001
    container.data_access._group_loader = lambda _settings: set()  # noqa: SLF001
    container.data_access._member_loader = lambda _settings, _groups: []  # noqa: SLF001
    container.data_access._assignment_loader = lambda _settings: []  # noqa: SLF001
    container.data_access.readiness = lambda: DataAccessReadiness(  # type: ignore[method-assign]
        dry_run_ready=True, apply_ready=True, checks=[], setup_checks=[], setup_ready=True
    )
    seen: list[int] = []

    def _fake_apply(settings, plan):  # noqa: ANN001
        seen.append(len(plan.grants_to_add))
        return plan

    container.data_access._apply_plan = _fake_apply  # noqa: SLF001

    run = client.post(
        "/api/data-access/runs",
        headers=headers_for("admin@example.com"),
        json={"mode": "apply"},
    ).json()
    # One SELECT per table plus shared USE CATALOG / USE SCHEMA.
    assert seen == [table_count + 2]
    assert run["plan"]["summary"]["grantsToAdd"] == table_count + 2
    assert len(run["plan"]["grantsToAdd"]) == PLAN_ITEM_LIMIT
    assert run["plan"]["truncated"] is True


def test_readiness_reports_checks(client: TestClient) -> None:
    body = client.get("/api/data-access/readiness", headers=headers_for("admin@example.com")).json()
    ids = {c["id"] for c in body["checks"]}
    assert {"credentials", "authenticate", "warehouse", "read_tags", "list_groups"} <= ids
    assert body["applyReady"] is False
    setup_ids = {c["id"] for c in body["setupChecks"]}
    assert setup_ids == {"sensitivity_definition", "data_access_definition"}
    assert body["setupReady"] is False


# ---------------------------------------------------------------------------
# DataAccessService unit tests. Fakes replace the warehouse, Account SCIM, and identity
# service so each branch (run history, locks, scheduler, apply steps) is exercised directly.
# ---------------------------------------------------------------------------


class _SqlFake:
    """Warehouse stand-in: returns rows for the first route whose key appears in the SQL."""

    def __init__(
        self,
        routes: dict[str, list[dict[str, Any]]] | None = None,
        *,
        configured: bool = True,
        query_error: Exception | None = None,
        execute_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.configured = configured
        self.routes = routes or {}
        self.query_error = query_error
        self.execute_errors = execute_errors or {}
        self.statements: list[str] = []
        self.executed: list[str] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        del parameters
        self.statements.append(statement)
        if self.query_error is not None:
            raise self.query_error
        for needle, rows in self.routes.items():
            if needle in statement:
                return list(rows)
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        del parameters
        self.executed.append(statement)
        for prefix, error in self.execute_errors.items():
            if statement.startswith(prefix):
                raise error


class _ScimApi:
    """Records Account SCIM / workspace API calls and answers them with ``handler``."""

    def __init__(self, handler=None) -> None:  # noqa: ANN001
        self.handler = handler or (lambda method, path, query, body: {})
        self.calls: list[tuple[str, str, Any, Any]] = []

    def do(self, method: str, path: str, query=None, body=None):  # noqa: ANN001, ANN201
        self.calls.append((method, path, query, body))
        return self.handler(method, path, query, body)


def _raise_on_call(error: Exception):  # noqa: ANN202
    def _handler(*_args):  # noqa: ANN002, ANN202
        raise error

    return _handler


def _admin(  # noqa: ANN202
    api: _ScimApi | None = None,
    *,
    me: object | None = None,
    me_error: Exception | None = None,
    groups: tuple | list = (),
    groups_error: Exception | None = None,
    users: tuple | list = (),
    service_principals: tuple | list = (),
):
    def _me():  # noqa: ANN202
        if me_error is not None:
            raise me_error
        return me if me is not None else SimpleNamespace(user_name="sp@example.com")

    def _groups_list(**_kwargs):  # noqa: ANN003, ANN202
        if groups_error is not None:
            raise groups_error
        return iter(groups)

    def _users_list(filter: str = "", **_kwargs):  # noqa: A002, ANN003, ANN202
        return iter([u for u in users if f'"{u.user_name}"' in filter])

    return SimpleNamespace(
        api_client=api or _ScimApi(),
        current_user=SimpleNamespace(me=_me),
        groups=SimpleNamespace(list=_groups_list),
        users=SimpleNamespace(list=_users_list),
        service_principals=SimpleNamespace(list=lambda **_kwargs: iter(service_principals)),
    )


class _Factory:
    def __init__(self, admin: object, *, configured: bool = True) -> None:
        self._admin = admin
        self.admin_configured = configured

    def admin(self):  # noqa: ANN201
        return self._admin

    def app(self):  # noqa: ANN201
        return self._admin


class _Identity:
    """Identity service stand-in for setup checks."""

    def __init__(
        self, *, sensitivity=None, data_access=None, access_tag: str = "data_access_control"
    ) -> None:  # noqa: ANN001
        self._sensitivity = sensitivity
        self._data_access = data_access
        self._access_tag = access_tag

    def access_tag_key(self) -> str:
        return self._access_tag

    def find_sensitivity_definition(self):  # noqa: ANN201
        return self._sensitivity

    def find_data_access_definition(self, access_tag_key=None):  # noqa: ANN001, ANN201
        del access_tag_key
        return self._data_access


class _JobsApi:
    """Records Job schedule updates and run_now_and_wait calls."""

    def __init__(
        self, *, on_run=None, run_error: Exception | None = None, update_error: Exception | None = None
    ) -> None:  # noqa: ANN001
        self.on_run = on_run
        self.run_error = run_error
        self.update_error = update_error
        self.run_calls: list[dict[str, Any]] = []
        self.update_calls: list[Any] = []

    def run_now_and_wait(self, job_id: int, **kwargs):  # noqa: ANN003, ANN201
        self.run_calls.append({"job_id": job_id, **kwargs})
        if self.run_error is not None:
            raise self.run_error
        if self.on_run is not None:
            return self.on_run(job_id, **kwargs)
        return SimpleNamespace(state=SimpleNamespace(life_cycle_state="TERMINATED"))

    def update(self, job_id: int, *, new_settings=None, fields_to_remove=None):  # noqa: ANN001, ANN201
        del fields_to_remove
        self.update_calls.append({"job_id": job_id, "new_settings": new_settings})
        if self.update_error is not None:
            raise self.update_error


def _service(  # noqa: ANN201
    *,
    sql: _SqlFake | None = None,
    admin: object | None = None,
    configured: bool = True,
    identity: _Identity | None = None,
    storage: InMemoryStorageProvider | None = None,
    sync_job_id: str | None = None,
    jobs: _JobsApi | None = None,
) -> DataAccessService:
    factory = _Factory(admin or _admin(), configured=configured)
    if jobs is not None:
        factory._admin.jobs = jobs  # type: ignore[attr-defined]
    return DataAccessService(
        storage=storage or InMemoryStorageProvider(),
        sql=sql or _SqlFake(),
        factory=factory,
        identity_attributes=identity,
        sync_job_id=sync_job_id,
    )


def _loaders(
    svc: DataAccessService,
    *,
    tags: list[TagRow] | tuple = (),
    grants: list[ActualGrant] | tuple = (),
    groups: set[str] | tuple = (),
    members: list[ActualMember] | tuple = (),
    assignments: list[AssignmentRow] | tuple = (),
) -> None:
    svc._tag_loader = lambda _settings: list(tags)
    svc._grant_loader = lambda _settings: list(grants)
    svc._group_loader = lambda _settings: set(groups)
    svc._member_loader = lambda _settings, _groups: list(members)
    svc._assignment_loader = lambda _settings: list(assignments)


def _stored_run(run_id: str, *, ago: timedelta = timedelta(0), mode: str = "dry_run") -> DataAccessRun:
    started = datetime.now(UTC) - ago
    return DataAccessRun(
        run_id=run_id,
        status=SyncRunStatus.SUCCESS,
        trigger=SyncTrigger.USER,
        mode=mode,
        started_at=started,
        finished_at=started,
        triggered_by="admin@example.com",
    )


def _check(readiness: DataAccessReadiness, check_id: str):  # noqa: ANN201
    return next(c for c in readiness.checks if c.id == check_id)


ADMIN = SimpleNamespace(user_name="admin@example.com")


def test_collapse_membership_keeps_first_of_duplicate_actions() -> None:
    add = MembershipChange(
        group="g", principal_name="a@x", principal_type="USER", action=MembershipAction.ADD
    )
    add2 = MembershipChange(
        group="g", principal_name="b@x", principal_type="USER", action=MembershipAction.ADD
    )
    rem = MembershipChange(
        group="g", principal_name="c@x", principal_type="USER", action=MembershipAction.REMOVE
    )
    rem2 = MembershipChange(
        group="g", principal_name="d@x", principal_type="USER", action=MembershipAction.REMOVE
    )
    collapsed = collapse_membership_by_principal_id(
        [("g1", "u1", add), ("g1", "u1", add2), ("g1", "u2", rem), ("g1", "u2", rem2)]
    )
    assert [change.principal_name for _, _, change in collapsed] == ["a@x", "c@x"]


def test_get_settings_defaults_and_enrolled_catalogs() -> None:
    svc = _service(sql=_SqlFake({"ORDER BY catalog_name": [{"catalog_name": "sandbox"}]}))
    view = svc.get_settings()
    assert view.settings == DataAccessSettings()
    assert view.enrolled_catalogs == ["sandbox"]


def test_invalid_stored_settings_fall_back_to_defaults() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({"data_access": {"intervalMinutes": "soon"}}, "tester")
    svc = _service(storage=storage)
    assert svc.get_settings().settings.interval_minutes == DEFAULT_SYNC_INTERVAL_MINUTES


def test_save_settings_persists_and_returns_view() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage, sql=_SqlFake({"ORDER BY catalog_name": [{"catalog_name": "sandbox"}]}))
    view = svc.save_settings(DataAccessSettings(interval_minutes=30), ADMIN)
    assert view.settings.interval_minutes == 30
    assert view.enrolled_catalogs == ["sandbox"]
    assert svc.get_settings().settings.interval_minutes == 30


def test_save_settings_keeps_tuning_fields_the_request_omits() -> None:
    """A partial save changes only the fields it sends; tuned concurrency and warehouse values stay."""
    svc = _service(sql=_SqlFake({"ORDER BY catalog_name": []}))
    svc.save_settings(
        DataAccessSettings(
            grant_concurrency=16,
            membership_concurrency=4,
            warehouse_autoscale=True,
            warehouse_max_clusters=6,
            warehouse_baseline_clusters=3,
        ),
        ADMIN,
    )
    svc.save_settings(DataAccessSettings(interval_minutes=30), ADMIN)
    stored = svc.get_settings().settings
    assert stored.interval_minutes == 30
    assert stored.grant_concurrency == 16
    assert stored.membership_concurrency == 4
    assert stored.warehouse_autoscale is True
    assert stored.warehouse_max_clusters == 6
    assert stored.warehouse_baseline_clusters == 3


def test_put_settings_without_tuning_fields_keeps_them(client: TestClient) -> None:
    """An API client that does not send the tuning fields must not reset them."""
    admin = headers_for("admin@example.com")
    current = client.get("/api/data-access/settings", headers=admin).json()["settings"]
    tuned = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={**current, "grantConcurrency": 16, "warehouseAutoscale": True, "warehouseMaxClusters": 6},
    )
    assert tuned.status_code == 200

    partial = client.put(
        "/api/data-access/settings",
        headers=admin,
        json={"intervalMinutes": 20, "enrollmentCatalogTagKey": "data_access_controlled", "enrollmentCatalogTagValue": "true"},
    )
    assert partial.status_code == 200
    saved = partial.json()["settings"]
    assert saved["intervalMinutes"] == 20
    assert saved["grantConcurrency"] == 16
    assert saved["warehouseAutoscale"] is True
    assert saved["warehouseMaxClusters"] == 6


def test_list_enrolled_catalogs_filters_system_and_malformed_rows() -> None:
    sql = _SqlFake(
        {
            "ORDER BY catalog_name": [
                {"catalog_name": "zeta"},
                {"catalog_name": "System"},
                {"catalog_name": None},
                {"catalog_name": "alpha"},
                {},
            ]
        }
    )
    assert _service(sql=sql).list_enrolled_catalogs() == ["alpha", "zeta"]


def test_list_enrolled_catalogs_empty_when_warehouse_unavailable() -> None:
    assert _service(sql=_SqlFake(configured=False)).list_enrolled_catalogs() == []
    assert (
        _service(sql=_SqlFake(query_error=NotConfiguredError("no warehouse"))).list_enrolled_catalogs() == []
    )
    assert _service(sql=_SqlFake(query_error=DatabricksError("denied"))).list_enrolled_catalogs() == []


def test_readiness_reports_every_check_as_skipped_without_credentials_or_warehouse() -> None:
    svc = _service(sql=_SqlFake(configured=False), configured=False)
    readiness = svc.readiness()
    assert readiness.dry_run_ready is False
    assert readiness.apply_ready is False
    assert readiness.setup_checks == []
    assert readiness.setup_ready is False
    assert _check(readiness, "credentials").ok is False
    assert _check(readiness, "authenticate").detail == "Skipped; credentials are missing"
    assert _check(readiness, "warehouse").detail == "DATABRICKS_WAREHOUSE_ID is not set"
    assert _check(readiness, "read_tags").detail == "Skipped; SQL warehouse is not configured"
    assert _check(readiness, "list_groups").detail == "Skipped; credentials are missing"
    assert _check(readiness, "create_groups").ok is False
    assert _check(readiness, "grants").detail == "Skipped; SQL warehouse is not configured"


def test_readiness_healthy_with_identity_definitions() -> None:
    identity = _Identity(
        sensitivity=SimpleNamespace(key="sensitivity"),
        data_access=SimpleNamespace(key="data_access"),
    )
    svc = _service(
        sql=_SqlFake(
            {"catalog_tags": [{"catalog_name": "sandbox"}], "catalog_privileges": [{"grantee": "x"}]}
        ),
        admin=_admin(me=SimpleNamespace(user_name=None, display_name="SP Display")),
        identity=identity,
    )
    readiness = svc.readiness()
    assert readiness.dry_run_ready is True
    assert readiness.apply_ready is True
    assert readiness.setup_ready is True
    assert _check(readiness, "authenticate").detail == "SP Display"
    assert _check(readiness, "read_tags").ok is True
    assert _check(readiness, "grants").ok is True
    assert _check(readiness, "create_groups").ok is True
    details = {c.id: c.detail for c in readiness.setup_checks}
    assert details["sensitivity_definition"] == "Configured as 'sensitivity'"
    assert details["data_access_definition"] == "Configured as 'data_access' → tag 'data_access_control'"


def test_readiness_setup_checks_report_missing_definitions() -> None:
    svc = _service(identity=_Identity())
    readiness = svc.readiness()
    assert readiness.setup_ready is False
    details = {c.id: c.detail for c in readiness.setup_checks}
    assert details["sensitivity_definition"] == "Create Identity definition with key 'sensitivity'"
    assert details["data_access_definition"] == (
        "Create Identity definition linked to governed tag 'data_access_control'"
    )


def test_readiness_maps_sql_and_auth_errors_to_failed_checks() -> None:
    warehouse_down = _service(sql=_SqlFake(query_error=NotConfiguredError("no warehouse")))
    assert _check(warehouse_down.readiness(), "read_tags").detail == "no warehouse"

    generic_failure = _service(
        sql=_SqlFake(query_error=RuntimeError("timeout")),
        admin=_admin(me_error=DatabricksError("bad creds"), groups_error=RuntimeError("nope")),
    )
    readiness = generic_failure.readiness()
    assert _check(readiness, "read_tags").detail == "timeout"
    assert _check(readiness, "grants").ok is False
    assert _check(readiness, "authenticate").ok is False
    assert _check(readiness, "list_groups").ok is False
    assert readiness.dry_run_ready is False


def test_list_runs_filters_mode_and_clamps_paging() -> None:
    svc = _service()
    svc._write_run(_stored_run("older-dry", ago=timedelta(minutes=2)))  # noqa: SLF001
    svc._write_run(_stored_run("apply", ago=timedelta(minutes=1), mode="apply"))  # noqa: SLF001
    svc._write_run(_stored_run("newest-dry"))  # noqa: SLF001

    dry = svc.list_runs(mode="DRY_RUN")
    assert dry.total == 2
    assert dry.runs[0].run_id == "newest-dry"
    assert dry.runs[0].plan is None
    assert dry.mode == "dry_run"

    tiny = svc.list_runs(page=0, page_size=0)
    assert (tiny.page, tiny.page_size, len(tiny.runs)) == (1, 1, 1)
    assert svc.list_runs(page_size=999).page_size == 50
    assert svc.list_runs(mode="   ").mode is None

    with pytest.raises(InvalidRequestError, match="dry_run or apply"):
        svc.list_runs(mode="bogus")


def test_old_runs_are_hidden_and_not_found() -> None:
    svc = _service()
    svc._write_run(_stored_run("expired", ago=timedelta(days=45)))  # noqa: SLF001
    svc._write_run(_stored_run("fresh"))  # noqa: SLF001
    assert svc.list_runs().total == 1
    with pytest.raises(NotFoundError):
        svc.get_run("expired")
    assert svc.get_run("fresh").run_id == "fresh"
    assert svc.latest_run(mode="apply") is None
    assert svc.latest_run().run_id == "fresh"


def test_write_run_caps_history_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.services.data_access.RUN_HISTORY_LIMIT", 2)
    svc = _service()
    for index, ago in enumerate([timedelta(minutes=2), timedelta(minutes=1), timedelta(0)]):
        svc._write_run(_stored_run(f"r{index}", ago=ago))  # noqa: SLF001
    assert [r.run_id for r in svc._runs()] == ["r2", "r1"]  # noqa: SLF001


def test_corrupt_run_history_is_ignored() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    storage.settings.put_many({"data_access_runs": "not-a-list"}, "tester")
    assert svc.list_runs().total == 0
    good = _stored_run("good").model_dump(mode="json", exclude={"duration_seconds"})
    storage.settings.put_many({"data_access_runs": [{"bogus": True}, good]}, "tester")
    assert [r.run_id for r in svc._runs()] == ["good"]  # noqa: SLF001


def test_acquire_lock_respects_fresh_lock_and_takes_stale_or_malformed_ones() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    now = datetime.now(UTC)
    storage.settings.put_many({"data_access_lock": {"run_id": "other", "started_at": now.isoformat()}}, "t")
    assert svc._acquire_lock("mine", now) is False  # noqa: SLF001

    storage.settings.put_many(
        {"data_access_lock": {"run_id": "other", "started_at": "2020-01-01T00:00:00Z"}}, "t"
    )
    assert svc._acquire_lock("mine", now) is True  # noqa: SLF001

    storage.settings.put_many({"data_access_lock": {"run_id": "other", "started_at": "not-a-date"}}, "t")
    assert svc._acquire_lock("second", now) is True  # noqa: SLF001

    storage.settings.put_many({"data_access_lock": "garbage"}, "t")
    assert svc._acquire_lock("third", now) is True  # noqa: SLF001


def test_release_lock_only_clears_its_own_run() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    storage.settings.put_many({"data_access_lock": {"run_id": "mine", "started_at": "x"}}, "t")
    svc._release_lock("other")  # noqa: SLF001
    assert storage.settings.get_all()["data_access_lock"]["run_id"] == "mine"
    svc._release_lock("mine")  # noqa: SLF001
    assert storage.settings.get_all()["data_access_lock"] == {}


def test_list_runs_closes_orphan_running_history() -> None:
    """A cancelled Job leaves status=running with no lock; list_runs marks it failed."""
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    started = datetime.now(UTC) - timedelta(minutes=5)
    orphan = DataAccessRun(
        run_id="cancelled",
        status=SyncRunStatus.RUNNING,
        trigger=SyncTrigger.SCHEDULE,
        mode="dry_run",
        started_at=started,
        triggered_by="scheduler",
    )
    svc._write_run(orphan)  # noqa: SLF001
    storage.settings.put_many({"data_access_lock": {}}, "t")

    listed = svc.list_runs()
    assert listed.total == 1
    assert listed.runs[0].status == SyncRunStatus.FAILED
    assert listed.runs[0].finished_at is not None
    assert "Cancelled or interrupted" in (listed.runs[0].error or "")


def test_run_sync_rejects_unknown_mode() -> None:
    with pytest.raises(InvalidRequestError, match="dry_run or apply"):
        _service().run_sync(actor="admin@example.com", trigger=SyncTrigger.USER, mode="preview")


def test_run_sync_dry_run_persists_runs_and_reports_no_drift() -> None:
    svc = _service()
    _loaders(svc, tags=_reviewed("table", "sandbox", "finance", "budget", "finance.budget"))
    first = svc.run_now(ADMIN)
    assert first.status == SyncRunStatus.SUCCESS
    assert first.triggered_by == "admin@example.com"
    assert first.summary is not None
    assert first.summary.eligible_objects == 1

    second = svc.run_sync(actor="scheduler", trigger=SyncTrigger.SCHEDULE)
    assert second.plan is not None
    assert second.plan.drift_since_last_run == ["Desired state matches the last successful dry-run"]
    assert svc.list_runs().total == 2
    assert svc.latest_run().run_id == second.run_id
    assert svc.get_run(first.run_id).plan is not None


def test_run_sync_apply_is_blocked_until_readiness_passes() -> None:
    svc = _service()
    ready = DataAccessReadiness(
        dry_run_ready=True, apply_ready=False, checks=[], setup_checks=[], setup_ready=True
    )
    svc.readiness = lambda: ready  # type: ignore[method-assign]
    with pytest.raises(InvalidRequestError, match="Apply is not ready"):
        svc.run_sync(actor="admin@example.com", trigger=SyncTrigger.USER, mode="apply")

    svc.readiness = lambda: ready.model_copy(update={"apply_ready": True, "setup_ready": False})  # type: ignore[method-assign]
    with pytest.raises(InvalidRequestError, match="sensitivity and data-access"):
        svc.run_sync(actor="admin@example.com", trigger=SyncTrigger.USER, mode="apply")


def test_run_sync_refuses_while_another_run_holds_the_lock() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    storage.settings.put_many(
        {"data_access_lock": {"run_id": "other", "started_at": datetime.now(UTC).isoformat()}}, "tester"
    )
    with pytest.raises(InvalidRequestError, match="already running"):
        svc.run_sync(actor="admin@example.com", trigger=SyncTrigger.USER)


def test_run_sync_records_failed_run_and_releases_lock() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    _loaders(svc)

    def _boom(_settings):  # noqa: ANN001, ANN202
        raise RuntimeError("warehouse down")

    svc._tag_loader = _boom  # type: ignore[method-assign]  # noqa: SLF001
    run = svc.run_sync(actor="admin@example.com", trigger=SyncTrigger.USER)
    assert run.status == SyncRunStatus.FAILED
    assert run.error == "warehouse down"
    assert svc.latest_run().status == SyncRunStatus.FAILED
    assert storage.settings.get_all()["data_access_lock"] == {}


def test_run_sync_apply_surfaces_step_errors_on_successful_run() -> None:
    svc = _service()
    _loaders(
        svc,
        tags=_reviewed("table", "sandbox", "finance", "budget", "finance.budget"),
        assignments=[AssignmentRow("ana@example.com", "USER", "finance.budget")],
    )
    svc.readiness = lambda: DataAccessReadiness(  # type: ignore[method-assign]
        dry_run_ready=True, apply_ready=True, checks=[], setup_checks=[], setup_ready=True
    )
    svc._apply_plan = lambda _settings, plan: plan.model_copy(  # type: ignore[method-assign]
        update={"apply_errors": ["GRANT SELECT on x: boom"]}
    )
    run = svc.run_sync(actor="admin@example.com", trigger=SyncTrigger.USER, mode="apply")
    assert run.status == SyncRunStatus.SUCCESS
    assert run.error == "GRANT SELECT on x: boom"
    assert run.plan is not None
    assert run.plan.apply_errors == ["GRANT SELECT on x: boom"]


def test_sync_job_schedule_updates_cron_and_pause() -> None:
    from databricks.sdk.service.jobs import PauseStatus

    from backend.app.services.data_access_schedule import quartz_cron_for_interval_minutes

    jobs = _JobsApi()
    svc = _service(sync_job_id="42", jobs=jobs)
    svc.sync_job_schedule(DataAccessSettings(interval_minutes=15, schedule_enabled=True))
    assert jobs.update_calls[0]["job_id"] == 42
    schedule = jobs.update_calls[0]["new_settings"].schedule
    assert schedule.quartz_cron_expression == quartz_cron_for_interval_minutes(15)
    assert schedule.pause_status == PauseStatus.UNPAUSED

    svc.sync_job_schedule(DataAccessSettings(interval_minutes=7, schedule_enabled=False))
    schedule = jobs.update_calls[1]["new_settings"].schedule
    assert schedule.quartz_cron_expression == "0 */6 * * * ?"  # 7 snaps down to 6
    assert schedule.pause_status == PauseStatus.PAUSED


def test_sync_job_schedule_is_noop_without_job_id() -> None:
    jobs = _JobsApi()
    _service(jobs=jobs).sync_job_schedule(DataAccessSettings())
    assert jobs.update_calls == []


def test_sync_job_schedule_maps_update_failures() -> None:
    from backend.app.core.errors import UpstreamError

    jobs = _JobsApi(update_error=RuntimeError("denied"))
    with pytest.raises(UpstreamError, match="Could not update data-access sync Job schedule"):
        _service(sync_job_id="9", jobs=jobs).sync_job_schedule(DataAccessSettings())


def test_save_settings_pushes_job_schedule() -> None:
    jobs = _JobsApi()
    svc = _service(sync_job_id="7", jobs=jobs)
    svc.save_settings(DataAccessSettings(interval_minutes=30, schedule_enabled=False), ADMIN)
    assert len(jobs.update_calls) == 1
    assert jobs.update_calls[0]["new_settings"].schedule.pause_status.value == "PAUSED"


def test_trigger_job_and_wait_returns_run_written_by_job() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage, sync_job_id="99")
    _loaders(svc)

    def _on_run(job_id: int, **kwargs):  # noqa: ANN003, ANN202
        params = kwargs["job_parameters"]
        assert job_id == 99
        assert params["mode"] == "dry_run"
        assert params["trigger"] == "user"
        assert params["actor"] == "admin@example.com"
        return svc.run_sync(
            actor=params["actor"],
            trigger=SyncTrigger.USER,
            mode=params["mode"],
            run_id=params["run_id"],
        )

    jobs = _JobsApi(on_run=_on_run)
    svc = _service(storage=storage, sync_job_id="99", jobs=jobs)
    _loaders(svc)
    finished = svc.run_now(ADMIN, mode="dry_run")
    assert finished.status == SyncRunStatus.SUCCESS
    assert finished.triggered_by == "admin@example.com"
    assert jobs.run_calls[0]["job_parameters"]["run_id"] == finished.run_id


def test_trigger_job_and_wait_surfaces_job_failure_when_run_missing() -> None:
    from backend.app.core.errors import UpstreamError

    jobs = _JobsApi(run_error=RuntimeError("job exploded"))
    with pytest.raises(UpstreamError, match="Data-access sync Job failed"):
        _service(sync_job_id="1", jobs=jobs).trigger_job_and_wait(actor=ADMIN, mode="dry_run")


def test_run_sync_accepts_predetermined_run_id() -> None:
    svc = _service()
    _loaders(svc, tags=_reviewed("table", "sandbox", "finance", "budget", "finance.budget"))
    run = svc.run_sync(
        actor="scheduler",
        trigger=SyncTrigger.SCHEDULE,
        mode="dry_run",
        run_id="fixed-run-id",
    )
    assert run.run_id == "fixed-run-id"
    assert svc.get_run("fixed-run-id").run_id == "fixed-run-id"


def test_load_tags_builds_catalog_schema_and_table_rows() -> None:
    sql = _SqlFake(
        {
            "ORDER BY catalog_name": [{"catalog_name": "sandbox"}],
            "system.information_schema.catalog_tags": [
                {"catalog_name": "sandbox", "tag_name": "data_access_control", "tag_value": "finance"}
            ],
            "system.information_schema.schema_tags": [
                {
                    "catalog_name": "sandbox",
                    "schema_name": "finance",
                    "tag_name": "clr_review_status",
                    "tag_value": None,
                }
            ],
            "system.information_schema.table_tags": [
                {
                    "catalog_name": "sandbox",
                    "schema_name": "finance",
                    "table_name": "budget",
                    "tag_name": "data_access_control",
                    "tag_value": "finance",
                }
            ],
        }
    )
    svc = _service(sql=sql)
    tags = svc._load_tags(DataAccessSettings())  # noqa: SLF001
    assert tags == [
        TagRow("catalog", "sandbox", None, None, "data_access_control", "finance"),
        TagRow("schema", "sandbox", "finance", None, "clr_review_status", ""),
        TagRow("table", "sandbox", "finance", "budget", "data_access_control", "finance"),
    ]
    assert any("IN ('sandbox')" in statement for statement in sql.statements)


def test_load_tags_public_delegates_to_tag_loader() -> None:
    svc = _service()
    _loaders(svc, tags=[TagRow("catalog", "sandbox", None, None, "data_access_control", "finance")])
    assert svc.load_tags_public()[0].catalog == "sandbox"


def test_load_grants_reads_each_securable_and_skips_inherited() -> None:
    sql = _SqlFake(
        {
            "ORDER BY catalog_name": [{"catalog_name": "sandbox"}],
            "catalog_privileges": [
                {
                    "catalog_name": "sandbox",
                    "grantee": "dac_finance",
                    "privilege_type": "USE_CATALOG",
                    "inherited_from": "NONE",
                },
                {
                    "catalog_name": "sandbox",
                    "grantee": "dac_x",
                    "privilege_type": "USE_CATALOG",
                    "inherited_from": "CATALOG",
                },
            ],
            "schema_privileges": [
                {
                    "catalog_name": "sandbox",
                    "schema_name": "finance",
                    "grantee": "dac_finance",
                    "privilege_type": "USE_SCHEMA",
                    "inherited_from": None,
                },
            ],
            "table_privileges": [
                {
                    "table_catalog": "sandbox",
                    "table_schema": "finance",
                    "table_name": "budget",
                    "grantee": "dac_finance_budget",
                    "privilege_type": "SELECT",
                    "inherited_from": "NONE",
                },
            ],
        }
    )
    svc = _service(sql=sql)
    grants = svc._load_grants(DataAccessSettings(group_prefix="dac"))  # noqa: SLF001
    assert grants == [
        ActualGrant("catalog", "sandbox", "dac_finance", "USE_CATALOG"),
        ActualGrant("schema", "sandbox.finance", "dac_finance", "USE_SCHEMA"),
        ActualGrant("table", "sandbox.finance.budget", "dac_finance_budget", "SELECT"),
    ]


def test_scim_list_groups_passes_filter_and_accepts_only_resource_lists() -> None:
    api = _ScimApi(lambda *_args: {"Resources": [{"displayName": "dac_a"}]})
    svc = _service(admin=_admin(api))
    assert svc._account_scim_list_groups(filter_expr='displayName sw "dac"') == [  # noqa: SLF001
        {"displayName": "dac_a"}
    ]
    assert api.calls[-1][2]["filter"] == 'displayName sw "dac"'
    api.handler = lambda *_args: ["not", "a", "dict"]
    assert svc._account_scim_list_groups() == []  # noqa: SLF001


def test_scim_user_lookup_returns_none_on_error_or_bad_payload() -> None:
    api = _ScimApi(_raise_on_call(DatabricksError("gone")))
    svc = _service(admin=_admin(api))
    assert svc._account_scim_get_user("u1") is None  # noqa: SLF001
    api.handler = lambda *_args: {"userName": "a@example.com"}
    assert svc._account_scim_get_user("u1") == {"userName": "a@example.com"}  # noqa: SLF001
    api.handler = lambda *_args: ["bad"]
    assert svc._account_scim_get_user("u1") is None  # noqa: SLF001


def test_scim_create_and_patch_group_shape_requests() -> None:
    api = _ScimApi(lambda *_args: {"id": 123})
    svc = _service(admin=_admin(api))
    assert svc._account_scim_create_group("dac_new") == "123"  # noqa: SLF001
    assert api.calls[-1][0] == "POST"

    api.handler = lambda *_args: {}
    with pytest.raises(RuntimeError, match="missing id"):
        svc._account_scim_create_group("dac_new")  # noqa: SLF001

    svc._account_scim_patch_group("g1", [{"op": "remove"}])  # noqa: SLF001
    method, path, _query, body = api.calls[-1]
    assert method == "PATCH"
    assert path.endswith("/Groups/g1")
    assert body["Operations"] == [{"op": "remove"}]


def test_load_groups_returns_names_and_degrades_on_error() -> None:
    api = _ScimApi(lambda *_args: {"Resources": [{"displayName": "dac_a"}, {"id": "no-name"}]})
    svc = _service(admin=_admin(api))
    assert svc._load_groups(DataAccessSettings()) == {"dac_a"}  # noqa: SLF001
    api.handler = _raise_on_call(DatabricksError("denied"))
    assert svc._load_groups(DataAccessSettings()) == set()  # noqa: SLF001


def test_load_assignments_reads_active_data_access_rows() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(identity=_Identity(data_access=SimpleNamespace(key="data_access")), storage=storage)
    seen: list[Any] = []

    class _IdentityRepo:
        def list_assignments(self, flt):  # noqa: ANN001, ANN201
            seen.append(flt)
            return [
                SimpleNamespace(
                    principal_name="ana@example.com",
                    principal_type=SimpleNamespace(value="USER"),
                    attribute_value="finance.budget",
                )
            ]

    svc._storage = SimpleNamespace(settings=storage.settings, identity_attributes=_IdentityRepo())  # type: ignore[assignment]  # noqa: SLF001
    rows = svc._load_assignments(DataAccessSettings())  # noqa: SLF001
    assert rows == [AssignmentRow("ana@example.com", "USER", "finance.budget")]
    assert seen[0].attribute_key == "data_access"


def test_load_assignments_empty_without_identity_or_definition() -> None:
    assert _service()._load_assignments(DataAccessSettings()) == []  # noqa: SLF001
    assert _service(identity=_Identity())._load_assignments(DataAccessSettings()) == []  # noqa: SLF001


def test_load_members_resolves_user_names_and_groups() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del method, query, body
        if path.endswith("/Users/u1"):
            return {"userName": "david@example.com"}
        if path.endswith("/Users/u2"):
            raise DatabricksError("gone")
        return {
            "Resources": [
                {
                    "displayName": "dac_finance_budget",
                    "members": [
                        {"value": "u1", "display": "David Galluzzo", "type": "USER"},
                        {"value": "u1", "display": "David Galluzzo", "type": "USER"},
                        {"value": "u2", "display": "Gone Person", "type": "USER"},
                        {"value": "", "display": "Plain Name", "type": "UNKNOWN"},
                        {"value": "g9", "display": "dac_finance", "type": "UNKNOWN"},
                        {"value": "g10", "display": "finance-owners", "type": "GROUP"},
                        {"value": None, "display": None},
                        "not-a-dict",
                    ],
                },
                {"displayName": "other_group", "members": [{"value": "u3", "display": "X", "type": "USER"}]},
            ]
        }

    svc = _service(admin=_admin(_ScimApi(_handler)))
    members = svc._load_members(DataAccessSettings(), {"dac_finance_budget"})  # noqa: SLF001
    assert [(m.principal_name, m.principal_type) for m in members] == [
        ("david@example.com", "USER"),
        ("david@example.com", "USER"),
        ("Gone Person", "USER"),
        ("Plain Name", "USER"),
        ("dac_finance", "GROUP"),
        ("finance-owners", "GROUP"),
    ]
    assert {m.group_name for m in members} == {"dac_finance_budget"}


def test_load_members_handles_prefix_without_underscore_and_listing_errors() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del method, path, query, body
        return {
            "Resources": [
                {
                    "displayName": "dac_team",
                    "members": [{"value": "g1", "display": "dac_sub", "type": "UNKNOWN"}],
                }
            ]
        }

    svc = _service(admin=_admin(_ScimApi(_handler)))
    members = svc._load_members(DataAccessSettings(group_prefix="dac"), {"dac_team"})  # noqa: SLF001
    assert [(m.principal_name, m.principal_type) for m in members] == [("dac_sub", "GROUP")]

    failing = _service(admin=_admin(_ScimApi(_raise_on_call(DatabricksError("denied")))))
    assert failing._load_members(DataAccessSettings(), {"dac_team"}) == []  # noqa: SLF001

    unconfigured = _service(configured=False)
    assert unconfigured._load_members(DataAccessSettings(), {"dac_team"}) == []  # noqa: SLF001
    assert svc._load_members(DataAccessSettings(), set()) == []  # noqa: SLF001


def test_ensure_groups_creates_missing_and_recovers_races() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del path
        if method == "GET":
            flt = (query or {}).get("filter")
            if flt is None:
                raise DatabricksError("list denied")
            if "dac_race" in flt:
                return {"Resources": [{"displayName": "dac_race", "id": "g-race"}]}
            if "dac_broke" in flt:
                raise DatabricksError("lookup broke")
            return {"Resources": []}
        if body["displayName"] == "dac_ok":
            return {"id": "g-ok"}
        raise RuntimeError(f"409 {body['displayName']}")

    svc = _service(admin=_admin(_ScimApi(_handler)))
    errors: list[str] = []
    groups = [
        PlannedGroup(name="dac_ok", value="ok"),
        PlannedGroup(name="dac_race", value="race"),
        PlannedGroup(name="dac_lost", value="lost"),
        PlannedGroup(name="dac_broke", value="broke"),
    ]
    ids = svc._ensure_groups(groups, errors)  # noqa: SLF001
    assert ids["dac_ok"] == "g-ok"
    assert ids["dac_race"] == "g-race"
    assert "dac_lost" not in ids
    assert "dac_broke" not in ids
    assert errors[0] == "list account groups: list denied"
    assert any(e.startswith("create group dac_lost") for e in errors)
    assert any(e.startswith("create group dac_broke") for e in errors)


def test_nest_groups_skips_existing_links_and_reports_failures() -> None:
    patches: list[str] = []

    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del method, query, body
        patches.append(path)
        if path.endswith("/g-already"):
            raise DatabricksError("already a member")
        if path.endswith("/g-denied"):
            raise DatabricksError("permission denied")
        return {}

    svc = _service(admin=_admin(_ScimApi(_handler)))
    groups = [
        PlannedGroup(name="dac_already", value="a", parent_group="dac_parent"),
        # Inventory already shows this nest; apply must not PATCH Account SCIM for it.
        PlannedGroup(name="dac_present", value="p", parent_group="dac_parent", nest_exists=True),
        PlannedGroup(name="dac_denied", value="d", parent_group="dac_parent"),
        PlannedGroup(name="dac_gone", value="g", parent_group="dac_parent"),
        PlannedGroup(name="dac_root", value="r"),
    ]
    errors: list[str] = []
    svc._nest_groups(  # noqa: SLF001
        groups,
        {
            "dac_already": "g-already",
            "dac_present": "g-present",
            "dac_denied": "g-denied",
            "dac_parent": "g-parent",
        },
        errors,
    )
    assert len(errors) == 2
    assert any("dac_denied" in e for e in errors)
    assert any("missing group id" in e for e in errors)
    assert not any(path.endswith("/g-present") for path in patches)


def test_apply_membership_handles_duplicates_removals_and_resolution_failures() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del query
        if method == "GET":
            # Account SCIM user lookups return nothing here, so the workspace fallback resolves users.
            return {"Resources": []}
        op = body["Operations"][0]
        if op["op"] == "remove":
            raise DatabricksError("forbidden")
        if op["value"][0]["value"] == "u-bob":
            raise DatabricksError("already a member")
        return {}

    users = [
        SimpleNamespace(id="u-ana", user_name="ana@example.com"),
        SimpleNamespace(id="u-bob", user_name="bob@example.com"),
        SimpleNamespace(id="u-cy", user_name="cy@example.com"),
    ]
    svc = _service(admin=_admin(_ScimApi(_handler), users=users))
    errors: list[str] = []
    svc._apply_membership(  # noqa: SLF001
        [
            MembershipChange(
                group="dac_x",
                principal_name="ana@example.com",
                principal_type="USER",
                action=MembershipAction.ADD,
            ),
            # ADD already present on the group is treated as success; the duplicate REMOVE is collapsed away.
            MembershipChange(
                group="dac_x",
                principal_name="bob@example.com",
                principal_type="USER",
                action=MembershipAction.ADD,
            ),
            MembershipChange(
                group="dac_x",
                principal_name="bob@example.com",
                principal_type="USER",
                action=MembershipAction.REMOVE,
            ),
            MembershipChange(
                group="dac_x",
                principal_name="cy@example.com",
                principal_type="USER",
                action=MembershipAction.REMOVE,
            ),
            MembershipChange(
                group="dac_x",
                principal_name="nobody@example.com",
                principal_type="USER",
                action=MembershipAction.ADD,
            ),
        ],
        {"dac_x": "g-x"},
        errors,
    )
    assert len(errors) == 2
    assert any("remove cy@example.com on dac_x" in e for e in errors)
    assert any("resolve principal nobody@example.com: not found" in e for e in errors)


def test_resolve_principal_covers_each_principal_type() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del method, path, body
        flt = (query or {}).get("filter", "")
        if "dac_team" in flt:
            return {"Resources": [{"displayName": "dac_team", "id": "g-team"}]}
        return {"Resources": []}

    users = [
        SimpleNamespace(id=None, user_name="nil@example.com"),
        SimpleNamespace(id="u-1", user_name="ok@example.com"),
    ]
    svc = _service(
        admin=_admin(_ScimApi(_handler), users=users, service_principals=[SimpleNamespace(id="sp-1")]),
    )
    errors: list[str] = []
    assert svc._resolve_principal_id("dac_team", "GROUP", errors) == "g-team"  # noqa: SLF001
    assert svc._resolve_principal_id("missing", "GROUP", errors) is None  # noqa: SLF001
    assert svc._resolve_principal_id("app-id", "SERVICE_PRINCIPAL", errors) == "sp-1"  # noqa: SLF001
    assert svc._resolve_principal_id("ok@example.com", "USER", errors) == "u-1"  # noqa: SLF001
    assert svc._resolve_principal_id("nil@example.com", "USER", errors) is None  # noqa: SLF001
    assert errors == [
        "resolve principal missing: account group not found",
        "resolve principal nil@example.com: not found",
    ]

    empty_sp = _service(admin=_admin())
    sp_errors: list[str] = []
    assert empty_sp._resolve_principal_id("app-id", "SERVICE_PRINCIPAL", sp_errors) is None  # noqa: SLF001
    assert sp_errors == []


def test_resolve_principal_records_lookup_exceptions() -> None:
    admin = _admin()

    def _explode(**_kwargs):  # noqa: ANN003, ANN202
        raise RuntimeError("svc down")

    admin.users = SimpleNamespace(list=_explode)
    errors: list[str] = []
    assert _service(admin=admin)._resolve_principal_id("x@example.com", "USER", errors) is None  # noqa: SLF001
    assert errors == ["resolve principal x@example.com: svc down"]


def test_apply_plan_creates_nests_members_and_grants() -> None:
    created: list[str] = []
    patches: list[tuple[str, list[dict[str, Any]]]] = []

    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del query
        if method == "GET" and path.endswith("/Users"):
            return {"Resources": []}
        if method == "GET":
            return {"Resources": [{"displayName": "dac_finance", "id": "g-parent"}]}
        if method == "POST":
            created.append(body["displayName"])
            return {"id": "g-child"}
        patches.append((path, body["Operations"]))
        return {}

    users = [SimpleNamespace(id="u-ana", user_name="ana@example.com")]
    sql = _SqlFake(execute_errors={"REVOKE": RuntimeError("boom")})
    svc = _service(sql=sql, admin=_admin(_ScimApi(_handler), users=users))
    plan = DataAccessPlan(
        summary=DataAccessPlanSummary(),
        fingerprint="test-fingerprint",
        groups=[
            PlannedGroup(name="dac_finance", value="finance"),
            PlannedGroup(name="dac_finance_budget", value="finance.budget", parent_group="dac_finance"),
            PlannedGroup(name="dac_orphan", value="orphan", parent_group="dac_missing"),
        ],
        grants_to_add=[
            PlannedGrant(
                securable_type="table",
                full_name="sandbox.finance.budget",
                principal="dac_finance_budget",
                privilege="SELECT",
            )
        ],
        grants_to_revoke=[
            PlannedGrant(
                securable_type="table",
                full_name="sandbox.finance.old",
                principal="dac_finance_budget",
                privilege="SELECT",
            )
        ],
        membership_changes=[
            MembershipChange(
                group="dac_finance_budget",
                principal_name="ana@example.com",
                principal_type="USER",
                action=MembershipAction.ADD,
            ),
            MembershipChange(
                group="dac_finance_budget",
                principal_name="stale@example.com",
                principal_type="USER",
                action=MembershipAction.REMOVE,
            ),
            MembershipChange(
                group="dac_unknown",
                principal_name="ana@example.com",
                principal_type="USER",
                action=MembershipAction.ADD,
            ),
        ],
    )

    result = svc._apply_plan_impl(DataAccessSettings(), plan)  # noqa: SLF001

    assert created == ["dac_finance_budget", "dac_orphan"]
    assert (
        "/api/2.0/account/scim/v2/Groups/g-child",
        [{"op": "add", "path": "members", "value": [{"value": "g-parent"}]}],
    ) in patches
    assert (
        "/api/2.0/account/scim/v2/Groups/g-child",
        [{"op": "add", "path": "members", "value": [{"value": "u-ana"}]}],
    ) in patches
    assert "GRANT SELECT ON TABLE `sandbox`.`finance`.`budget` TO `dac_finance_budget`" in sql.executed
    assert set(result.apply_errors) == {
        "nest dac_missing → dac_orphan: missing group id",
        "resolve principal stale@example.com: not found",
        "membership add ana@example.com: missing group",
        "REVOKE SELECT on sandbox.finance.old: boom",
    }


def test_apply_grants_collects_failures_per_statement() -> None:
    sql = _SqlFake(execute_errors={"GRANT": RuntimeError("no privilege")})
    svc = _service(sql=sql)
    errors: list[str] = []
    svc._apply_grants(  # noqa: SLF001
        [
            PlannedGrant(
                securable_type="catalog", full_name="sandbox", principal="dac_x", privilege="USE CATALOG"
            )
        ],
        [
            PlannedGrant(
                securable_type="catalog", full_name="sandbox", principal="dac_x", privilege="USE CATALOG"
            )
        ],
        errors,
    )
    assert errors == ["GRANT USE CATALOG on sandbox: no privilege"]
    assert sql.executed[-1] == "REVOKE USE CATALOG ON CATALOG `sandbox` FROM `dac_x`"


class _ConcurrentSql:
    """Thread-safe warehouse stand-in: tracks peak in-flight statements and fails ``bad`` tables."""

    def __init__(self, *, delay: float = 0.02, bad: tuple[str, ...] = ()) -> None:
        self.delay = delay
        self.bad = bad
        self.executed: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        del parameters
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.executed.append(statement)
        try:
            time.sleep(self.delay)
            if any(f"`{name}`" in statement for name in self.bad):
                raise RuntimeError("denied by warehouse")
        finally:
            with self._lock:
                self.in_flight -= 1


def _table_grants(count: int, principal: str = "dac_x") -> list[PlannedGrant]:
    return [
        PlannedGrant(
            securable_type="table",
            full_name=f"sandbox.perf.t{i:04d}",
            principal=principal,
            privilege="SELECT",
        )
        for i in range(count)
    ]


def test_apply_grants_runs_statements_concurrently_and_maps_failures() -> None:
    sql = _ConcurrentSql(bad=("t0003",))
    svc = _service(sql=sql)
    failures: dict[str, str] = {}
    errors: list[str] = []
    to_add = _table_grants(20)
    to_revoke = _table_grants(5)
    svc._apply_grants(to_add, to_revoke, errors, failures, concurrency=8)  # noqa: SLF001

    assert len(sql.executed) == 25
    assert sql.peak > 1  # really parallel, not one at a time
    assert sql.peak <= 8  # never above the configured concurrency
    # t0003 is in both lists, so its GRANT and its REVOKE each fail and are keyed separately.
    assert sorted(errors) == [
        "GRANT SELECT on sandbox.perf.t0003: denied by warehouse",
        "REVOKE SELECT on sandbox.perf.t0003: denied by warehouse",
    ]
    assert failures == {
        grant_key(GapKind.GRANT_ADD, to_add[3]): "denied by warehouse",
        grant_key(GapKind.GRANT_REVOKE, to_revoke[3]): "denied by warehouse",
    }


def test_apply_grants_concurrency_is_capped_by_the_number_of_statements() -> None:
    sql = _ConcurrentSql(delay=0.0)
    svc = _service(sql=sql)
    svc._apply_grants(_table_grants(2), [], [], {}, concurrency=32)  # noqa: SLF001
    assert len(sql.executed) == 2
    svc._apply_grants([], [], [], {}, concurrency=32)  # noqa: SLF001
    assert len(sql.executed) == 2


def test_apply_grants_records_call_timings_on_the_run_profile() -> None:
    sql = _ConcurrentSql(delay=0.0, bad=("t0001",))
    svc = _service(sql=sql)
    profiler = RunProfiler()
    with profiling(profiler):
        svc._apply_grants(_table_grants(3), _table_grants(1, "dac_y"), [], {}, concurrency=4)  # noqa: SLF001
    assert profiler.calls["sql.grant_table"] == 3
    assert profiler.calls["sql.revoke_table"] == 1
    assert profiler.sizes["grant_concurrency"] == 4


def test_account_scim_user_lookup_is_tried_before_the_workspace_list() -> None:
    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        del body
        if method == "GET" and path.endswith("/Users"):
            assert query["filter"] == 'userName eq "outside@example.com"'
            return {"Resources": [{"id": "acct-42", "userName": "outside@example.com"}]}
        return {"Resources": []}

    # The workspace list knows nobody, so only the account lookup can resolve this user.
    svc = _service(admin=_admin(_ScimApi(_handler), users=()))
    errors: list[str] = []
    assert svc._resolve_principal_id("outside@example.com", "USER", errors) == "acct-42"  # noqa: SLF001
    assert errors == []


def test_apply_membership_runs_patches_for_different_groups_concurrently() -> None:
    patches: list[str] = []
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def _handler(method, path, query, body):  # noqa: ANN001, ANN202
        nonlocal in_flight, peak
        del query, body
        if method == "GET":
            return {"Resources": []}
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            patches.append(path)
        try:
            time.sleep(0.05)
        finally:
            with lock:
                in_flight -= 1
        return {}

    users = [SimpleNamespace(id=f"u-{i}", user_name=f"u{i}@example.com") for i in range(6)]
    svc = _service(admin=_admin(_ScimApi(_handler), users=users))
    changes = [
        MembershipChange(
            group=f"dac_{i % 3}",
            principal_name=f"u{i}@example.com",
            principal_type="USER",
            action=MembershipAction.ADD,
        )
        for i in range(6)
    ]
    errors: list[str] = []
    svc._apply_membership(  # noqa: SLF001
        changes,
        {"dac_0": "g0", "dac_1": "g1", "dac_2": "g2"},
        errors,
        concurrency=4,
    )
    assert errors == []
    assert len(patches) == 6
    assert peak > 1  # different groups run at the same time
    assert peak <= 3  # at most one worker per group


def test_member_type_comes_from_ref_before_the_display_name_guess() -> None:
    # Real Account SCIM shape: no ``type``, ``$ref`` says Groups/ or Users/. An account group
    # such as "bc-oil-gas-analysts" must not be treated as a user (removals then fail).
    member = DataAccessService._member_principal_type  # noqa: SLF001
    group_ref = {"display": "bc-oil-gas-analysts", "value": "1", "$ref": "Groups/1"}
    user_ref = {"display": "David Galluzzo", "value": "2", "$ref": "Users/2"}
    sp_ref = {"display": "app-sp", "value": "3", "$ref": "ServicePrincipals/3"}
    assert member(group_ref, "bc-oil-gas-analysts", "dac") == "GROUP"
    assert member(user_ref, "David Galluzzo", "dac") == "USER"
    assert member(sp_ref, "app-sp", "dac") == "SERVICE_PRINCIPAL"
    assert member({"type": "GROUP", "display": "x"}, "x", "dac") == "GROUP"
    # No usable hint: fall back to the managed-prefix guess.
    assert member({"display": "dac_sub"}, "dac_sub", "dac") == "GROUP"
    assert member({"display": "Plain Name"}, "Plain Name", "dac") == "USER"


def test_account_scim_user_lookup_falls_back_when_the_account_api_fails() -> None:
    api = _ScimApi(_raise_on_call(DatabricksError("account api denied")))
    users = [SimpleNamespace(id="u-ws", user_name="ws@example.com")]
    svc = _service(admin=_admin(api, users=users))
    errors: list[str] = []
    assert svc._resolve_principal_id("ws@example.com", "USER", errors) == "u-ws"  # noqa: SLF001
    assert errors == []
    assert svc._account_scim_find_user("nobody@example.com") is None  # noqa: SLF001


def test_grant_sql_quotes_identifiers_and_strips_principal_backticks() -> None:
    grant = PlannedGrant(
        securable_type="table", full_name="sandbox.finance.budget", principal="dac`x", privilege="SELECT"
    )
    assert (
        DataAccessService._grant_sql("GRANT", grant)  # noqa: SLF001
        == "GRANT SELECT ON TABLE `sandbox`.`finance`.`budget` TO `dacx`"
    )
    assert (
        DataAccessService._grant_sql("REVOKE", grant)  # noqa: SLF001
        == "REVOKE SELECT ON TABLE `sandbox`.`finance`.`budget` FROM `dacx`"
    )


def test_catalog_sql_fails_closed_without_enrolled_catalogs() -> None:
    svc = _service()
    settings = DataAccessSettings()
    closed = svc._catalog_sql(settings, "catalog_name", [])  # noqa: SLF001
    assert closed.endswith("AND 1 = 0")
    assert svc._catalog_sql(settings, "catalog_name", None).endswith("AND 1 = 0")  # noqa: SLF001
    open_clause = svc._catalog_sql(settings, "catalog_name", ["Finance"])  # noqa: SLF001
    assert "lower(catalog_name) IN ('finance')" in open_clause
    assert "lower(catalog_name) <> 'system'" in open_clause


def test_startup_reapplies_stored_schedule_to_job() -> None:
    storage = InMemoryStorageProvider()
    jobs = _JobsApi()
    svc = _service(storage=storage, sync_job_id="5", jobs=jobs)
    svc.save_settings(DataAccessSettings(interval_minutes=60, schedule_enabled=True), ADMIN)
    jobs.update_calls.clear()
    svc.sync_job_schedule_from_storage()
    assert len(jobs.update_calls) == 1
    assert jobs.update_calls[0]["new_settings"].schedule.quartz_cron_expression == "0 0 * * * ?"


def test_startup_schedule_sync_is_noop_without_job_or_on_failure() -> None:
    jobs = _JobsApi()
    _service(jobs=jobs).sync_job_schedule_from_storage()
    assert jobs.update_calls == []

    failing = _JobsApi(update_error=RuntimeError("denied"))
    # Startup must not raise even when the Job update fails.
    _service(sync_job_id="5", jobs=failing).sync_job_schedule_from_storage()

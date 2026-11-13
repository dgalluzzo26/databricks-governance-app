"""Outstanding work, tag change detection, immediate membership, and access status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import DatabricksError
from fastapi.testclient import TestClient

from backend.app.core.errors import InvalidRequestError
from backend.app.models.data_access import (
    DataAccessPlan,
    DataAccessPlanSummary,
    DataAccessReadiness,
    DataAccessSettings,
    MembershipAction,
    MembershipChange,
    PlannedGrant,
    PlannedGroup,
    SyncTrigger,
)
from backend.app.models.data_access_pending import (
    GapItem,
    GapKind,
    GapMeta,
    GapStatus,
    MembershipEvent,
    MembershipEventStatus,
    MembershipState,
    StructureState,
    TagChange,
    TagChangeKind,
    TagSnapshotRow,
)
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    IdentityAttributeAssignment,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.repositories.data_access_state import InMemoryDataAccessStateRepository
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services.data_access import DataAccessService
from backend.app.services.data_access_gap import (
    build_gap,
    diff_snapshots,
    grant_key,
    group_key,
    member_key,
    missing_nesting,
    nest_key,
    snapshot_from_tags,
)
from backend.app.services.data_access_planner import (
    ActualMember,
    AssignmentRow,
    TagRow,
    plan_data_access,
)
from backend.app.services.data_access_status import compute_access_status
from backend.app.services.identity_attributes import ASSIGNMENT_ASSIGNED, ASSIGNMENT_REVOKED
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import FakeGovernedTags

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _reviewed(catalog: str, schema: str, table: str, value: str, *, review: str = "reviewed") -> list[TagRow]:
    return [
        TagRow("table", catalog, schema, table, "data_access_control", value),
        TagRow("table", catalog, schema, table, "clr_review_status", review),
    ]


def _assignment(
    value: str = "finance.budget",
    *,
    principal: str = "ana@example.com",
    key: str = "data_access",
    created: datetime | None = None,
    expires: datetime | None = None,
    revoked: datetime | None = None,
    assignment_id: str = "a1",
    principal_type: PrincipalKind = PrincipalKind.USER,
) -> IdentityAttributeAssignment:
    return IdentityAttributeAssignment(
        assignment_id=assignment_id,
        principal_name=principal,
        principal_type=principal_type,
        attribute_key=key,
        attribute_value=value,
        created_at=created or NOW - timedelta(days=1),
        expires_at=expires,
        revoked_at=revoked,
    )


class _Scim:
    """Account SCIM stand-in: groups by display name, recorded calls, optional failures."""

    def __init__(self, groups: dict[str, str] | None = None) -> None:
        self.groups = dict(groups or {})
        self.calls: list[tuple[str, str, Any]] = []
        self.patch_error: Exception | None = None
        self.create_error: Exception | None = None

    def do(self, method: str, path: str, query=None, body=None):  # noqa: ANN001, ANN201
        self.calls.append((method, path, body))
        if method == "GET":
            flt = (query or {}).get("filter") or ""
            name = flt.split('"')[1] if '"' in flt else ""
            return {
                "Resources": [
                    {"id": gid, "displayName": g}
                    for g, gid in self.groups.items()
                    if g.lower() == name.lower()
                ]
            }
        if method == "POST":
            if self.create_error is not None:
                raise self.create_error
            gid = f"g-{body['displayName']}"
            self.groups[body["displayName"]] = gid
            return {"id": gid}
        if self.patch_error is not None:
            raise self.patch_error
        return {}


def _admin_client(scim: _Scim, users: tuple[str, ...] = ("ana@example.com", "bob@example.com")) -> object:
    def _users_list(filter: str = "", **_kwargs):  # noqa: A002, ANN003, ANN202
        return iter([SimpleNamespace(id=f"u-{u}", user_name=u) for u in users if f'"{u}"' in filter])

    return SimpleNamespace(
        api_client=scim,
        users=SimpleNamespace(list=_users_list),
        service_principals=SimpleNamespace(list=lambda **_kwargs: iter(())),
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
    def __init__(self, key: str | None = "data_access") -> None:
        self._key = key
        self.listeners: list[Any] = []

    def find_data_access_definition(self, access_tag_key=None):  # noqa: ANN001, ANN201
        del access_tag_key
        return SimpleNamespace(key=self._key) if self._key else None

    def add_listener(self, listener) -> None:  # noqa: ANN001
        self.listeners.append(listener)


def _service(
    scim: _Scim | None = None,
    *,
    configured: bool = True,
    identity: _Identity | None = None,
    storage: InMemoryStorageProvider | None = None,
) -> DataAccessService:
    return DataAccessService(
        storage=storage or InMemoryStorageProvider(),
        sql=SimpleNamespace(execute=lambda _sql: []),
        factory=_Factory(_admin_client(scim or _Scim()), configured=configured),
        identity_attributes=identity or _Identity(),
    )


def _actor() -> SimpleNamespace:
    return SimpleNamespace(user_name="approver@example.com", is_admin=True, groups=[])


def _loaders(svc: DataAccessService, *, tags=(), groups=(), members=(), assignments=()) -> None:  # noqa: ANN001
    svc._tag_loader = lambda _s: list(tags)  # noqa: SLF001
    svc._grant_loader = lambda _s: []  # noqa: SLF001
    svc._group_loader = lambda _s: set(groups)  # noqa: SLF001
    svc._member_loader = lambda _s, _g: list(members)  # noqa: SLF001
    svc._assignment_loader = lambda _s: list(assignments)  # noqa: SLF001


# Pure gap and snapshot helpers.


def test_build_gap_dry_run_lists_every_missing_change_with_values() -> None:
    tags = [
        *_reviewed("cat", "fin", "budget", "finance.budget"),
        *_reviewed("cat", "fin", "ledger", "finance"),
    ]
    plan = plan_data_access(
        DataAccessSettings(),
        tags,
        [],
        {"dac_finance"},
        assignments=[AssignmentRow("ana@example.com", "USER", "finance.budget")],
        actual_members=[],
    )
    gap = build_gap(plan, [], applied=False, failures=None, run_id="r1", computed_at=NOW)
    kinds = {i.kind for i in gap}
    assert kinds == {GapKind.GROUP_CREATE, GapKind.GROUP_NEST, GapKind.MEMBER_ADD, GapKind.GRANT_ADD}
    create = next(i for i in gap if i.kind == GapKind.GROUP_CREATE)
    assert create.group_name == "dac_finance_budget" and create.value == "finance.budget"
    nest = next(i for i in gap if i.kind == GapKind.GROUP_NEST)
    assert (nest.group_name, nest.principal_name) == ("dac_finance_budget", "dac_finance")
    member = next(i for i in gap if i.kind == GapKind.MEMBER_ADD)
    assert member.value == "finance.budget" and member.item_key == member_key(
        "add", "dac_finance_budget", "ana@example.com"
    )
    grants = [i for i in gap if i.kind == GapKind.GRANT_ADD]
    assert {g.value for g in grants} == {"finance", "finance.budget"}
    assert all(i.status == GapStatus.PENDING and i.run_id == "r1" for i in gap)
    assert len({i.item_key for i in gap}) == len(gap)


def test_missing_nesting_ignores_links_already_present() -> None:
    plan = DataAccessPlan(
        summary=DataAccessPlanSummary(),
        fingerprint="x",
        groups=[
            PlannedGroup(name="dac_a_b", value="a.b", parent_group="dac_a", exists=True),
            PlannedGroup(name="dac_a_c", value="a.c", parent_group="dac_a", exists=True),
            PlannedGroup(name="dac_a", value="a", exists=True),
        ],
    )
    members = [ActualMember("dac_a_b", "DAC_A", "GROUP"), ActualMember("dac_a_c", "dac_a", "USER")]
    assert missing_nesting(plan, members) == [("dac_a_c", "dac_a", "a.c")]


def test_build_gap_after_apply_keeps_only_failures() -> None:
    grant = PlannedGrant(securable_type="table", full_name="c.s.t", principal="dac_x", privilege="SELECT")
    revoke = PlannedGrant(securable_type="table", full_name="c.s.old", principal="dac_x", privilege="SELECT")
    plan = DataAccessPlan(
        summary=DataAccessPlanSummary(),
        fingerprint="x",
        groups=[PlannedGroup(name="dac_x", value="x")],
        grants_to_add=[grant],
        grants_to_revoke=[revoke],
        membership_changes=[
            MembershipChange(
                group="dac_x",
                principal_name="gone@example.com",
                principal_type="USER",
                action=MembershipAction.REMOVE,
            )
        ],
    )
    failures = {grant_key(GapKind.GRANT_REVOKE, revoke): "denied"}
    gap = build_gap(plan, [], applied=True, failures=failures, run_id="r2", computed_at=NOW)
    assert [(i.kind, i.status, i.error) for i in gap] == [(GapKind.GRANT_REVOKE, GapStatus.FAILED, "denied")]
    dry = build_gap(plan, [], applied=False, failures=None, run_id="r2", computed_at=NOW)
    assert {i.kind for i in dry} == {
        GapKind.GROUP_CREATE,
        GapKind.MEMBER_REMOVE,
        GapKind.GRANT_ADD,
        GapKind.GRANT_REVOKE,
    }


def test_item_keys_are_case_insensitive() -> None:
    assert group_key(GapKind.GROUP_CREATE, "DAC_X") == "group_create|dac_x"
    assert nest_key("DAC_A_B", "DAC_A") == "group_nest|dac_a_b|dac_a"
    assert (
        member_key(MembershipAction.REMOVE, "Dac_X", "Ana@Example.com")
        == "member_remove|dac_x|ana@example.com"
    )


def test_snapshot_keeps_table_rows_with_the_access_tag_only() -> None:
    tags = [
        *_reviewed("cat", "fin", "budget", "finance.budget"),
        *_reviewed("cat", "fin", "draft", "finance", review="pending"),
        TagRow("table", "cat", "fin", "plain", "clr_review_status", "reviewed"),
        TagRow("schema", "cat", "fin", None, "data_access_control", "finance"),
        *_reviewed("system", "info", "t", "finance"),
    ]
    rows = snapshot_from_tags(DataAccessSettings(), tags)
    assert [(r.full_name, r.access_value, r.reviewed, r.eligible) for r in rows] == [
        ("cat.fin.budget", "finance.budget", True, True),
        ("cat.fin.draft", "finance", False, False),
    ]


def test_diff_snapshots_reports_eligibility_and_value_changes() -> None:
    previous = {
        "c.s.moved": TagSnapshotRow(
            full_name="c.s.moved", access_value="finance", reviewed=True, eligible=True
        ),
        "c.s.dropped": TagSnapshotRow(
            full_name="c.s.dropped", access_value="hr", reviewed=True, eligible=True
        ),
        "c.s.unreviewed": TagSnapshotRow(
            full_name="c.s.unreviewed", access_value="hr", reviewed=True, eligible=True
        ),
        "c.s.same": TagSnapshotRow(full_name="c.s.same", access_value="hr", reviewed=True, eligible=True),
        "c.s.pending": TagSnapshotRow(
            full_name="c.s.pending", access_value="hr", reviewed=False, eligible=False
        ),
    }
    current = [
        TagSnapshotRow(full_name="c.s.moved", access_value="finance.budget", reviewed=True, eligible=True),
        TagSnapshotRow(full_name="c.s.unreviewed", access_value="hr", reviewed=False, eligible=False),
        TagSnapshotRow(full_name="c.s.same", access_value="hr", reviewed=True, eligible=True),
        TagSnapshotRow(full_name="c.s.pending", access_value="hr", reviewed=True, eligible=True),
        TagSnapshotRow(full_name="c.s.new", access_value="ops", reviewed=True, eligible=True),
    ]
    changes = diff_snapshots(previous, current, run_id="r", detected_at=NOW)
    assert [(c.full_name, c.change, c.old_value, c.new_value) for c in changes] == [
        ("c.s.dropped", TagChangeKind.NO_LONGER_ELIGIBLE, "hr", None),
        ("c.s.moved", TagChangeKind.VALUE_CHANGED, "finance", "finance.budget"),
        ("c.s.new", TagChangeKind.NEWLY_ELIGIBLE, None, "ops"),
        ("c.s.pending", TagChangeKind.NEWLY_ELIGIBLE, None, "hr"),
        ("c.s.unreviewed", TagChangeKind.NO_LONGER_ELIGIBLE, "hr", None),
    ]


# In-memory repository.


def _item(key: str, kind: GapKind, **kwargs: Any) -> GapItem:
    return GapItem(item_key=key, kind=kind, computed_at=NOW, **kwargs)


def test_in_memory_state_repository_round_trip() -> None:
    repo = InMemoryDataAccessStateRepository()
    assert repo.gap_meta() is None
    repo.replace_gap(
        [
            _item("k1", GapKind.GRANT_ADD, value="finance.budget", full_name="c.s.t"),
            _item("k2", GapKind.MEMBER_ADD, group_name="dac_x", principal_name="Ana@example.com"),
            _item("k3", GapKind.GROUP_NEST, value="hr", status=GapStatus.FAILED, error="boom"),
            _item("k4", GapKind.GRANT_REVOKE, value=None),
        ],
        GapMeta(run_id="r1", mode="dry_run", computed_at=NOW),
    )
    assert repo.gap_meta().run_id == "r1"  # type: ignore[union-attr]
    assert repo.gap_counts() == {
        "grant_add": 1,
        "member_add": 1,
        "group_nest": 1,
        "grant_revoke": 1,
        "failed": 1,
    }
    page, total = repo.gap_page(kind=None, status=None, query=None, limit=2, offset=0)
    assert total == 4 and [i.item_key for i in page] == ["k1", "k4"]
    page, total = repo.gap_page(kind="group_nest", status="failed", query="HR", limit=10, offset=0)
    assert total == 1 and page[0].item_key == "k3"
    assert repo.gap_structure_values() == {"finance.budget", "hr"}
    assert [i.item_key for i in repo.member_gap_items(["ana@EXAMPLE.com"])] == ["k2"]
    repo.upsert_gap_items(
        [_item("k2", GapKind.MEMBER_ADD, status=GapStatus.FAILED, principal_name="ana@example.com")]
    )
    repo.delete_gap_items(["k1", "missing"])
    assert repo.gap_counts()["failed"] == 2 and "grant_add" not in repo.gap_counts()

    repo.replace_tag_snapshot([TagSnapshotRow(full_name="C.S.T", access_value="x", eligible=True)])
    assert list(repo.load_tag_snapshot()) == ["c.s.t"]
    old = TagChange(
        run_id="r0", detected_at=NOW - timedelta(days=40), full_name="a", change=TagChangeKind.NEWLY_ELIGIBLE
    )
    new = TagChange(run_id="r1", detected_at=NOW, full_name="b", change=TagChangeKind.VALUE_CHANGED)
    repo.add_tag_changes([old, new])
    assert repo.tag_change_counts("r1") == {"value_changed": 1}
    assert repo.tag_changes_page(change=None, run_id=None, limit=10, offset=0)[1] == 2
    assert repo.tag_changes_page(change="newly_eligible", run_id="r1", limit=10, offset=0)[1] == 0
    assert repo.prune_tag_changes(NOW - timedelta(days=30)) == 1

    first = MembershipEvent(
        recorded_at=NOW - timedelta(minutes=5),
        assignment_id="a1",
        principal_name="ana",
        principal_type="USER",
        group_name="dac_x",
        value="x",
        action="add",
        status=MembershipEventStatus.FAILED,
    )
    second = first.model_copy(update={"recorded_at": NOW, "status": MembershipEventStatus.APPLIED})
    repo.record_membership_event(first)
    repo.record_membership_event(second)
    assert repo.latest_membership_events(["a1", "a2"])["a1"].status == MembershipEventStatus.APPLIED
    assert len(repo.membership_events_since(NOW)) == 1
    assert repo.prune_membership_events(NOW) == 1


def test_storage_provider_exposes_a_process_local_state_store() -> None:
    storage = InMemoryStorageProvider()
    assert storage.data_access_state is storage.data_access_state


# Sync records the gap and tag changes.


def test_run_sync_stores_gap_and_baselines_then_diffs_tags() -> None:
    svc = _service()
    budget = _reviewed("cat", "fin", "budget", "finance.budget")
    _loaders(svc, tags=budget, assignments=[AssignmentRow("ana@example.com", "USER", "finance.budget")])
    first = svc.run_sync(actor="admin", trigger=SyncTrigger.USER)
    summary = svc.pending_summary()
    assert summary.meta is not None and summary.meta.run_id == first.run_id
    assert summary.meta.tag_baseline is True and summary.meta.tables_tracked == 1
    assert summary.counts.group_create == 1 and summary.counts.member_add == 1
    assert summary.counts.grant_add == 3 and summary.total == 5
    assert summary.tag_changes.newly_eligible == 0

    ledger = _reviewed("cat", "fin", "ledger", "finance")
    _loaders(svc, tags=[*ledger], assignments=[])
    second = svc.run_sync(actor="admin", trigger=SyncTrigger.USER)
    summary = svc.pending_summary()
    assert summary.meta.run_id == second.run_id and summary.meta.tag_baseline is False  # type: ignore[union-attr]
    assert summary.tag_changes.newly_eligible == 1 and summary.tag_changes.no_longer_eligible == 1
    latest = svc.tag_changes()
    assert {c.full_name for c in latest.items} == {"cat.fin.ledger", "cat.fin.budget"}
    assert svc.tag_changes(change="newly_eligible", scope="all").total == 1
    with pytest.raises(InvalidRequestError):
        svc.tag_changes(scope="ever")


def test_tag_changes_before_any_sync_is_empty() -> None:
    assert _service().tag_changes().total == 0


def test_apply_run_keeps_only_failed_items_and_events_during_the_run() -> None:
    svc = _service()
    _loaders(
        svc,
        tags=_reviewed("cat", "fin", "budget", "finance.budget"),
        assignments=[
            AssignmentRow("ana@example.com", "USER", "finance.budget"),
            AssignmentRow("bob@example.com", "USER", "finance.budget"),
        ],
    )
    svc.readiness = lambda: DataAccessReadiness(  # type: ignore[method-assign]
        dry_run_ready=True, apply_ready=True, checks=[], setup_checks=[], setup_ready=True
    )
    state = svc._storage.data_access_state  # noqa: SLF001

    def _apply(_settings, plan):  # noqa: ANN001, ANN202
        failing = next(g for g in plan.grants_to_add if g.securable_type == "table")
        # Approvals that land while the run is applying.
        for name, status in (
            ("ana@example.com", MembershipEventStatus.APPLIED),
            ("cy@example.com", MembershipEventStatus.FAILED),
        ):
            state.record_membership_event(
                MembershipEvent(
                    recorded_at=datetime.now(UTC),
                    assignment_id=name,
                    principal_name=name,
                    principal_type="USER",
                    group_name="dac_finance_budget",
                    value="finance.budget",
                    action="add",
                    status=status,
                    error="nope" if status == MembershipEventStatus.FAILED else None,
                )
            )
        return plan.model_copy(
            update={
                "apply_failures": {
                    grant_key(GapKind.GRANT_ADD, failing): "denied",
                    member_key("add", "dac_finance_budget", "ana@example.com"): "late",
                }
            }
        )

    svc._apply_plan = _apply  # noqa: SLF001
    run = svc.run_sync(actor="admin", trigger=SyncTrigger.USER, mode="apply")
    assert run.status.value == "success"
    assert run.plan is not None and "applyFailures" not in run.model_dump(by_alias=True)["plan"]
    items = svc.pending_items().items
    assert {(i.kind, i.principal_name or i.full_name, i.status) for i in items} == {
        (GapKind.GRANT_ADD, "cat.fin.budget", GapStatus.FAILED),
        (GapKind.MEMBER_ADD, "cy@example.com", GapStatus.FAILED),
    }
    assert svc.pending_summary().counts.failed == 2


def test_record_state_failure_does_not_fail_the_run() -> None:
    svc = _service()
    _loaders(svc, tags=_reviewed("cat", "fin", "budget", "finance"))

    def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("lakebase down")

    svc._storage.data_access_state.replace_gap = _boom  # type: ignore[method-assign]  # noqa: SLF001
    assert svc.run_sync(actor="admin", trigger=SyncTrigger.USER).status.value == "success"


def test_pending_items_validates_filters_and_pages() -> None:
    svc = _service()
    svc._storage.data_access_state.replace_gap(  # noqa: SLF001
        [_item(f"k{i}", GapKind.GRANT_ADD, full_name=f"c.s.t{i}") for i in range(5)],
        GapMeta(run_id="r", mode="dry_run", computed_at=NOW),
    )
    page = svc.pending_items(kind="grant_add", page=2, page_size=2)
    assert page.total == 5 and [i.item_key for i in page.items] == ["k2", "k3"]
    assert svc.pending_items(query="t4").total == 1
    with pytest.raises(InvalidRequestError):
        svc.pending_items(kind="bogus")
    with pytest.raises(InvalidRequestError):
        svc.pending_items(status="done")


def test_expired_since_sync_counts_and_pages() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage=storage)
    now = datetime.now(UTC)
    storage.data_access_state.replace_gap(
        [], GapMeta(run_id="r", mode="apply", computed_at=now - timedelta(hours=1))
    )
    repo = storage.identity_attributes
    repo.add_assignment(_assignment(assignment_id="e1", expires=now - timedelta(minutes=10)))
    repo.add_assignment(
        _assignment(assignment_id="e2", principal="bob@example.com", expires=now - timedelta(minutes=5))
    )
    repo.add_assignment(_assignment(assignment_id="old", expires=now - timedelta(days=2)))
    repo.add_assignment(_assignment(assignment_id="rev", expires=now - timedelta(minutes=1), revoked=now))
    repo.add_assignment(_assignment(assignment_id="star", value="*", expires=now - timedelta(minutes=1)))
    repo.add_assignment(
        _assignment(assignment_id="other", key="sensitivity", expires=now - timedelta(minutes=1))
    )
    assert svc.pending_summary().counts.expired_since_sync == 2
    page = svc.pending_items(kind="expired", page_size=1)
    assert page.total == 2 and page.items[0].principal_name == "bob@example.com"
    assert page.items[0].kind == GapKind.MEMBER_REMOVE and page.items[0].group_name == "dac_finance_budget"
    assert svc.pending_items(kind="expired", query="ana").total == 1
    assert _service(identity=_Identity(key=None)).pending_summary().counts.expired_since_sync == 0


# Apply helpers record failures by item key.


def test_apply_helpers_record_failure_keys() -> None:
    scim = _Scim()
    scim.create_error = DatabricksError("quota")
    svc = _service(scim)
    failures: dict[str, str] = {}
    errors: list[str] = []
    ids = svc._ensure_groups([PlannedGroup(name="dac_new", value="new")], errors, failures)  # noqa: SLF001
    assert ids == {} and failures[group_key(GapKind.GROUP_CREATE, "dac_new")] == "quota"

    svc._nest_groups(  # noqa: SLF001
        [PlannedGroup(name="dac_a_b", value="a.b", parent_group="dac_a")], {}, errors, failures
    )
    assert failures[nest_key("dac_a_b", "dac_a")] == "missing group id"
    scim.patch_error = DatabricksError("denied")
    svc._nest_groups(  # noqa: SLF001
        [PlannedGroup(name="dac_a_b", value="a.b", parent_group="dac_a")],
        {"dac_a_b": "g1", "dac_a": "g2"},
        errors,
        failures,
    )
    assert failures[nest_key("dac_a_b", "dac_a")] == "denied"

    changes = [
        MembershipChange(
            group="dac_none",
            principal_name="ana@example.com",
            principal_type="USER",
            action=MembershipAction.ADD,
        ),
        MembershipChange(
            group="dac_x",
            principal_name="ghost@example.com",
            principal_type="USER",
            action=MembershipAction.ADD,
        ),
        MembershipChange(
            group="dac_x",
            principal_name="bob@example.com",
            principal_type="USER",
            action=MembershipAction.REMOVE,
        ),
    ]
    svc._apply_membership(changes, {"dac_x": "gx"}, errors, failures)  # noqa: SLF001
    assert failures[member_key("add", "dac_none", "ana@example.com")] == "missing group"
    assert "not found" in failures[member_key("add", "dac_x", "ghost@example.com")]
    assert failures[member_key("remove", "dac_x", "bob@example.com")] == "denied"

    def _fail(_sql: str) -> None:
        raise RuntimeError("no warehouse")

    svc._sql = SimpleNamespace(execute=_fail)  # noqa: SLF001
    grant = PlannedGrant(securable_type="table", full_name="c.s.t", principal="dac_x", privilege="SELECT")
    svc._apply_grants([grant], [grant], errors, failures)  # noqa: SLF001
    assert failures[grant_key(GapKind.GRANT_ADD, grant)] == "no warehouse"
    assert failures[grant_key(GapKind.GRANT_REVOKE, grant)] == "no warehouse"


def test_apply_plan_impl_returns_failures_off_the_stored_shape() -> None:
    svc = _service(_Scim({"dac_x": "gx"}))
    plan = DataAccessPlan(
        summary=DataAccessPlanSummary(), fingerprint="f", groups=[PlannedGroup(name="dac_x", value="x")]
    )
    applied = svc._apply_plan_impl(DataAccessSettings(), plan)  # noqa: SLF001
    assert applied.apply_failures == {} and applied.apply_errors == []


# Immediate membership on approve / assign / revoke.


def _events(svc: DataAccessService) -> list[MembershipEvent]:
    return svc._storage.data_access_state.membership_events_since(datetime.min.replace(tzinfo=UTC))  # noqa: SLF001


def test_service_registers_itself_as_an_assignment_listener() -> None:
    identity = _Identity()
    svc = _service(identity=identity)
    assert identity.listeners == [svc.on_assignment_event]


def test_assign_creates_missing_group_and_adds_member() -> None:
    scim = _Scim()
    svc = _service(scim)
    state = svc._storage.data_access_state  # noqa: SLF001
    state.upsert_gap_items(
        [_item(member_key("add", "dac_finance_budget", "ana@example.com"), GapKind.MEMBER_ADD)]
    )
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(), _actor())
    assert scim.groups["dac_finance_budget"] == "g-dac_finance_budget"
    patch = next(c for c in scim.calls if c[0] == "PATCH")
    assert patch[2]["Operations"][0]["value"] == [{"value": "u-ana@example.com"}]
    [event] = _events(svc)
    assert (event.action, event.status, event.group_created, event.actor) == (
        "add",
        MembershipEventStatus.APPLIED,
        True,
        "approver@example.com",
    )
    assert state.gap_counts() == {}


def test_assign_into_existing_group_treats_already_member_as_success() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    scim.patch_error = DatabricksError("User already exists in the group")
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(), _actor())
    [event] = _events(svc)
    assert event.status == MembershipEventStatus.APPLIED and event.group_created is False
    assert not any(c[0] == "POST" for c in scim.calls)


def test_assign_failure_is_recorded_and_listed_for_sync() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    scim.patch_error = DatabricksError("permission denied")
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(), _actor())
    [event] = _events(svc)
    assert event.status == MembershipEventStatus.FAILED and "permission denied" in (event.error or "")
    [item] = svc.pending_items(kind="member_add").items
    assert item.status == GapStatus.FAILED and item.principal_name == "ana@example.com"


def test_assign_unknown_principal_fails_without_patching() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(principal="ghost@example.com"), _actor())
    [event] = _events(svc)
    assert event.status == MembershipEventStatus.FAILED and "not found" in (event.error or "")
    assert not any(c[0] == "PATCH" for c in scim.calls)


def test_assign_without_admin_sp_is_left_for_sync() -> None:
    svc = _service(configured=False)
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(), _actor())
    [event] = _events(svc)
    assert event.status == MembershipEventStatus.SKIPPED
    assert svc.pending_items(kind="member_add").items[0].status == GapStatus.PENDING


@pytest.mark.parametrize(
    ("assignment", "identity"),
    [
        (_assignment(key="sensitivity"), _Identity()),
        (_assignment(value="*"), _Identity()),
        (_assignment(value="..."), _Identity()),
        (_assignment(), _Identity(key=None)),
    ],
)
def test_non_data_access_assignments_are_ignored(
    assignment: IdentityAttributeAssignment, identity: _Identity
) -> None:
    scim = _Scim()
    svc = _service(scim, identity=identity)
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, assignment, _actor())
    assert scim.calls == [] and _events(svc) == []


def test_revoke_removes_member() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_REVOKED, _assignment(revoked=NOW), _actor())
    patch = next(c for c in scim.calls if c[0] == "PATCH")
    assert patch[2]["Operations"][0] == {"op": "remove", "path": 'members[value eq "u-ana@example.com"]'}
    assert _events(svc)[0].status == MembershipEventStatus.APPLIED


def test_revoke_when_group_or_membership_is_already_gone_is_success() -> None:
    svc = _service(_Scim())
    svc.on_assignment_event(ASSIGNMENT_REVOKED, _assignment(revoked=NOW), _actor())
    assert _events(svc)[0].status == MembershipEventStatus.APPLIED

    scim = _Scim({"dac_finance_budget": "g1"})
    scim.patch_error = DatabricksError("Member not found")
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_REVOKED, _assignment(revoked=NOW), _actor())
    assert _events(svc)[0].status == MembershipEventStatus.APPLIED


def test_revoke_failure_is_listed_for_sync() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    scim.patch_error = DatabricksError("forbidden")
    svc = _service(scim)
    svc.on_assignment_event(ASSIGNMENT_REVOKED, _assignment(revoked=NOW), _actor())
    assert _events(svc)[0].status == MembershipEventStatus.FAILED
    assert svc.pending_items(kind="member_remove").total == 1


def test_revoke_keeps_member_when_another_assignment_maps_to_the_group() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    storage = InMemoryStorageProvider()
    storage.identity_attributes.add_assignment(_assignment(assignment_id="keep", value="Finance.Budget"))
    svc = _service(scim, storage=storage)
    svc.on_assignment_event(ASSIGNMENT_REVOKED, _assignment(assignment_id="gone", revoked=NOW), _actor())
    assert not any(c[0] == "PATCH" for c in scim.calls)
    [event] = _events(svc)
    assert event.status == MembershipEventStatus.SKIPPED
    assert svc.pending_summary().total == 0


# Access status.


def _status(assignment: IdentityAttributeAssignment, **overrides: Any):  # noqa: ANN202
    args: dict[str, Any] = {
        "prefix": "dac_",
        "separator": ".",
        "meta": GapMeta(run_id="r", mode="apply", computed_at=NOW - timedelta(hours=1)),
        "structure_values": set(),
        "groups_created_since_sync": set(),
        "event": None,
        "member_items": [],
        "now": NOW,
    }
    args.update(overrides)
    return compute_access_status(assignment, **args)


def _event(action: str, status: MembershipEventStatus, **kwargs: Any) -> MembershipEvent:
    return MembershipEvent(
        recorded_at=NOW,
        assignment_id="a1",
        principal_name="ana@example.com",
        principal_type="USER",
        group_name="dac_finance_budget",
        value="finance.budget",
        action=action,
        status=status,
        **kwargs,
    )


def test_status_member_and_ready_after_sync() -> None:
    status = _status(_assignment(created=NOW - timedelta(days=2)))
    assert (status.membership, status.structure) == (MembershipState.MEMBER, StructureState.READY)
    assert status.group_name == "dac_finance_budget"


def test_status_member_but_structure_pending_for_new_group_or_subtree() -> None:
    applied = _event("add", MembershipEventStatus.APPLIED)
    created = _status(
        _assignment(created=NOW), event=applied, groups_created_since_sync={"dac_finance_budget"}
    )
    assert (created.membership, created.structure) == (MembershipState.MEMBER, StructureState.PENDING_SYNC)
    assert "next sync" in created.detail
    subtree = _status(_assignment("finance"), structure_values={"finance.budget", "financeX", "hr"})
    assert subtree.structure == StructureState.PENDING_SYNC and subtree.detail.startswith("1 path")


def test_status_membership_variants() -> None:
    assert _status(_assignment(created=NOW)).membership == MembershipState.PENDING_SYNC
    assert _status(_assignment(), meta=None).membership == MembershipState.UNKNOWN
    assert _status(_assignment(), meta=None).structure == StructureState.UNKNOWN
    failed = _status(_assignment(), event=_event("add", MembershipEventStatus.FAILED, error="denied"))
    assert failed.membership == MembershipState.FAILED and "denied" in failed.detail
    skipped = _status(_assignment(), event=_event("add", MembershipEventStatus.SKIPPED, error="no admin SP"))
    assert skipped.membership == MembershipState.PENDING_SYNC and "no admin SP" in skipped.detail
    pending = _item(
        "k", GapKind.MEMBER_ADD, group_name="DAC_FINANCE_BUDGET", principal_name="ana@example.com"
    )
    assert _status(_assignment(), member_items=[pending]).membership == MembershipState.PENDING_SYNC
    failed_item = pending.model_copy(update={"status": GapStatus.FAILED, "error": "x"})
    assert _status(_assignment(), member_items=[failed_item]).membership == MembershipState.FAILED
    other_group = pending.model_copy(update={"group_name": "dac_hr"})
    assert _status(_assignment(), member_items=[other_group]).membership == MembershipState.MEMBER


def test_status_for_revoked_expired_and_wildcard() -> None:
    expired = _status(_assignment(expires=NOW - timedelta(minutes=1)))
    assert expired.membership == MembershipState.REMOVAL_PENDING and expired.detail.startswith("Expired")
    revoked = _status(_assignment(revoked=NOW))
    assert revoked.membership == MembershipState.REMOVAL_PENDING and revoked.detail.startswith("Revoked")
    removed = _status(_assignment(revoked=NOW), event=_event("remove", MembershipEventStatus.APPLIED))
    assert removed.membership == MembershipState.REMOVED
    failed = _status(
        _assignment(revoked=NOW), event=_event("remove", MembershipEventStatus.FAILED, error="x")
    )
    assert failed.membership == MembershipState.FAILED
    assert removed.structure == StructureState.NOT_APPLICABLE
    assert _status(_assignment("*")).membership == MembershipState.NOT_APPLICABLE
    assert _status(_assignment("...")).group_name is None


def test_access_status_service_filters_to_data_access_assignments() -> None:
    svc = _service(_Scim())
    state = svc._storage.data_access_state  # noqa: SLF001
    state.replace_gap(
        [_item("k", GapKind.GRANT_ADD, value="hr")],
        GapMeta(run_id="r", mode="apply", computed_at=datetime.now(UTC) - timedelta(hours=1)),
    )
    svc.on_assignment_event(ASSIGNMENT_ASSIGNED, _assignment(), _actor())
    statuses = svc.access_status(
        [
            _assignment(),
            _assignment(
                "hr",
                assignment_id="a2",
                principal="bob@example.com",
                created=datetime.now(UTC) - timedelta(hours=2),
            ),
            _assignment(key="sensitivity"),
        ]
    )
    assert [(s.assignment_id, s.membership, s.structure) for s in statuses] == [
        ("a1", MembershipState.MEMBER, StructureState.PENDING_SYNC),
        ("a2", MembershipState.MEMBER, StructureState.PENDING_SYNC),
    ]
    assert svc.access_status([_assignment(key="sensitivity")]) == []
    assert _service(identity=_Identity(key=None)).access_status([_assignment()]) == []


# Identity service listener and API routes.


def test_identity_service_notifies_listeners_and_isolates_failures(container) -> None:  # noqa: ANN001
    container.data_access._factory = _Factory(_admin_client(_Scim()), configured=False)  # noqa: SLF001
    service = container.identity_attributes
    service._governed_tags = FakeGovernedTags()  # noqa: SLF001
    seen: list[tuple[str, str]] = []

    def _broken(*_args: Any) -> None:
        raise RuntimeError("listener bug")

    service.add_listener(_broken)
    service.add_listener(lambda event, assignment, actor: seen.append((event, assignment.attribute_value)))
    actor = SimpleNamespace(user_name="admin@example.com", is_admin=True, groups=["admins"])
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(governed_tag_keys=["data_access_control"]), actor
    )
    assigned = service.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="data_access_control",
            attribute_value="finance",
        ),
        actor,
    )
    service.revoke(assigned.assignment_id, actor)
    assert seen == [(ASSIGNMENT_ASSIGNED, "finance"), (ASSIGNMENT_REVOKED, "finance")]
    # The container's data-access listener ran too; without an Admin SP it leaves the change to sync.
    events = container.data_access._storage.data_access_state.membership_events_since(  # noqa: SLF001
        datetime.min.replace(tzinfo=UTC)
    )
    assert [e.action for e in events] == ["add", "remove"]


def test_pending_routes_require_admin(client: TestClient) -> None:
    for path in (
        "/api/data-access/pending",
        "/api/data-access/pending/items",
        "/api/data-access/tag-changes",
    ):
        assert client.get(path, headers=headers_for("analyst@example.com")).status_code == 403
        assert client.get(path, headers=headers_for("admin@example.com")).status_code == 200
    denied = client.post(
        "/api/data-access/access-status",
        json={"assignmentIds": []},
        headers=headers_for("analyst@example.com"),
    )
    assert denied.status_code == 403


def test_pending_routes_return_counts_items_and_status(client: TestClient, container) -> None:  # noqa: ANN001
    container.data_access._factory = _Factory(_admin_client(_Scim()), configured=False)  # noqa: SLF001
    state = container.storage.data_access_state
    state.replace_gap(
        [_item("k1", GapKind.GRANT_ADD, value="finance", full_name="c.s.t")],
        GapMeta(run_id="r1", mode="dry_run", computed_at=datetime.now(UTC) - timedelta(hours=1)),
    )
    state.add_tag_changes(
        [
            TagChange(
                run_id="r1",
                detected_at=NOW,
                full_name="c.s.t",
                change=TagChangeKind.NEWLY_ELIGIBLE,
                new_value="finance",
            )
        ]
    )
    admin = headers_for("admin@example.com")
    summary = client.get("/api/data-access/pending", headers=admin).json()
    assert summary["counts"]["grantAdd"] == 1 and summary["tagChanges"]["newlyEligible"] == 1
    items = client.get("/api/data-access/pending/items?kind=grant_add&page_size=10", headers=admin).json()
    assert items["total"] == 1 and items["items"][0]["fullName"] == "c.s.t"
    assert client.get("/api/data-access/pending/items?kind=nope", headers=admin).status_code == 422
    changes = client.get("/api/data-access/tag-changes?scope=all", headers=admin).json()
    assert changes["items"][0]["change"] == "newly_eligible"

    service = container.identity_attributes
    service._governed_tags = FakeGovernedTags()  # noqa: SLF001
    actor = SimpleNamespace(user_name="admin@example.com", is_admin=True, groups=["admins"])
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(governed_tag_keys=["data_access_control"]), actor
    )
    assigned = service.assign(
        AssignAttributeRequest(
            principal_name="analyst@example.com",
            principal_type=PrincipalKind.USER,
            attribute_key="data_access_control",
            attribute_value="finance",
        ),
        actor,
    )
    mine = client.get("/api/data-access/access-status/me", headers=headers_for("analyst@example.com")).json()
    assert [(s["value"], s["membership"], s["structure"]) for s in mine["statuses"]] == [
        ("finance", "pending_sync", "pending_sync")
    ]
    body = {"assignmentIds": [assigned.assignment_id, "missing", assigned.assignment_id]}
    listed = client.post("/api/data-access/access-status", json=body, headers=admin).json()
    assert [s["assignmentId"] for s in listed["statuses"]] == [assigned.assignment_id]

"""Subscriptions: reserved group prefix, validation, resolution, reconcile, and scope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.errors import ForbiddenError, InvalidRequestError
from backend.app.models.app_settings import AppSettings
from backend.app.models.data_access import DataAccessSettings, MembershipAction
from backend.app.models.identity_attribute import AttributeSource, IdentityAttributeAssignment
from backend.app.models.principal import PrincipalKind
from backend.app.models.subscription import (
    PrivilegePreset,
    ResourceSelector,
    SecurableKind,
    SubscriptionDraft,
    SubscriptionResource,
    SubscriptionStatus,
    TagCondition,
    TagMatchMode,
    privileges_for,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services.app_settings import AppSettingsService
from backend.app.services.data_access_planner import (
    ActualGrant,
    ActualMember,
    AssignmentRow,
    TagRow,
    is_subscription_group,
    plan_data_access,
)
from backend.app.services.subscription_engine import SubscriptionEngine, TagInventory
from backend.app.services.subscriptions import SubscriptionService

ADMIN = CurrentUser(
    user_name="admin@example.com", email="admin@example.com", display_name="Admin", is_admin=True
)
APPROVER = CurrentUser(
    user_name="appr@example.com", email="appr@example.com", display_name="Approver", is_approver=True
)
SETTINGS = DataAccessSettings()


# ---------------------------------------------------------------- planner prefix


def test_subscription_groups_are_reserved_from_data_access_sync() -> None:
    assert is_subscription_group("dac_subscription_ia_build", "dac_")
    assert not is_subscription_group("dac_people", "dac_")
    tags = [
        TagRow("table", "c", "s", "t", "access_path", "people"),
        TagRow("table", "c", "s", "t", "data_governance_status", "ready"),
        TagRow("table", "c", "s", "u", "access_path", "subscription.x"),
        TagRow("table", "c", "s", "u", "data_governance_status", "ready"),
    ]
    plan = plan_data_access(
        SETTINGS,
        tags,
        [ActualGrant("table", "c.s.other", "dac_subscription_ia_build", "SELECT")],
        {"dac_people"},
        assignments=[],
        actual_members=[ActualMember("dac_people", "dac_subscription_ia_build", "GROUP")],
    )
    assert plan.grants_to_revoke == []
    assert "subscription.x" in plan.invalid_values
    removes = [m for m in plan.membership_changes if m.action == MembershipAction.REMOVE]
    assert [(m.group, m.principal_name) for m in removes] == [("dac_people", "dac_subscription_ia_build")]


def test_subscription_group_kept_in_dac_group_while_assigned() -> None:
    tags = [
        TagRow("table", "c", "s", "t", "access_path", "people"),
        TagRow("table", "c", "s", "t", "data_governance_status", "ready"),
    ]
    plan = plan_data_access(
        SETTINGS,
        tags,
        [],
        {"dac_people"},
        assignments=[AssignmentRow("dac_subscription_x", "GROUP", "people")],
        actual_members=[ActualMember("dac_people", "dac_subscription_x", "GROUP")],
    )
    assert plan.membership_changes == []


# ---------------------------------------------------------------- models


def _object(
    kind: SecurableKind, name: str, preset: PrivilegePreset = PrivilegePreset.READ
) -> SubscriptionResource:
    return SubscriptionResource(
        selector=ResourceSelector.OBJECT, securable_kind=kind, full_name=name, preset=preset
    )


def _draft(**overrides: Any) -> SubscriptionDraft:
    data: dict[str, Any] = {
        "name": "ia_build",
        "display_name": "IA build",
        "expires_at": datetime.now(UTC) + timedelta(days=30),
        "subjects": [{"principal_name": "ana@example.com", "principal_type": "USER"}],
        "resources": [
            {
                "selector": "TAG_MATCH",
                "conditions": [{"tag_key": "access_path", "values": ["people.ia"], "match": "PREFIX"}],
                "levels": ["TABLE"],
                "preset": "READ",
            }
        ],
    }
    data.update(overrides)
    return SubscriptionDraft.model_validate(data)


@pytest.mark.parametrize("name", ["IA Build", "ia-build", "ia__build", "_ia", "ia build!"])
def test_names_must_be_slugs(name: str) -> None:
    with pytest.raises(ValueError):
        _draft(name=name)
    assert _draft(name="IA_build").name == "ia_build"


def test_object_names_need_the_right_parts() -> None:
    with pytest.raises(ValueError):
        _object(SecurableKind.TABLE, "c.s")
    with pytest.raises(ValueError):
        _object(SecurableKind.WORKSPACE_FOLDER, "Shared/x")
    assert _object(SecurableKind.SCHEMA, "c.s").full_name == "c.s"


def test_presets_only_grant_privileges_valid_for_the_object_type() -> None:
    assert privileges_for(SecurableKind.TABLE, PrivilegePreset.BUILD, []) == ["APPLY TAG", "SELECT", "MODIFY"]
    assert privileges_for(SecurableKind.VOLUME, PrivilegePreset.READ, []) == ["READ VOLUME"]
    assert privileges_for(SecurableKind.WORKSPACE_FOLDER, PrivilegePreset.BUILD, []) == ["CAN_EDIT"]
    assert privileges_for(SecurableKind.APP, PrivilegePreset.READ, []) == ["CAN_USE"]
    assert privileges_for(SecurableKind.DASHBOARD, PrivilegePreset.READ, []) == ["CAN_RUN"]
    assert privileges_for(SecurableKind.SQL_WAREHOUSE, PrivilegePreset.ADMIN, []) == ["CAN_MANAGE"]
    assert privileges_for(SecurableKind.CATALOG, PrivilegePreset.ADMIN, []) == ["ALL PRIVILEGES", "MANAGE"]
    assert privileges_for(SecurableKind.EXTERNAL_LOCATION, PrivilegePreset.READ, []) == ["READ FILES"]


def test_build_matches_the_data_editor_preset_at_catalog_level() -> None:
    data_editor = {
        "APPLY TAG",
        "BROWSE",
        "READ VOLUME",
        "SELECT",
        "MODIFY",
        "WRITE VOLUME",
        "CREATE FUNCTION",
        "CREATE MATERIALIZED VIEW",
        "CREATE MODEL",
        "CREATE SCHEMA",
        "CREATE TABLE",
        "CREATE VOLUME",
    }
    assert set(privileges_for(SecurableKind.CATALOG, PrivilegePreset.BUILD, [])) == data_editor
    with pytest.raises(ValueError):
        SubscriptionResource(
            selector=ResourceSelector.OBJECT,
            securable_kind=SecurableKind.FUNCTION,
            full_name="c.s.f",
            preset=PrivilegePreset.CUSTOM,
            privileges=["SELECT"],
        )


# ---------------------------------------------------------------- engine fakes


def _inventory() -> TagInventory:
    inv = TagInventory()
    rows = [
        (SecurableKind.CATALOG, "dga_people", {"data_governance_status": "ready"}),
        (
            SecurableKind.SCHEMA,
            "dga_people.ia",
            {"access_path": "people.ia", "data_governance_status": "ready"},
        ),
        (
            SecurableKind.TABLE,
            "dga_people.ia.claims",
            {"access_path": "people.ia.nemis", "data_governance_status": "ready"},
        ),
        (
            SecurableKind.TABLE,
            "dga_people.ia.drafts",
            {"access_path": "people.ia.nemis", "data_governance_status": "draft"},
        ),
        (SecurableKind.TABLE, "dga_people.hr.staff", {"access_path": "people.hr"}),
        (SecurableKind.VOLUME, "dga_people.ia.files", {"access_path": "people.ia"}),
    ]
    for kind, name, tags in rows:
        inv.tags[(kind, name.lower())] = tags
        inv.names[(kind, name.lower())] = name
    return inv


class _Sql:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.configured = True

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def query(self, statement: str) -> list[dict]:
        return []


class _DataAccess:
    def __init__(self) -> None:
        self.groups: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}

    def _settings(self) -> DataAccessSettings:
        return SETTINGS

    def list_enrolled_catalogs(self, *_: Any, **__: Any) -> list[str]:
        return ["dga_people"]

    def _find_group_id(self, group: str) -> str | None:
        return self.groups.get(group)

    def _account_scim_create_group(self, group: str) -> str:
        self.groups[group] = f"g-{group}"
        self.members[f"g-{group}"] = set()
        return self.groups[group]

    def _resolve_principal_id(self, name: str, kind: str, errors: list[str]) -> str:
        return f"id-{name}"

    def _patch_member_add(self, group_id: str, member_id: str) -> None:
        self.members[group_id].add(member_id)

    def _patch_member_remove(self, group_id: str, member_id: str) -> None:
        self.members[group_id].discard(member_id)


class _Api:
    def __init__(self, data_access: _DataAccess) -> None:
        self.data_access = data_access
        self.workspace_calls: list[tuple[str, str]] = []
        self.bodies: dict[str, Any] = {}

    def do(self, method: str, path: str, query: Any = None, body: Any = None) -> Any:
        if path.startswith("/api/2.0/account/scim/v2/Groups/"):
            gid = path.rsplit("/", 1)[-1]
            return {"members": [{"value": m} for m in sorted(self.data_access.members.get(gid, set()))]}
        if path == "/api/2.0/preview/permissionassignments":
            return {"permission_assignments": []}
        if path == "/api/2.0/preview/scim/v2/Groups":
            return {"Resources": []}
        self.workspace_calls.append((method, path))
        if method == "PUT":
            self.bodies[path] = body
        if method == "GET" and path.startswith("/api/2.0/permissions/"):
            return {
                "access_control_list": [
                    {
                        "user_name": "owner@example.com",
                        "all_permissions": [{"permission_level": "CAN_MANAGE"}],
                    },
                    {
                        "group_name": "dac_subscription_ia_build",
                        "all_permissions": [{"permission_level": "CAN_RUN"}],
                    },
                    {
                        "group_name": "admins",
                        "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": True}],
                    },
                ]
            }
        return {}


class _Identity:
    def __init__(self, storage: InMemoryStorageProvider) -> None:
        self.storage = storage
        self.assigned: list[tuple[str, str]] = []
        self.revoked: list[str] = []

    def assign(self, request: Any, actor: Any, *, source: AttributeSource) -> Any:
        self.assigned.append((request.attribute_key, request.attribute_value))
        item = IdentityAttributeAssignment(
            assignment_id=f"a-{len(self.assigned)}",
            principal_name=request.principal_name,
            principal_type=request.principal_type,
            attribute_key=request.attribute_key,
            attribute_value=request.attribute_value,
            source=source,
            expires_at=request.expires_at,
            created_at=datetime.now(UTC),
        )
        return self.storage.identity_attributes.add_assignment(item)

    def revoke(self, assignment_id: str, actor: Any) -> Any:
        self.revoked.append(assignment_id)
        return self.storage.identity_attributes.revoke_assignment(assignment_id, "engine", datetime.now(UTC))

    def list_definitions(self) -> Any:
        return SimpleNamespace(definitions=[])

    def get_definition(self, key: str) -> Any:
        return SimpleNamespace(key=key, allowed_values=[])

    def find_data_access_definition(self) -> Any:
        return SimpleNamespace(key="data_access")

    def all_governed_tags(self) -> list[Any]:
        return [
            SimpleNamespace(tag_key="access_path", values=["people", "people.ia"]),
            SimpleNamespace(tag_key="Region", values=["west", "east"]),
        ]


def _engine() -> tuple[SubscriptionEngine, InMemoryStorageProvider, _Sql, _DataAccess, _Identity, _Api]:
    storage = InMemoryStorageProvider()
    sql = _Sql()
    data_access = _DataAccess()
    api = _Api(data_access)
    factory = SimpleNamespace(admin=lambda: SimpleNamespace(api_client=api))
    identity = _Identity(storage)
    engine = SubscriptionEngine(storage, sql, factory, data_access, identity)  # type: ignore[arg-type]
    engine.tag_loader = lambda catalogs, keys: _inventory()
    engine.grant_loader = lambda settings: {}
    return engine, storage, sql, data_access, identity, api


# ---------------------------------------------------------------- resolution


def test_tag_match_uses_prefix_and_review_gate() -> None:
    engine, *_ = _engine()
    resolution = engine.resolve(
        _draft().resources, review_exempt=False, settings=SETTINGS, inventory=_inventory()
    )
    assert [o.full_name for o in resolution.objects] == ["dga_people.ia.claims"]
    assert resolution.skipped_unreviewed == 1
    privileges = {(g.securable_kind.value, g.full_name, g.privilege) for g in resolution.grants.values()}
    assert privileges == {
        ("CATALOG", "dga_people", "USE CATALOG"),
        ("SCHEMA", "dga_people.ia", "USE SCHEMA"),
        ("TABLE", "dga_people.ia.claims", "SELECT"),
    }
    exempt = engine.resolve(_draft().resources, review_exempt=True, settings=SETTINGS, inventory=_inventory())
    assert len(exempt.objects) == 2


def test_picked_volume_uses_parent_review_tag() -> None:
    engine, *_ = _engine()
    resources = [_object(SecurableKind.VOLUME, "dga_people.ia.files", PrivilegePreset.VOLUME_READ_WRITE)]
    resolution = engine.resolve(resources, review_exempt=False, settings=SETTINGS, inventory=_inventory())
    assert resolution.objects[0].privileges == ["READ VOLUME", "WRITE VOLUME"]
    assert resolution.objects[0].access_path == "people.ia"


# ---------------------------------------------------------------- reconcile


def _service(engine: SubscriptionEngine, storage: InMemoryStorageProvider, identity: _Identity, **app: Any):
    app_settings = AppSettingsService(storage, None)
    if app:
        app_settings.save_settings(AppSettings(**app), ADMIN)
    access = SimpleNamespace(
        grantable_attributes=lambda actor: SimpleNamespace(
            attributes=[SimpleNamespace(key="sensitivity", categories=["people.ia"])]
        ),
        _can_decide_for=lambda actor, value, attribute_key: value.startswith("people.ia"),
    )
    return SubscriptionService(storage, engine, identity, access, app_settings, None)  # type: ignore[arg-type]


def test_admin_subscription_applies_then_strips_on_revoke() -> None:
    engine, storage, sql, data_access, identity, api = _engine()
    service = _service(engine, storage, identity)
    draft = _draft(attributes=[{"attribute_key": "sensitivity", "attribute_value": "people.ia.pii"}])
    created = service.create(draft, ADMIN)
    assert created.status == SubscriptionStatus.APPROVED
    assert created.group_name == "dac_subscription_ia_build"

    state = service.reconcile(created.subscription_id)
    assert state is not None and state.ok and state.active
    gid = data_access.groups["dac_subscription_ia_build"]
    assert data_access.members[gid] == {"id-ana@example.com"}
    assert "GRANT SELECT ON TABLE `dga_people`.`ia`.`claims` TO `dac_subscription_ia_build`" in sql.executed
    assert identity.assigned == [("sensitivity", "people.ia.pii")]
    assert ("PUT", f"/api/2.0/preview/permissionassignments/principals/{gid}") in api.workspace_calls

    engine.grant_loader = lambda settings: {
        "dac_subscription_ia_build": {
            ("TABLE", "dga_people.ia.claims", "SELECT"),
            ("CATALOG", "dga_people", "USE CATALOG"),
            ("SCHEMA", "dga_people.ia", "USE SCHEMA"),
        }
    }
    service.revoke(created.subscription_id, ADMIN)
    state = service.reconcile(created.subscription_id)
    assert state is not None and not state.active and state.grants_revoked == 3
    assert data_access.members[gid] == set()
    assert (
        "REVOKE SELECT ON TABLE `dga_people`.`ia`.`claims` FROM `dac_subscription_ia_build`" in sql.executed
    )
    assert identity.revoked == ["a-1"]


def test_scheduled_subscription_waits_for_its_start() -> None:
    engine, storage, sql, data_access, identity, _ = _engine()
    service = _service(engine, storage, identity)
    created = service.create(_draft(starts_at=datetime.now(UTC) + timedelta(days=2)), ADMIN)
    state = service.reconcile(created.subscription_id)
    assert state is not None and not state.active
    assert data_access.groups == {}
    assert sql.executed == []


def test_window_must_fit_the_maximum() -> None:
    engine, storage, *_rest = _engine()
    identity = _rest[2]
    service = _service(engine, storage, identity, subscription_max_days=10)
    with pytest.raises(InvalidRequestError):
        service.create(_draft(), ADMIN)
    service = _service(engine, storage, identity, subscription_max_days=60)
    service.create(_draft(), ADMIN)
    with pytest.raises(InvalidRequestError):
        service.create(_draft(), ADMIN)


def test_approver_subscriptions_wait_and_stay_in_scope() -> None:
    engine, storage, _sql, _da, identity, _ = _engine()
    service = _service(engine, storage, identity)
    created = service.create(_draft(), APPROVER)
    assert created.status == SubscriptionStatus.PENDING_APPROVAL

    outside = _draft(
        name="hr",
        resources=[
            {
                "selector": "TAG_MATCH",
                "conditions": [{"tag_key": "access_path", "values": ["people.hr"], "match": "PREFIX"}],
            }
        ],
    )
    with pytest.raises(ForbiddenError):
        service.create(outside, APPROVER)
    unscoped = _draft(
        name="all_ready",
        resources=[
            {
                "selector": "TAG_MATCH",
                "conditions": [{"tag_key": "data_governance_status", "values": ["ready"]}],
            }
        ],
    )
    with pytest.raises(ForbiddenError):
        service.create(unscoped, APPROVER)
    picked_outside = _draft(
        name="hr_pick", resources=[_object(SecurableKind.TABLE, "dga_people.hr.staff").model_dump()]
    )
    with pytest.raises(ForbiddenError):
        service.create(picked_outside, APPROVER)
    with pytest.raises(ForbiddenError):
        service.create(_draft(name="exempt", review_exempt=True), APPROVER)

    approved = service.decide(created.subscription_id, SimpleNamespace(approve=True, note=None), ADMIN)  # type: ignore[arg-type]
    assert approved.status == SubscriptionStatus.APPROVED
    assert approved.decided_by == ADMIN.user_name


def test_approver_subscriptions_skip_review_when_setting_is_off() -> None:
    engine, storage, _sql, _da, identity, _ = _engine()
    service = _service(engine, storage, identity, subscriptions_require_admin_approval=False)
    assert service.create(_draft(), APPROVER).status == SubscriptionStatus.APPROVED


def test_managed_groups_cannot_be_subjects() -> None:
    engine, storage, _sql, _da, identity, _ = _engine()
    service = _service(engine, storage, identity)
    draft = _draft(subjects=[{"principal_name": "dac_people", "principal_type": PrincipalKind.GROUP}])
    with pytest.raises(InvalidRequestError):
        service.create(draft, ADMIN)


def test_tag_condition_requires_values() -> None:
    with pytest.raises(ValueError):
        TagCondition(tag_key="access_path", values=["  "], match=TagMatchMode.PREFIX)


def test_options_offer_every_governed_tag_and_hide_admin_from_approvers() -> None:
    engine, storage, _sql, _da, identity, _ = _engine()
    service = _service(engine, storage, identity)
    options = service.options(ADMIN)
    assert "Region" in options.tag_keys
    assert options.tag_values["region"] == ["east", "west"]
    assert options.presets["BUILD"]["TABLE"] == ["APPLY TAG", "SELECT", "MODIFY"]
    assert options.presets["ADMIN"]["CATALOG"] == ["ALL PRIVILEGES", "MANAGE"]
    assert "ADMIN" not in service.options(APPROVER).presets


def test_external_locations_and_workspace_objects_are_granted() -> None:
    engine, storage, sql, _da, identity, api = _engine()
    service = _service(engine, storage, identity)
    draft = _draft(
        resources=[
            _object(SecurableKind.EXTERNAL_LOCATION, "landing", PrivilegePreset.BUILD).model_dump(),
            {**_object(SecurableKind.DASHBOARD, "d1").model_dump(), "label": "Claims"},
        ]
    )
    created = service.create(draft, ADMIN)
    preview = service.preview(draft, ADMIN)
    assert [(o.securable_kind, o.label) for o in preview.objects] == [
        (SecurableKind.DASHBOARD, "Claims"),
        (SecurableKind.EXTERNAL_LOCATION, None),
    ]
    state = service.reconcile(created.subscription_id)
    assert state is not None and state.ok
    assert "GRANT READ FILES ON EXTERNAL LOCATION `landing` TO `dac_subscription_ia_build`" in sql.executed
    assert not any("USE CATALOG" in stmt for stmt in sql.executed)
    body = api.bodies["/api/2.0/permissions/dashboards/d1"]
    assert body["access_control_list"] == [
        {"user_name": "owner@example.com", "permission_level": "CAN_MANAGE"},
        {"group_name": "dac_subscription_ia_build", "permission_level": "CAN_RUN"},
    ]


def test_workspace_level_change_is_one_write() -> None:
    engine, storage, _sql, _da, identity, api = _engine()
    service = _service(engine, storage, identity)
    created = service.create(_draft(resources=[_object(SecurableKind.GENIE_SPACE, "g1").model_dump()]), ADMIN)
    service.reconcile(created.subscription_id)
    stored = storage.subscriptions.get_subscription(created.subscription_id)
    assert stored is not None
    stored.resources[0].preset = PrivilegePreset.BUILD
    storage.subscriptions.save_subscription(stored)
    api.bodies.clear()
    state = service.reconcile(created.subscription_id)
    assert state is not None and state.ok
    puts = [c for c in api.workspace_calls if c == ("PUT", "/api/2.0/permissions/genie/g1")]
    assert len(puts) == 2
    assert api.bodies["/api/2.0/permissions/genie/g1"]["access_control_list"][-1] == {
        "group_name": "dac_subscription_ia_build",
        "permission_level": "CAN_EDIT",
    }


def test_approvers_cannot_use_admin_level_or_workspace_objects() -> None:
    engine, storage, _sql, _da, identity, _ = _engine()
    service = _service(engine, storage, identity)
    admin_level = _draft(
        resources=[
            {
                "selector": "TAG_MATCH",
                "conditions": [{"tag_key": "access_path", "values": ["people.ia"], "match": "PREFIX"}],
                "preset": "ADMIN",
            }
        ]
    )
    with pytest.raises(ForbiddenError):
        service.create(admin_level, APPROVER)
    with pytest.raises(ForbiddenError):
        service.create(_draft(resources=[_object(SecurableKind.APP, "claims-app").model_dump()]), APPROVER)
    with pytest.raises(ForbiddenError):
        service.browse(APPROVER, SecurableKind.DASHBOARD, None)

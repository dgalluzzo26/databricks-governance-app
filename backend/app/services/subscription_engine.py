"""Subscription engine: resolve resources and reconcile each subscription group.

For every subscription the engine computes the desired state (members, grants, conferred
attributes) and makes the account group ``dac_subscription_<name>`` match it. While a
subscription is in effect the desired state is what it defines; before it starts, after it
ends, or once it is revoked or denied, the desired state is empty, so members, grants and
attributes are stripped. The engine owns these groups outright, so anything else granted to
them is revoked.

Tag matches use each object's own tags. The review gate reads the object's review tag, or
the closest parent's when the object has none.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.models.data_access import DataAccessSettings
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeAssignmentFilter,
    AttributeSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.subscription import (
    DATA_KINDS,
    UC_PRIVILEGES,
    WORKSPACE_KINDS,
    MatchedObject,
    ResourceSelector,
    SecurableKind,
    Subscription,
    SubscriptionGrant,
    SubscriptionResource,
    SubscriptionSyncResult,
    SubscriptionSyncState,
    TagCondition,
    TagMatchMode,
    privileges_for,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.data_access import DataAccessService
from backend.app.services.data_access_planner import normalize_privilege, subscription_group_prefix
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.workspace_access import WorkspaceGroupAccess

logger = logging.getLogger(__name__)

ENGINE_ACTOR = CurrentUser(
    user_name="subscription-engine",
    email="",
    display_name="Subscription engine",
    is_admin=True,
)
PREVIEW_LIMIT = 500
_ERROR_LIMIT = 20
_SYSTEM_CATALOGS = frozenset({"system", "samples", "__databricks_internal"})
_TAG_TABLES: dict[SecurableKind, tuple[str, str]] = {
    SecurableKind.CATALOG: ("catalog_tags", "NULL, NULL"),
    SecurableKind.SCHEMA: ("schema_tags", "schema_name, NULL"),
    SecurableKind.TABLE: ("table_tags", "schema_name, table_name"),
    SecurableKind.VOLUME: ("volume_tags", "schema_name, volume_name"),
}
# (information_schema table, SQL securable keyword, name columns)
_PRIVILEGE_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("catalog_privileges", "CATALOG", ("catalog_name",)),
    ("schema_privileges", "SCHEMA", ("catalog_name", "schema_name")),
    ("table_privileges", "TABLE", ("table_catalog", "table_schema", "table_name")),
    ("volume_privileges", "VOLUME", ("volume_catalog", "volume_schema", "volume_name")),
    ("routine_privileges", "FUNCTION", ("routine_catalog", "routine_schema", "routine_name")),
    ("external_location_privileges", "EXTERNAL LOCATION", ("external_location_name",)),
)
_KIND_FOR_SECURABLE = {"EXTERNAL LOCATION": SecurableKind.EXTERNAL_LOCATION}
_PERMISSION_PATHS: dict[SecurableKind, str] = {
    SecurableKind.DASHBOARD: "dashboards",
    SecurableKind.GENIE_SPACE: "genie",
    SecurableKind.APP: "apps",
    SecurableKind.SQL_WAREHOUSE: "sql/warehouses",
}


def sql_securable(kind: SecurableKind) -> str:
    """SQL keyword for a securable; registered models live in the function namespace."""
    if kind == SecurableKind.MODEL:
        return "FUNCTION"
    if kind == SecurableKind.EXTERNAL_LOCATION:
        return "EXTERNAL LOCATION"
    return kind.value


def workspace_object_name(kind: SecurableKind, full_name: str) -> str:
    """Canonical name for a workspace object (folder paths lose their trailing slash)."""
    if kind == SecurableKind.WORKSPACE_FOLDER:
        return full_name.rstrip("/") or "/"
    return full_name


def grant_key(grant: SubscriptionGrant) -> tuple[str, str, str]:
    """Comparison key: SQL securable, lower-case name, canonical privilege."""
    if grant.securable_kind in WORKSPACE_KINDS:
        name = workspace_object_name(grant.securable_kind, grant.full_name)
        return (grant.securable_kind.value, name.lower(), grant.privilege)
    return (
        sql_securable(grant.securable_kind),
        grant.full_name.lower(),
        normalize_privilege(grant.privilege),
    )


@dataclass
class TagInventory:
    """Own tags per object for the catalogs a sync looks at.

    Attributes:
        tags: ``(kind, full_name_lower) -> {tag_key_lower: value}``.
        names: ``(kind, full_name_lower) -> display full name``.
    """

    tags: dict[tuple[SecurableKind, str], dict[str, str]] = field(default_factory=dict)
    names: dict[tuple[SecurableKind, str], str] = field(default_factory=dict)

    def own(self, kind: SecurableKind, full_name: str) -> dict[str, str]:
        """Tags set directly on the object."""
        return self.tags.get((kind, full_name.lower()), {})

    def nearest(self, kind: SecurableKind, full_name: str, key: str) -> str | None:
        """The object's own value for ``key``, else the closest parent's."""
        for parent_kind, parent_name in _lineage(kind, full_name):
            value = self.own(parent_kind, parent_name).get(key)
            if value:
                return value
        return None


def _lineage(kind: SecurableKind, full_name: str) -> list[tuple[SecurableKind, str]]:
    """The object followed by its schema and catalog."""
    parts = full_name.split(".")
    chain: list[tuple[SecurableKind, str]] = [(kind, full_name)]
    if len(parts) == 3:
        chain.append((SecurableKind.SCHEMA, ".".join(parts[:2])))
    if len(parts) >= 2:
        chain.append((SecurableKind.CATALOG, parts[0]))
    return chain


def path_within(value: str, prefixes: Iterable[str], separator: str = ".") -> bool:
    """True when ``value`` equals one of ``prefixes`` or sits under one in the hierarchy."""
    v = value.strip().lower()
    for prefix in prefixes:
        p = prefix.strip().lower()
        if p and (v == p or v.startswith(p + separator)):
            return True
    return False


def condition_matches(condition: TagCondition, tags: dict[str, str], separator: str = ".") -> bool:
    """Whether an object's own tags satisfy one tag condition."""
    value = tags.get(condition.tag_key.lower())
    if value is None:
        return False
    if condition.match == TagMatchMode.PREFIX:
        return path_within(value, condition.values, separator)
    return value.strip().lower() in {v.lower() for v in condition.values}


def traversal_grants(kind: SecurableKind, full_name: str) -> list[SubscriptionGrant]:
    """USE CATALOG / USE SCHEMA needed to reach an object inside a catalog."""
    if kind not in DATA_KINDS:
        return []
    parts = full_name.split(".")
    grants = [
        SubscriptionGrant(securable_kind=SecurableKind.CATALOG, full_name=parts[0], privilege="USE CATALOG")
    ]
    if kind == SecurableKind.CATALOG:
        grants.append(
            SubscriptionGrant(
                securable_kind=SecurableKind.CATALOG, full_name=parts[0], privilege="USE SCHEMA"
            )
        )
    else:
        grants.append(
            SubscriptionGrant(
                securable_kind=SecurableKind.SCHEMA, full_name=".".join(parts[:2]), privilege="USE SCHEMA"
            )
        )
    return grants


def object_grants(kind: SecurableKind, full_name: str, privileges: list[str]) -> list[SubscriptionGrant]:
    """Grants for one object: its privileges plus traversal (workspace objects get one level)."""
    if not privileges:
        return []
    if kind in WORKSPACE_KINDS:
        levels = UC_PRIVILEGES[kind]
        level = max((p for p in privileges if p in levels), key=levels.index, default=None)
        if level is None:
            return []
        return [
            SubscriptionGrant(
                securable_kind=kind, full_name=workspace_object_name(kind, full_name), privilege=level
            )
        ]
    own = [SubscriptionGrant(securable_kind=kind, full_name=full_name, privilege=p) for p in privileges]
    return traversal_grants(kind, full_name) + own


@dataclass
class Resolution:
    """What a set of resources reaches right now.

    Attributes:
        objects: Matched objects that will be granted.
        grants: Unique grants including traversal.
        skipped_unreviewed: Matched objects held back by the review gate.
        warnings: Non-blocking issues.
    """

    objects: list[MatchedObject] = field(default_factory=list)
    grants: dict[tuple[str, str, str], SubscriptionGrant] = field(default_factory=dict)
    skipped_unreviewed: int = 0
    warnings: list[str] = field(default_factory=list)


class SubscriptionEngine:
    """Resolves subscription resources and reconciles subscription groups.

    Args:
        storage: App state (subscriptions).
        sql: Warehouse executor running as the Account Admin SP.
        factory: Workspace clients (Account Admin SP for SCIM and workspace permissions).
        data_access: Data Access service (settings, enrolled catalogs, account SCIM helpers).
        identity: Identity service, used to confer attributes so the UC mirror stays current.
        grant_concurrency: Parallel GRANT/REVOKE statements.
    """

    def __init__(
        self,
        storage: StorageProvider,
        sql: SqlExecutor,
        factory: WorkspaceClientFactory,
        data_access: DataAccessService,
        identity: IdentityAttributeService,
        *,
        grant_concurrency: int = 8,
    ) -> None:
        self._storage = storage
        self._sql = sql
        self._factory = factory
        self._data_access = data_access
        self._identity = identity
        self._grant_concurrency = grant_concurrency
        self._lock = threading.Lock()
        # Tests replace these without a warehouse or SCIM.
        self.tag_loader: Callable[[set[str], set[str]], TagInventory] = self._load_tags
        self.grant_loader: Callable[[DataAccessSettings], dict[str, set[tuple[str, str, str]]]] = (
            self._load_actual_grants
        )

    # ------------------------------------------------------------------ settings

    def settings(self) -> DataAccessSettings:
        """Current Data Access settings (prefix, tag keys, review gate)."""
        return self._data_access._settings()  # noqa: SLF001

    def enrolled_catalogs(self) -> list[str]:
        """Catalogs in the governance perimeter."""
        return self._data_access.list_enrolled_catalogs()

    def group_name_for(self, name: str, settings: DataAccessSettings | None = None) -> str:
        """Account group a subscription named ``name`` owns."""
        cfg = settings or self.settings()
        return f"{subscription_group_prefix(cfg.group_prefix)}{name}"

    # ------------------------------------------------------------------ resolution

    def inventory_for(
        self, resources: Iterable[SubscriptionResource], settings: DataAccessSettings
    ) -> TagInventory:
        """Load own tags for enrolled catalogs plus the catalogs of picked objects."""
        keys = {settings.access_tag_key.lower(), settings.review_tag_key.lower()}
        catalogs = {c.lower() for c in self._data_access.list_enrolled_catalogs(settings, fresh=True)}
        for resource in resources:
            for condition in resource.conditions:
                keys.add(condition.tag_key.lower())
            if resource.selector == ResourceSelector.OBJECT and resource.securable_kind in DATA_KINDS:
                catalogs.add((resource.full_name or "").split(".")[0].lower())
        catalogs -= _SYSTEM_CATALOGS
        catalogs.discard("")
        if not catalogs:
            return TagInventory()
        return self.tag_loader(catalogs, keys)

    def resolve(
        self,
        resources: list[SubscriptionResource],
        *,
        review_exempt: bool,
        settings: DataAccessSettings,
        inventory: TagInventory | None = None,
    ) -> Resolution:
        """Objects and grants the resources reach now.

        Args:
            resources: Subscription resources.
            review_exempt: Skip the review gate.
            settings: Data Access settings (tag keys, review value, separator).
            inventory: Pre-loaded tags; loaded when None.
        """
        inv = inventory if inventory is not None else self.inventory_for(resources, settings)
        access_key = settings.access_tag_key.lower()
        review_key = settings.review_tag_key.lower()
        review_value = settings.review_tag_value.strip().lower()
        separator = settings.hierarchy_separator
        out = Resolution()
        seen: dict[tuple[SecurableKind, str], MatchedObject] = {}

        def reviewed(kind: SecurableKind, name: str) -> bool:
            if review_exempt or kind not in DATA_KINDS:
                return True
            return (inv.nearest(kind, name, review_key) or "").strip().lower() == review_value

        def add(kind: SecurableKind, name: str, resource: SubscriptionResource) -> None:
            privileges = privileges_for(kind, resource.preset, resource.privileges)
            if not privileges:
                return
            if not reviewed(kind, name):
                out.skipped_unreviewed += 1
                return
            key = (kind, name.lower())
            existing = seen.get(key)
            if existing is not None:
                existing.privileges = list(dict.fromkeys([*existing.privileges, *privileges]))
            else:
                access = inv.nearest(kind, name, access_key) if kind in DATA_KINDS else None
                seen[key] = MatchedObject(
                    securable_kind=kind,
                    full_name=name,
                    label=resource.label if resource.selector == ResourceSelector.OBJECT else None,
                    privileges=privileges,
                    access_path=access,
                    reviewed=True,
                )
            for grant in object_grants(kind, name, privileges):
                out.grants.setdefault(grant_key(grant), grant)

        for resource in resources:
            if resource.selector == ResourceSelector.OBJECT:
                kind = resource.securable_kind
                name = resource.full_name or ""
                if kind is None:
                    continue
                if kind in DATA_KINDS and not inv.nearest(kind, name, access_key):
                    out.warnings.append(f"{name} has no {settings.access_tag_key} tag")
                add(kind, name, resource)
                continue
            wanted = set(resource.levels)
            for (kind, lower_name), tags in inv.tags.items():
                if kind not in wanted:
                    continue
                if all(condition_matches(c, tags, separator) for c in resource.conditions):
                    add(kind, inv.names[(kind, lower_name)], resource)

        out.objects = sorted(seen.values(), key=lambda o: (o.securable_kind.value, o.full_name.lower()))
        return out

    # ------------------------------------------------------------------ sync

    def sync_all(self, now: datetime | None = None) -> SubscriptionSyncResult:
        """Reconcile every subscription; one failure never stops the rest."""
        with self._lock:
            moment = now or datetime.now(UTC)
            settings = self.settings()
            subscriptions = self._storage.subscriptions.list_subscriptions()
            result = SubscriptionSyncResult()
            if not subscriptions:
                return result
            active_resources = [r for s in subscriptions if s.in_effect(moment) for r in s.resources]
            inventory = self.inventory_for(active_resources, settings) if active_resources else TagInventory()
            actual = self.grant_loader(settings)
            for subscription in subscriptions:
                state = self._sync_locked(subscription, settings, inventory, actual, moment)
                result.processed += 1
                result.active += int(state.active)
                result.grants_added += state.grants_added
                result.grants_revoked += state.grants_revoked
                result.members_added += state.members_added
                result.members_removed += state.members_removed
                if not state.ok:
                    result.failed += 1
                    result.errors.extend(f"{subscription.name}: {e}" for e in state.errors[:5])
            result.errors = result.errors[:50]
            return result

    def sync_one(self, subscription_id: str, now: datetime | None = None) -> SubscriptionSyncState | None:
        """Reconcile one subscription (after approve, revoke, or an admin's Sync now)."""
        with self._lock:
            subscription = self._storage.subscriptions.get_subscription(subscription_id)
            if subscription is None:
                return None
            moment = now or datetime.now(UTC)
            settings = self.settings()
            inventory = (
                self.inventory_for(subscription.resources, settings)
                if subscription.in_effect(moment)
                else TagInventory()
            )
            actual = self.grant_loader(settings)
            return self._sync_locked(subscription, settings, inventory, actual, moment)

    def _sync_locked(
        self,
        subscription: Subscription,
        settings: DataAccessSettings,
        inventory: TagInventory,
        actual_by_group: dict[str, set[tuple[str, str, str]]],
        moment: datetime,
    ) -> SubscriptionSyncState:
        active = subscription.in_effect(moment)
        errors: list[str] = []
        state = SubscriptionSyncState(synced_at=moment, active=active)
        group = subscription.group_name
        try:
            if active:
                resolution = self.resolve(
                    subscription.resources,
                    review_exempt=subscription.review_exempt,
                    settings=settings,
                    inventory=inventory,
                )
                desired = resolution.grants
                state.matched_objects = len(resolution.objects)
            else:
                desired = {}
            group_id = self._find_group_id(group)
            if group_id is None and active:
                group_id = self._data_access._account_scim_create_group(group)  # noqa: SLF001
                logger.info("Created subscription group %s", group)
            if group_id is not None:
                state.members_added, state.members_removed, state.members = self._sync_members(
                    group_id, subscription.subjects if active else [], errors
                )
                if active:
                    WorkspaceGroupAccess(lambda: self._factory.admin().api_client).ensure(
                        {group: group_id}, settings.workspace_entitlements, errors
                    )
            held = self._held_grants(subscription, actual_by_group.get(group.lower(), set()))
            to_add = [g for k, g in desired.items() if k not in held]
            to_revoke = [g for k, g in held.items() if k not in desired]
            failed = self._apply_grants(group, to_add, to_revoke, errors) if group_id is not None else set()
            state.grants_added = len(to_add) - len({k for k in failed if k[0] == "GRANT"})
            state.grants_revoked = len(to_revoke) - len({k for k in failed if k[0] == "REVOKE"})
            kept = {k: g for k, g in desired.items() if ("GRANT", *k) not in failed}
            kept.update({k: g for k, g in held.items() if ("REVOKE", *k) in failed})
            state.grants = sorted(kept.values(), key=lambda g: grant_key(g))
            self._sync_attributes(subscription, active, errors)
        except Exception as exc:  # noqa: BLE001 - record and move on to the next subscription
            logger.exception("Subscription %s sync failed", subscription.name)
            errors.append(str(exc))
        state.errors = errors[:_ERROR_LIMIT]
        state.ok = not errors
        latest = self._storage.subscriptions.get_subscription(subscription.subscription_id) or subscription
        self._storage.subscriptions.save_subscription(latest.model_copy(update={"last_sync": state}))
        return state

    # ------------------------------------------------------------------ members

    def _find_group_id(self, group: str) -> str | None:
        return self._data_access._find_group_id(group)  # noqa: SLF001

    def _group_member_ids(self, group_id: str) -> set[str]:
        admin = self._factory.admin()
        payload = admin.api_client.do(
            "GET",
            f"{DataAccessService._ACCOUNT_SCIM_GROUPS}/{group_id}",  # noqa: SLF001
            query={"attributes": "id,displayName,members"},
        )
        members = payload.get("members") if isinstance(payload, dict) else None
        return {str(m.get("value")) for m in members or [] if isinstance(m, dict) and m.get("value")}

    def _sync_members(self, group_id: str, subjects: list[Any], errors: list[str]) -> tuple[int, int, int]:
        """Make the group's members exactly ``subjects``; returns (added, removed, total)."""
        desired: set[str] = set()
        for subject in subjects:
            member_id = self._data_access._resolve_principal_id(  # noqa: SLF001
                subject.principal_name, subject.principal_type.value, errors
            )
            if member_id:
                desired.add(member_id)
        current = self._group_member_ids(group_id)
        added = removed = 0
        for member_id in sorted(desired - current):
            try:
                self._data_access._patch_member_add(group_id, member_id)  # noqa: SLF001
                added += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"add member {member_id}: {exc}")
        for member_id in sorted(current - desired):
            try:
                self._data_access._patch_member_remove(group_id, member_id)  # noqa: SLF001
                removed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"remove member {member_id}: {exc}")
        return added, removed, len(desired)

    # ------------------------------------------------------------------ grants

    def _held_grants(
        self, subscription: Subscription, actual: set[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], SubscriptionGrant]:
        """Grants the group holds now.

        UC grants come from information_schema; workspace object permissions are not visible
        there, so the ones recorded by the last run are used.
        """
        held: dict[tuple[str, str, str], SubscriptionGrant] = {}
        for securable, name, privilege in actual:
            kind = _KIND_FOR_SECURABLE.get(securable) or SecurableKind(securable)
            held[(securable, name, privilege)] = SubscriptionGrant(
                securable_kind=kind, full_name=name, privilege=privilege
            )
        recorded = subscription.last_sync.grants if subscription.last_sync else []
        for grant in recorded:
            if grant.securable_kind in WORKSPACE_KINDS:
                held.setdefault(grant_key(grant), grant)
        return held

    def _load_actual_grants(self, settings: DataAccessSettings) -> dict[str, set[tuple[str, str, str]]]:
        """Direct UC grants held by any subscription group, keyed by lower-case group name."""
        prefix = subscription_group_prefix(settings.group_prefix)
        like = string_literal(prefix.replace("_", r"\_") + "%")
        out: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        for table, securable, columns in _PRIVILEGE_TABLES:
            name_sql = " || '.' || ".join(columns)
            rows = self._sql.query(
                f"""
                SELECT grantee, {name_sql} AS full_name, privilege_type, inherited_from
                FROM system.information_schema.{table}
                WHERE lower(grantee) LIKE {like} ESCAPE '\\\\'
                """
            )
            for row in rows:
                inherited = str(row.get("inherited_from") or "").strip().upper()
                if inherited and inherited != "NONE":
                    continue
                out[str(row["grantee"]).lower()].add(
                    (
                        securable,
                        str(row["full_name"]).lower(),
                        normalize_privilege(str(row["privilege_type"])),
                    )
                )
        return out

    def _apply_grants(
        self,
        group: str,
        to_add: list[SubscriptionGrant],
        to_revoke: list[SubscriptionGrant],
        errors: list[str],
    ) -> set[tuple[str, str, str, str]]:
        """Issue grants and revokes; returns ``(verb, *grant_key)`` of failures."""
        jobs = [("GRANT", g) for g in to_add] + [("REVOKE", g) for g in to_revoke]
        failed: set[tuple[str, str, str, str]] = set()
        if not jobs:
            return failed
        uc_jobs = [j for j in jobs if j[1].securable_kind not in WORKSPACE_KINDS]
        # Workspace objects hold one level per principal: set the final level in one write.
        workspace: dict[tuple[SecurableKind, str], list[tuple[str, SubscriptionGrant]]] = defaultdict(list)
        for job in jobs:
            grant = job[1]
            if grant.securable_kind in WORKSPACE_KINDS:
                name = workspace_object_name(grant.securable_kind, grant.full_name)
                workspace[(grant.securable_kind, name.lower())].append(job)

        def run(job: tuple[str, SubscriptionGrant]) -> Exception | None:
            verb, grant = job
            try:
                self._sql.execute(self._grant_sql(verb, grant, group))
                return None
            except Exception as exc:  # noqa: BLE001 - one failed grant must not stop the rest
                return exc

        if uc_jobs:
            workers = max(1, min(self._grant_concurrency, len(uc_jobs)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sub-grant") as pool:
                futures = {pool.submit(run, job): job for job in uc_jobs}
                for future in as_completed(futures):
                    verb, grant = futures[future]
                    exc = future.result()
                    if exc is not None:
                        errors.append(f"{verb} {grant.privilege} on {grant.full_name}: {exc}")
                        failed.add((verb, *grant_key(grant)))
        for object_jobs in workspace.values():
            grant = object_jobs[0][1]
            level = next((g.privilege for verb, g in object_jobs if verb == "GRANT"), None)
            try:
                self._set_workspace_permission(grant.securable_kind, grant.full_name, group, level)
            except Exception as exc:  # noqa: BLE001
                label = grant.securable_kind.value.replace("_", " ").lower()
                errors.append(f"{level or 'Remove access'} on {label} {grant.full_name}: {exc}")
                for verb, g in object_jobs:
                    failed.add((verb, *grant_key(g)))
        return failed

    @staticmethod
    def _grant_sql(verb: str, grant: SubscriptionGrant, group: str) -> str:
        if grant.securable_kind == SecurableKind.EXTERNAL_LOCATION:
            quoted = quote_identifier(grant.full_name)
        else:
            quoted = ".".join(quote_identifier(p) for p in grant.full_name.split("."))
        principal = f"`{group.replace('`', '')}`"
        securable = sql_securable(grant.securable_kind)
        privilege = normalize_privilege(grant.privilege)
        if verb == "GRANT":
            return f"GRANT {privilege} ON {securable} {quoted} TO {principal}"
        return f"REVOKE {privilege} ON {securable} {quoted} FROM {principal}"

    def _permissions_url(self, api: Any, kind: SecurableKind, name: str) -> str:
        if kind == SecurableKind.WORKSPACE_FOLDER:
            status = api.do("GET", "/api/2.0/workspace/get-status", query={"path": name})
            object_id = status.get("object_id") if isinstance(status, dict) else None
            if not object_id:
                raise RuntimeError(f"folder {name} not found")
            return f"/api/2.0/permissions/directories/{object_id}"
        return f"/api/2.0/permissions/{_PERMISSION_PATHS[kind]}/{name}"

    def _set_workspace_permission(
        self, kind: SecurableKind, name: str, group: str, level: str | None
    ) -> None:
        """Set the group's direct permission on a workspace object; ``level=None`` removes it.

        Other principals' direct permissions are written back unchanged; inherited ones are
        left to the parent.
        """
        api = self._factory.admin().api_client
        url = self._permissions_url(api, kind, name)
        current = api.do("GET", url)
        keep: list[dict[str, str]] = []
        for entry in (current.get("access_control_list") if isinstance(current, dict) else None) or []:
            if str(entry.get("group_name") or "").lower() == group.lower():
                continue
            principal = {
                k: entry[k] for k in ("user_name", "group_name", "service_principal_name") if entry.get(k)
            }
            for permission in entry.get("all_permissions") or []:
                if permission.get("inherited"):
                    continue
                keep.append({**principal, "permission_level": permission["permission_level"]})
        if level is not None:
            keep.append({"group_name": group, "permission_level": level})
        api.do("PUT", url, body={"access_control_list": keep})

    # ------------------------------------------------------------------ attributes

    def _sync_attributes(self, subscription: Subscription, active: bool, errors: list[str]) -> None:
        """Hold the conferred attribute values on the group only while active."""
        held = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(
                principal_name=subscription.group_name,
                principal_type=PrincipalKind.GROUP,
                include_inactive=False,
            )
        )
        wanted = (
            {(a.attribute_key.lower(), a.attribute_value.lower()): a for a in subscription.attributes}
            if active
            else {}
        )
        for item in held:
            if item.source != AttributeSource.SUBSCRIPTION:
                continue
            if (item.attribute_key.lower(), item.attribute_value.lower()) not in wanted:
                try:
                    self._identity.revoke(item.assignment_id, ENGINE_ACTOR)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"revoke attribute {item.attribute_key}={item.attribute_value}: {exc}")
        for attribute in wanted.values():
            try:
                self._identity.assign(
                    AssignAttributeRequest(
                        principal_name=subscription.group_name,
                        principal_type=PrincipalKind.GROUP,
                        attribute_key=attribute.attribute_key,
                        attribute_value=attribute.attribute_value,
                        justification=f"Subscription {subscription.name}",
                        expires_at=subscription.expires_at,
                    ),
                    ENGINE_ACTOR,
                    source=AttributeSource.SUBSCRIPTION,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"confer attribute {attribute.attribute_key}={attribute.attribute_value}: {exc}"
                )

    # ------------------------------------------------------------------ inventory

    def _load_tags(self, catalogs: set[str], keys: set[str]) -> TagInventory:
        """Own tags for the given catalogs and tag keys (catalogs, schemas, tables, volumes)."""
        catalog_list = ", ".join(string_literal(c) for c in sorted(catalogs))
        key_list = ", ".join(string_literal(k) for k in sorted(keys))
        parts = []
        for kind, (table, columns) in _TAG_TABLES.items():
            parts.append(
                f"""
                SELECT '{kind.value}' AS kind, catalog_name, {columns.split(", ")[0]} AS schema_name,
                       {columns.split(", ")[1]} AS object_name, tag_name, tag_value
                FROM system.information_schema.{table}
                WHERE lower(catalog_name) IN ({catalog_list}) AND lower(tag_name) IN ({key_list})
                """
            )
        started = time.perf_counter()
        rows = self._sql.query(" UNION ALL ".join(parts))
        logger.info("Subscription tag inventory: %d rows in %.1fs", len(rows), time.perf_counter() - started)
        inventory = TagInventory()
        for row in rows:
            kind = SecurableKind(str(row["kind"]))
            name = ".".join(
                str(row[c]) for c in ("catalog_name", "schema_name", "object_name") if row.get(c) is not None
            )
            key = (kind, name.lower())
            inventory.names[key] = name
            inventory.tags.setdefault(key, {})[str(row["tag_name"]).lower()] = str(row.get("tag_value") or "")
        return inventory

"""Subscriptions: create, approve, extend, revoke, preview, and browse objects.

Admins may create anything, and their subscriptions are approved on creation. Approvers may
create subscriptions only over access paths they approve: tag matches must be limited by the
access tag to their paths, picked objects must carry an access tag under their paths, and
conferred attributes must be values they can grant. Workspace objects (folders, dashboards,
Genie spaces, apps, warehouses), external locations, the Admin preset (ALL PRIVILEGES /
MANAGE) and the review-gate waiver are admin-only. Whether an approver's subscription waits
for an admin is an app setting.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.errors import ForbiddenError, InvalidRequestError, NotFoundError
from backend.app.models.audit import AuditEvent
from backend.app.models.principal import PrincipalKind
from backend.app.models.subscription import (
    ADMIN_ONLY_PRIVILEGES,
    DATA_KINDS,
    UC_PRIVILEGES,
    PrivilegePreset,
    ResourceSelector,
    SecurableKind,
    Subscription,
    SubscriptionDecision,
    SubscriptionDraft,
    SubscriptionList,
    SubscriptionOptions,
    SubscriptionPreview,
    SubscriptionResource,
    SubscriptionStatus,
    SubscriptionSyncResult,
    SubscriptionSyncState,
    TagMatchMode,
    privileges_for,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.access_requests import AccessRequestService
from backend.app.services.app_settings import AppSettingsService
from backend.app.services.data_access_planner import is_managed_group, subscription_group_prefix
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.subscription_engine import (
    PREVIEW_LIMIT,
    SubscriptionEngine,
    path_within,
)

logger = logging.getLogger(__name__)

_GROUP_NAME_MAX = 128
_BROWSE_LIMIT = 500


class SubscriptionService:
    """Subscription lifecycle and the wizard's supporting reads.

    Args:
        storage: App state (subscriptions, audit).
        engine: Resolves resources and reconciles groups.
        identity: Attribute definitions (validate conferred attributes).
        access_requests: Approver scopes.
        app_settings: Approval flag and maximum duration.
        factory: Workspace clients for browsing objects (Account Admin SP).
    """

    def __init__(
        self,
        storage: StorageProvider,
        engine: SubscriptionEngine,
        identity: IdentityAttributeService,
        access_requests: AccessRequestService,
        app_settings: AppSettingsService,
        factory: Any,
    ) -> None:
        self._storage = storage
        self._engine = engine
        self._identity = identity
        self._access_requests = access_requests
        self._app_settings = app_settings
        self._factory = factory

    # ------------------------------------------------------------------ reads

    def options(self, actor: CurrentUser) -> SubscriptionOptions:
        """What the wizard offers the signed-in user."""
        app = self._app_settings.get_settings()
        settings = self._engine.settings()
        can_create = actor.is_admin or actor.is_approver
        definitions = self._identity.list_definitions().definitions
        keys = {settings.access_tag_key, settings.review_tag_key} | {
            k for d in definitions for k in d.governed_tag_keys
        }
        tag_values: dict[str, list[str]] = {}
        try:
            for tag in self._identity.all_governed_tags():
                keys.add(tag.tag_key)
                tag_values[tag.tag_key.lower()] = sorted(tag.values, key=str.lower)
        except Exception:  # noqa: BLE001 - the wizard still works with free-text values
            logger.warning("Governed tags unavailable for subscription options", exc_info=True)
        for definition in definitions:
            if len(definition.governed_tag_keys) == 1 and definition.allowed_values:
                values = tag_values.setdefault(definition.governed_tag_keys[0].lower(), [])
                values.extend(v for v in definition.allowed_values if v not in values)
        tag_keys = sorted({k.lower(): k for k in keys}.values(), key=str.lower)
        presets = {
            preset.value: {kind.value: privileges_for(kind, preset, []) for kind in SecurableKind}
            for preset in PrivilegePreset
            if preset != PrivilegePreset.CUSTOM and (actor.is_admin or preset != PrivilegePreset.ADMIN)
        }
        return SubscriptionOptions(
            can_create=can_create,
            is_admin=actor.is_admin,
            requires_approval=(not actor.is_admin) and app.subscriptions_require_admin_approval,
            max_days=app.subscription_max_days,
            group_prefix=subscription_group_prefix(settings.group_prefix),
            access_tag_key=settings.access_tag_key,
            allowed_paths=[] if actor.is_admin else self._allowed_paths(actor),
            tag_keys=tag_keys,
            tag_values=tag_values,
            privileges={k.value: list(v) for k, v in UC_PRIVILEGES.items()},
            presets=presets,
        )

    def list(self, actor: CurrentUser) -> SubscriptionList:
        """Admins see every subscription; approvers see the ones they created."""
        self._require_creator(actor)
        items = self._storage.subscriptions.list_subscriptions()
        if not actor.is_admin:
            items = [s for s in items if s.created_by.lower() == actor.user_name.lower()]
        return SubscriptionList(subscriptions=items, total=len(items))

    def get(self, subscription_id: str, actor: CurrentUser) -> Subscription:
        """One subscription the caller may see."""
        subscription = self._storage.subscriptions.get_subscription(subscription_id)
        if subscription is None or not self._can_see(subscription, actor):
            raise NotFoundError(f"Subscription {subscription_id} was not found")
        return subscription

    def preview(self, draft: SubscriptionDraft, actor: CurrentUser) -> SubscriptionPreview:
        """What a draft would reach right now (does not save or check name uniqueness)."""
        self._require_creator(actor)
        settings = self._engine.settings()
        inventory = self._engine.inventory_for(draft.resources, settings)
        self._check_scope(draft, actor, inventory)
        resolution = self._engine.resolve(
            draft.resources,
            review_exempt=draft.review_exempt and actor.is_admin,
            settings=settings,
            inventory=inventory,
        )
        return SubscriptionPreview(
            group_name=self._engine.group_name_for(draft.name, settings),
            objects=resolution.objects[:PREVIEW_LIMIT],
            total_objects=len(resolution.objects),
            skipped_unreviewed=resolution.skipped_unreviewed,
            grant_count=len(resolution.grants),
            truncated=len(resolution.objects) > PREVIEW_LIMIT,
            warnings=resolution.warnings[:20],
        )

    # ------------------------------------------------------------------ writes

    def create(self, draft: SubscriptionDraft, actor: CurrentUser) -> Subscription:
        """Create a subscription; admins' are approved at once, approvers' may wait."""
        self._require_creator(actor)
        if self._storage.subscriptions.get_by_name(draft.name) is not None:
            raise InvalidRequestError(f"A subscription named '{draft.name}' already exists")
        settings = self._engine.settings()
        group = self._engine.group_name_for(draft.name, settings)
        if len(group) > _GROUP_NAME_MAX:
            raise InvalidRequestError(
                f"Name is too long; the group name must be at most {_GROUP_NAME_MAX} characters"
            )
        starts_at, expires_at = self._check_window(draft.starts_at, draft.expires_at)
        self._validate(draft, actor)
        now = datetime.now(UTC)
        app = self._app_settings.get_settings()
        pending = not actor.is_admin and app.subscriptions_require_admin_approval
        subscription = Subscription(
            subscription_id=str(uuid.uuid4()),
            name=draft.name,
            display_name=draft.display_name,
            description=draft.description,
            group_name=group,
            status=SubscriptionStatus.PENDING_APPROVAL if pending else SubscriptionStatus.APPROVED,
            starts_at=starts_at,
            expires_at=expires_at,
            review_exempt=draft.review_exempt and actor.is_admin,
            subjects=draft.subjects,
            resources=draft.resources,
            attributes=draft.attributes,
            created_by=actor.user_name,
            created_at=now,
            updated_at=now,
            decided_by=None if pending else actor.user_name,
            decided_at=None if pending else now,
        )
        self._storage.subscriptions.save_subscription(subscription)
        self._audit("subscription.created", actor, subscription, {"status": subscription.status.value})
        return subscription

    def update(self, subscription_id: str, draft: SubscriptionDraft, actor: CurrentUser) -> Subscription:
        """Edit who, what, and when. Admins edit any; creators only while pending."""
        current = self.get(subscription_id, actor)
        if not actor.is_admin and current.status != SubscriptionStatus.PENDING_APPROVAL:
            raise ForbiddenError("Only app admins can edit a subscription after it is decided")
        if current.status in (SubscriptionStatus.DENIED, SubscriptionStatus.REVOKED):
            raise InvalidRequestError("Denied or revoked subscriptions cannot be edited")
        starts_at, expires_at = self._check_window(
            draft.starts_at or current.starts_at, draft.expires_at, allow_past_start=True
        )
        self._validate(draft, actor)
        updated = current.model_copy(
            update={
                "display_name": draft.display_name,
                "description": draft.description,
                "starts_at": starts_at,
                "expires_at": expires_at,
                "review_exempt": draft.review_exempt if actor.is_admin else current.review_exempt,
                "subjects": draft.subjects,
                "resources": draft.resources,
                "attributes": draft.attributes,
                "updated_at": datetime.now(UTC),
            }
        )
        self._storage.subscriptions.save_subscription(updated)
        self._audit("subscription.updated", actor, updated, {})
        return updated

    def decide(self, subscription_id: str, body: SubscriptionDecision, actor: CurrentUser) -> Subscription:
        """Approve or deny a pending subscription (app admins)."""
        self._require_admin(actor)
        current = self.get(subscription_id, actor)
        if current.status != SubscriptionStatus.PENDING_APPROVAL:
            raise InvalidRequestError("Only pending subscriptions can be approved or denied")
        now = datetime.now(UTC)
        if body.approve and _utc(current.expires_at) <= now:
            raise InvalidRequestError("This subscription has already passed its end time; extend it first")
        updated = current.model_copy(
            update={
                "status": SubscriptionStatus.APPROVED if body.approve else SubscriptionStatus.DENIED,
                "decided_by": actor.user_name,
                "decided_at": now,
                "decision_note": (body.note or "").strip() or None,
                "updated_at": now,
            }
        )
        self._storage.subscriptions.save_subscription(updated)
        self._audit(
            "subscription.approved" if body.approve else "subscription.denied",
            actor,
            updated,
            {"note": updated.decision_note},
        )
        return updated

    def extend(self, subscription_id: str, expires_at: datetime, actor: CurrentUser) -> Subscription:
        """Move the end time (app admins, or the creator while pending)."""
        current = self.get(subscription_id, actor)
        if not actor.is_admin and current.status != SubscriptionStatus.PENDING_APPROVAL:
            raise ForbiddenError("Only app admins can extend a decided subscription")
        if current.status in (SubscriptionStatus.DENIED, SubscriptionStatus.REVOKED):
            raise InvalidRequestError("Denied or revoked subscriptions cannot be extended")
        _, new_end = self._check_window(current.starts_at, expires_at, allow_past_start=True)
        updated = current.model_copy(update={"expires_at": new_end, "updated_at": datetime.now(UTC)})
        self._storage.subscriptions.save_subscription(updated)
        self._audit("subscription.extended", actor, updated, {"expires_at": new_end.isoformat()})
        return updated

    def revoke(self, subscription_id: str, actor: CurrentUser) -> Subscription:
        """End a subscription now (app admins or its creator)."""
        current = self.get(subscription_id, actor)
        if not (actor.is_admin or current.created_by.lower() == actor.user_name.lower()):
            raise ForbiddenError("Only app admins or the creator can revoke this subscription")
        if current.status in (SubscriptionStatus.DENIED, SubscriptionStatus.REVOKED):
            return current
        now = datetime.now(UTC)
        updated = current.model_copy(
            update={
                "status": SubscriptionStatus.REVOKED,
                "revoked_by": actor.user_name,
                "revoked_at": now,
                "updated_at": now,
            }
        )
        self._storage.subscriptions.save_subscription(updated)
        self._audit("subscription.revoked", actor, updated, {})
        return updated

    def delete(self, subscription_id: str, actor: CurrentUser) -> None:
        """Delete a subscription that no longer holds access (app admins)."""
        self._require_admin(actor)
        current = self.get(subscription_id, actor)
        if current.status == SubscriptionStatus.APPROVED and current.in_effect():
            raise InvalidRequestError("Revoke the subscription before deleting it")
        if current.last_sync is not None and (current.last_sync.active or current.last_sync.grants):
            raise InvalidRequestError(
                "Sync the subscription after revoking it so its access is removed first"
            )
        self._storage.subscriptions.delete_subscription(subscription_id)
        self._audit("subscription.deleted", actor, current, {})

    def sync_now(self, subscription_id: str, actor: CurrentUser) -> SubscriptionSyncState:
        """Reconcile one subscription immediately (app admins or its creator)."""
        current = self.get(subscription_id, actor)
        if not (actor.is_admin or current.created_by.lower() == actor.user_name.lower()):
            raise ForbiddenError("Only app admins or the creator can sync this subscription")
        state = self._engine.sync_one(subscription_id)
        if state is None:
            raise NotFoundError(f"Subscription {subscription_id} was not found")
        return state

    def reconcile(self, subscription_id: str) -> SubscriptionSyncState | None:
        """Reconcile one subscription without a permission check (callers already checked)."""
        return self._engine.sync_one(subscription_id)

    def sync_all(self) -> SubscriptionSyncResult:
        """Reconcile every subscription (the scheduled job task)."""
        return self._engine.sync_all()

    # ------------------------------------------------------------------ browse

    def browse(self, actor: CurrentUser, kind: SecurableKind, parent: str | None) -> list[dict[str, str]]:
        """Names of objects of ``kind`` under ``parent`` for the object picker."""
        self._require_creator(actor)
        if kind not in DATA_KINDS and not actor.is_admin:
            raise ForbiddenError(f"Only app admins can grant {_kind_label(kind)}s")
        client = self._factory.admin()
        names: list[dict[str, str]] = []

        def take(
            items: Any, attr: str = "full_name", label: str | None = None, comment: str = "comment"
        ) -> None:
            for item in items:
                value = getattr(item, attr, None)
                if value:
                    entry = {"name": str(value), "comment": str(getattr(item, comment, "") or "")}
                    if label and getattr(item, label, None):
                        entry["label"] = str(getattr(item, label))
                    names.append(entry)
                if len(names) >= _BROWSE_LIMIT:
                    break

        parts = (parent or "").split(".") if parent else []
        if kind == SecurableKind.CATALOG:
            take(client.catalogs.list(), "name")
            if not actor.is_admin:
                enrolled = {c.lower() for c in self._engine.enrolled_catalogs()}
                names = [n for n in names if n["name"].lower() in enrolled]
        elif kind == SecurableKind.SCHEMA:
            self._require_parts(parts, 1)
            take(client.schemas.list(catalog_name=parts[0]))
        elif kind == SecurableKind.TABLE:
            self._require_parts(parts, 2)
            take(client.tables.list(catalog_name=parts[0], schema_name=parts[1]))
        elif kind == SecurableKind.VOLUME:
            self._require_parts(parts, 2)
            take(client.volumes.list(catalog_name=parts[0], schema_name=parts[1]))
        elif kind == SecurableKind.FUNCTION:
            self._require_parts(parts, 2)
            take(client.functions.list(catalog_name=parts[0], schema_name=parts[1]))
        elif kind == SecurableKind.MODEL:
            self._require_parts(parts, 2)
            take(client.registered_models.list(catalog_name=parts[0], schema_name=parts[1]))
        elif kind == SecurableKind.EXTERNAL_LOCATION:
            take(client.external_locations.list(), "name", comment="url")
        elif kind == SecurableKind.DASHBOARD:
            take(client.lakeview.list(), "dashboard_id", "display_name", comment="path")
        elif kind == SecurableKind.GENIE_SPACE:
            take(self._genie_spaces(client), "space_id", "title", comment="description")
        elif kind == SecurableKind.APP:
            take(client.apps.list(), "name", comment="description")
        elif kind == SecurableKind.SQL_WAREHOUSE:
            take(client.warehouses.list(), "id", "name", comment="cluster_size")
        else:
            path = parent or "/"
            for item in client.workspace.list(path):
                if str(getattr(item, "object_type", "")).endswith("DIRECTORY"):
                    names.append({"name": str(item.path), "comment": ""})
                if len(names) >= _BROWSE_LIMIT:
                    break
        names.sort(key=lambda n: n.get("label", n["name"]).lower())
        return names

    @staticmethod
    def _genie_spaces(client: Any) -> Any:
        """Every Genie space, following page tokens."""
        token: str | None = None
        while True:
            page = client.genie.list_spaces(page_size=100, page_token=token)
            yield from page.spaces or []
            token = page.next_page_token
            if not token:
                return

    @staticmethod
    def _require_parts(parts: list[str], count: int) -> None:
        if len(parts) != count or any(not p for p in parts):
            raise InvalidRequestError(f"parent must have {count} dot-separated part(s)")

    # ------------------------------------------------------------------ validation

    def _check_window(
        self, starts_at: datetime | None, expires_at: datetime, *, allow_past_start: bool = False
    ) -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        start = _utc(starts_at) if starts_at is not None else now
        end = _utc(expires_at)
        if not allow_past_start and start < now - timedelta(minutes=5):
            start = now
        if end <= now:
            raise InvalidRequestError("The end time must be in the future")
        if end <= start:
            raise InvalidRequestError("The end time must be after the start time")
        max_days = self._app_settings.get_settings().subscription_max_days
        if end - start > timedelta(days=max_days):
            raise InvalidRequestError(f"Subscriptions can last at most {max_days} days")
        return start, end

    def _validate(self, draft: SubscriptionDraft, actor: CurrentUser) -> None:
        settings = self._engine.settings()
        if draft.review_exempt and not actor.is_admin:
            raise ForbiddenError("Only app admins can waive the review gate")
        if not actor.is_admin:
            for resource in draft.resources:
                chosen = set(resource.privileges) if resource.preset == PrivilegePreset.CUSTOM else set()
                if resource.preset == PrivilegePreset.ADMIN or chosen & ADMIN_ONLY_PRIVILEGES:
                    raise ForbiddenError("Only app admins can grant ALL PRIVILEGES or MANAGE")
        seen: set[tuple[str, str]] = set()
        for subject in draft.subjects:
            key = (subject.principal_type.value, subject.principal_name.lower())
            if key in seen:
                raise InvalidRequestError(f"{subject.principal_name} is listed twice")
            seen.add(key)
            if subject.principal_type == PrincipalKind.GROUP and is_managed_group(
                subject.principal_name, settings.group_prefix
            ):
                raise InvalidRequestError(
                    f"'{subject.principal_name}' is an app-managed group; add people or real groups instead"
                )
        for attribute in draft.attributes:
            definition = self._identity.get_definition(attribute.attribute_key)
            allowed = {v.lower() for v in (definition.allowed_values or [])}
            if allowed and attribute.attribute_value.lower() not in allowed:
                raise InvalidRequestError(
                    f"'{attribute.attribute_value}' is not an allowed value for {attribute.attribute_key}"
                )
        inventory = self._engine.inventory_for(draft.resources, settings) if not actor.is_admin else None
        self._check_scope(draft, actor, inventory)

    def _check_scope(self, draft: SubscriptionDraft, actor: CurrentUser, inventory: Any) -> None:
        """Approvers may only reach access paths they approve."""
        if actor.is_admin:
            return
        settings = self._engine.settings()
        separator = settings.hierarchy_separator
        allowed = self._allowed_paths(actor)
        if not allowed:
            raise ForbiddenError("You are not an approver for any access path")
        access_key = settings.access_tag_key.lower()
        for resource in draft.resources:
            self._check_resource_scope(resource, access_key, allowed, separator, inventory)
        for attribute in draft.attributes:
            if not self._access_requests._can_decide_for(  # noqa: SLF001
                actor, attribute.attribute_value, attribute_key=attribute.attribute_key
            ):
                raise ForbiddenError(
                    f"You cannot grant {attribute.attribute_key} = {attribute.attribute_value}"
                )

    def _check_resource_scope(
        self,
        resource: SubscriptionResource,
        access_key: str,
        allowed: list[str],
        separator: str,
        inventory: Any,
    ) -> None:
        if resource.selector == ResourceSelector.TAG_MATCH:
            limits = [c for c in resource.conditions if c.tag_key.lower() == access_key]
            if not limits:
                raise ForbiddenError(
                    f"Tag matches must include a {access_key} condition within your approver paths"
                )
            for condition in limits:
                outside = [v for v in condition.values if not path_within(v, allowed, separator)]
                if outside:
                    raise ForbiddenError(f"{', '.join(outside)} is outside your approver paths")
                if condition.match == TagMatchMode.EXACT and not condition.values:
                    raise ForbiddenError("Choose at least one access path")
            return
        if resource.securable_kind not in DATA_KINDS:
            raise ForbiddenError(f"Only app admins can grant {_kind_label(resource.securable_kind)}s")
        name = resource.full_name or ""
        path = inventory.nearest(resource.securable_kind, name, access_key) if inventory is not None else None
        if not path:
            raise ForbiddenError(f"{name} has no {access_key} tag; only app admins can grant it")
        if not path_within(path, allowed, separator):
            raise ForbiddenError(f"{name} ({path}) is outside your approver paths")

    def _allowed_paths(self, actor: CurrentUser) -> list[str]:
        """Access paths the approver covers (sensitivity and data-access approver scopes)."""
        settings = self._engine.settings()
        keys = {"sensitivity", settings.access_tag_key.lower()}
        data_access = self._identity.find_data_access_definition()
        if data_access is not None:
            keys.add(data_access.key.lower())
        paths: set[str] = set()
        for item in self._access_requests.grantable_attributes(actor).attributes:
            if item.key.lower() in keys:
                paths.update(item.categories)
        return sorted(paths, key=str.lower)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require_creator(actor: CurrentUser) -> None:
        if not (actor.is_admin or actor.is_approver):
            raise ForbiddenError("Subscriptions are managed by app admins and approvers")

    @staticmethod
    def _require_admin(actor: CurrentUser) -> None:
        if not actor.is_admin:
            raise ForbiddenError("This action requires app administrator access")

    @staticmethod
    def _can_see(subscription: Subscription, actor: CurrentUser) -> bool:
        return actor.is_admin or subscription.created_by.lower() == actor.user_name.lower()

    def _audit(self, event_type: str, actor: CurrentUser, subscription: Subscription, extra: dict) -> None:
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                actor=actor.user_name,
                payload={
                    "subscription_id": subscription.subscription_id,
                    "name": subscription.name,
                    "group_name": subscription.group_name,
                    **extra,
                },
                created_at=datetime.now(UTC),
            )
        )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _kind_label(kind: SecurableKind | None) -> str:
    return (kind.value if kind else "object").replace("_", " ").lower()

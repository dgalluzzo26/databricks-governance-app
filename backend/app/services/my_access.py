"""Personal access overview: groups, effective attributes, and on-demand resources."""

from __future__ import annotations

from backend.app.core.errors import ForbiddenError, InvalidRequestError, NotFoundError
from backend.app.models.attribute_explorer import AttributeExplorerKind
from backend.app.models.data_access_pending import MembershipState, StructureState
from backend.app.models.identity_attribute import ValueSource
from backend.app.models.my_access import (
    MyAccessAttribute,
    MyAccessGroup,
    MyAccessResources,
    MyAccessResourcesRequest,
    MyAccessSnapshot,
    MyAccessSubscription,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.subscription import Subscription, SubscriptionPhase
from backend.app.models.user import CurrentUser
from backend.app.repositories.subscriptions import SubscriptionRepository
from backend.app.services.attribute_explorer import AttributeExplorerService
from backend.app.services.data_access import DataAccessService
from backend.app.services.data_access_planner import is_managed_group
from backend.app.services.identity_attributes import IdentityAttributeService

# Shown first; then the ones that are about to start, then everything else.
_PHASE_ORDER = {
    SubscriptionPhase.ACTIVE: 0,
    SubscriptionPhase.EXPIRING: 1,
    SubscriptionPhase.SCHEDULED: 2,
    SubscriptionPhase.PENDING_APPROVAL: 3,
}


class MyAccessService:
    """Build the signed-in user's access snapshot and lazy resource lookups.

    Args:
        identity: Effective assignments (user + groups).
        data_access: Data-access status for ``dac_*`` paths.
        attribute_explorer: Kind classification and resource inventory.
        subscriptions: Subscription store, to list the ones the caller is a subject of.
    """

    def __init__(
        self,
        identity: IdentityAttributeService,
        data_access: DataAccessService,
        attribute_explorer: AttributeExplorerService,
        subscriptions: SubscriptionRepository,
    ) -> None:
        self._identity = identity
        self._data_access = data_access
        self._explorer = attribute_explorer
        self._subscriptions = subscriptions

    def snapshot(self, user: CurrentUser) -> MyAccessSnapshot:
        """Groups the user belongs to and every attribute they hold (direct or via group)."""
        assignments = self._identity.list_for_user(user, unique=False).assignments
        settings = self._data_access._settings()  # noqa: SLF001
        definitions = {d.key.lower(): d for d in self._identity.list_definitions().definitions}
        statuses = {s.assignment_id: s for s in self._data_access.access_status(assignments)}

        group_counts: dict[str, int] = {g: 0 for g in user.attribute_groups}
        attributes: list[MyAccessAttribute] = []
        direct = 0
        inherited = 0
        ready = 0

        for item in assignments:
            definition = definitions.get(item.attribute_key.lower())
            value_source = definition.value_source if definition is not None else ValueSource.FREE_FORM
            kind = self._explorer.classify(item.attribute_key, value_source, settings)
            status = statuses.get(item.assignment_id)
            if (
                status is not None
                and status.membership == MembershipState.MEMBER
                and status.structure == StructureState.READY
            ):
                ready += 1
            if item.principal_type == PrincipalKind.USER:
                direct += 1
            else:
                inherited += 1
                if item.principal_name in group_counts:
                    group_counts[item.principal_name] += 1
                else:
                    # Group name from assignment may differ in casing from /Me.
                    matched = next(
                        (g for g in group_counts if g.lower() == item.principal_name.lower()),
                        None,
                    )
                    if matched is not None:
                        group_counts[matched] += 1
            attributes.append(
                MyAccessAttribute(
                    assignment_id=item.assignment_id,
                    attribute_key=item.attribute_key,
                    attribute_value=item.attribute_value,
                    kind=kind,
                    principal_type=item.principal_type,
                    principal_name=item.principal_name,
                    expires_at=item.expires_at.isoformat() if item.expires_at else None,
                    access_status=status,
                )
            )

        # Direct first, then by group name, then key/value for stable scanning.
        attributes.sort(
            key=lambda row: (
                0 if row.principal_type == PrincipalKind.USER else 1,
                row.principal_name.lower(),
                row.attribute_key.lower(),
                row.attribute_value.lower(),
            )
        )
        # Hide Data Access sync groups (dac_*): membership is derived, not assignable.
        prefix = settings.group_prefix
        groups = [
            MyAccessGroup(name=name, attribute_count=group_counts.get(name, 0))
            for name in user.attribute_groups
            if not is_managed_group(name, prefix)
        ]
        subscriptions = self._subscriptions_for(user)
        return MyAccessSnapshot(
            groups=groups,
            attributes=attributes,
            group_count=len(groups),
            attribute_count=len(attributes),
            direct_attribute_count=direct,
            inherited_attribute_count=inherited,
            data_access_ready_count=ready,
            subscriptions=subscriptions,
            subscription_count=len(subscriptions),
        )

    def _subscriptions_for(self, user: CurrentUser) -> list[MyAccessSubscription]:
        """Subscriptions whose subjects include the caller, directly or through a group."""
        me = {n.lower() for n in (user.user_name, user.email) if n}
        groups = {g.lower(): g for g in user.attribute_groups}
        rows: list[MyAccessSubscription] = []
        for subscription in self._subscriptions.list_subscriptions():
            via = _via(subscription, me, groups)
            if via is None:
                continue
            rows.append(_to_row(subscription, via))
        rows.sort(key=lambda r: (_PHASE_ORDER.get(r.phase, 9), r.display_name.lower()))
        return rows

    def resources(self, body: MyAccessResourcesRequest, user: CurrentUser) -> MyAccessResources:
        """Load tables/columns for one attribute the caller holds.

        Raises:
            InvalidRequestError: Blank key or value.
            ForbiddenError: Caller does not hold this attribute (or key for row filters).
            NotFoundError: Attribute definition is missing.
        """
        key = body.attribute_key.strip()
        value = body.attribute_value.strip()
        if not key or not value:
            raise InvalidRequestError("attribute_key and attribute_value are required")

        held = self._identity.list_for_user(user).assignments
        held_for_key = [a for a in held if a.attribute_key.lower() == key.lower()]
        if not held_for_key:
            raise ForbiddenError("You do not hold this attribute")

        try:
            definition = self._identity.get_definition(key)
        except NotFoundError:
            definition = self._identity.get_definition(held_for_key[0].attribute_key)

        settings = self._data_access._settings()  # noqa: SLF001
        kind = self._explorer.classify(definition.key, definition.value_source, settings)

        if kind == AttributeExplorerKind.ROW_FILTER:
            # Resources are by dimension; any held value for the key authorizes.
            tables = self._explorer.resources_for(kind, definition.key, value)
            return MyAccessResources(
                attribute_key=definition.key,
                attribute_value=value,
                kind=kind,
                tables=tables,
            )

        exact = next(
            (a for a in held_for_key if a.attribute_value.lower() == value.lower()),
            None,
        )
        if exact is None:
            raise ForbiddenError("You do not hold this attribute value")

        tables = self._explorer.resources_for(kind, definition.key, exact.attribute_value)
        return MyAccessResources(
            attribute_key=definition.key,
            attribute_value=exact.attribute_value,
            kind=kind,
            tables=tables,
        )


def _via(subscription: Subscription, me: set[str], groups: dict[str, str]) -> str | None:
    """How the caller is covered: ``Direct`` wins over a group, None when not a subject."""
    via: str | None = None
    for subject in subscription.subjects:
        name = subject.principal_name.lower()
        if subject.principal_type == PrincipalKind.USER and name in me:
            return "Direct"
        if subject.principal_type == PrincipalKind.GROUP and name in groups:
            via = via or groups[name]
    return via


def _to_row(subscription: Subscription, via: str) -> MyAccessSubscription:
    in_effect = subscription.in_effect()
    sync = subscription.last_sync
    return MyAccessSubscription(
        subscription_id=subscription.subscription_id,
        name=subscription.name,
        display_name=subscription.display_name,
        group_name=subscription.group_name,
        phase=subscription.phase,
        starts_at=subscription.starts_at,
        expires_at=subscription.expires_at,
        via=via,
        resources=subscription.resources,
        grants=list(sync.grants) if sync and in_effect else [],
        matched_objects=sync.matched_objects if sync else 0,
        last_synced_at=sync.synced_at if sync else None,
        sync_ok=sync.ok if sync else None,
    )

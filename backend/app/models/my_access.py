"""Models for the Identity → My access personal access snapshot."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.app.models.attribute_explorer import AttributeExplorerKind, UnlockedTable
from backend.app.models.base import ApiModel
from backend.app.models.data_access_pending import AccessStatus
from backend.app.models.principal import PrincipalKind
from backend.app.models.subscription import SubscriptionGrant, SubscriptionPhase, SubscriptionResource


class MyAccessGroup(ApiModel):
    """One of the caller's groups and how many attribute values it contributes.

    Attributes:
        name: Group display name from SCIM /Me.
        attribute_count: Distinct active attribute values assigned to this group.
    """

    name: str
    attribute_count: int = 0


class MyAccessAttribute(ApiModel):
    """One effective attribute the caller holds (directly or via a group).

    Attributes:
        assignment_id: Underlying assignment id.
        attribute_key: Attribute key.
        attribute_value: Assigned value.
        kind: How the attribute maps to access.
        principal_type: USER (direct) or GROUP (inherited).
        principal_name: User email or group display name.
        expires_at: ISO expiry when set.
        access_status: Data-access sync status when ``kind`` is data_access.
    """

    assignment_id: str
    attribute_key: str
    attribute_value: str
    kind: AttributeExplorerKind
    principal_type: PrincipalKind
    principal_name: str
    expires_at: str | None = None
    access_status: AccessStatus | None = None


class MyAccessSubscription(ApiModel):
    """A subscription the caller is a subject of, directly or through one of their groups.

    Attributes:
        subscription_id: Subscription id.
        name: Subscription slug (its group is ``dac_subscription_<name>``).
        display_name: Title shown in the UI.
        group_name: Account group that holds the access.
        phase: Status combined with the time window.
        starts_at: Window start.
        expires_at: Window end.
        via: ``Direct`` when the caller is listed, otherwise the group they belong to.
        resources: What the subscription reaches (rules, not the matched objects).
        grants: Grants the group held at its last sync; empty when it is not in effect.
        matched_objects: Objects the last sync matched.
        last_synced_at: When the last sync ran.
        sync_ok: Whether the last sync finished without errors.
    """

    subscription_id: str
    name: str
    display_name: str
    group_name: str
    phase: SubscriptionPhase
    starts_at: datetime
    expires_at: datetime
    via: str
    resources: list[SubscriptionResource] = Field(default_factory=list)
    grants: list[SubscriptionGrant] = Field(default_factory=list)
    matched_objects: int = 0
    last_synced_at: datetime | None = None
    sync_ok: bool | None = None


class MyAccessSnapshot(ApiModel):
    """Personal access overview for the signed-in user.

    Attributes:
        groups: Every group from the caller's SCIM membership.
        attributes: Assignments the caller holds, including the same value both
            directly and via a group when both exist (direct first, then via groups).
        group_count: ``len(groups)``.
        attribute_count: ``len(attributes)``.
        direct_attribute_count: Assignments on the user principal.
        inherited_attribute_count: Assignments on groups the user belongs to.
        data_access_ready_count: Data-access attributes with membership+structure ready.
        subscriptions: Subscriptions the caller is a subject of, active first.
        subscription_count: ``len(subscriptions)``.
    """

    groups: list[MyAccessGroup] = Field(default_factory=list)
    attributes: list[MyAccessAttribute] = Field(default_factory=list)
    group_count: int = 0
    attribute_count: int = 0
    direct_attribute_count: int = 0
    inherited_attribute_count: int = 0
    data_access_ready_count: int = 0
    subscriptions: list[MyAccessSubscription] = Field(default_factory=list)
    subscription_count: int = 0


class MyAccessResourcesRequest(ApiModel):
    """Body to load resources for one attribute the caller holds.

    Attributes:
        attribute_key: Attribute key.
        attribute_value: Held value (for row filters any held value for the key is enough).
    """

    attribute_key: str = Field(min_length=1)
    attribute_value: str = Field(min_length=1)


class MyAccessResources(ApiModel):
    """Resources unlocked or filtered by one of the caller's attributes.

    Attributes:
        attribute_key: Queried key.
        attribute_value: Queried value.
        kind: Access mechanism.
        tables: Affected tables or columns.
    """

    attribute_key: str
    attribute_value: str
    kind: AttributeExplorerKind
    tables: list[UnlockedTable] = Field(default_factory=list)

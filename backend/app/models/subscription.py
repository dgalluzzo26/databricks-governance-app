"""Subscriptions: time-boxed bundles of Unity Catalog and workspace access for a group.

A subscription names who (users, groups, service principals), what (objects picked by hand
or matched by tag values), which privileges, and when. Each one owns an account group
``dac_subscription_<name>``; the subscription engine keeps that group's members, grants
and conferred Identity attributes in line with the subscription while it is active, and
strips them once it is not.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, computed_field, field_validator, model_validator

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind

SUBSCRIPTION_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SUBSCRIPTION_NAME_MAX = 64
EXPIRING_WINDOW_DAYS = 14


class SubscriptionStatus(StrEnum):
    """Decision state of a subscription (time is layered on top, see ``SubscriptionPhase``).

    Attributes:
        PENDING_APPROVAL: Created by an approver; waiting for an app admin.
        APPROVED: Approved; in effect between ``starts_at`` and ``expires_at``.
        DENIED: Rejected by an app admin; never takes effect.
        REVOKED: Ended early by an admin or its creator.
    """

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"


class SubscriptionPhase(StrEnum):
    """Status combined with the time window, as shown in the UI.

    Attributes:
        PENDING_APPROVAL: Waiting for an app admin.
        SCHEDULED: Approved, start time not reached.
        ACTIVE: Approved and inside its window.
        EXPIRING: Active and ending within ``EXPIRING_WINDOW_DAYS``.
        EXPIRED: Approved but past its end time.
        DENIED: Rejected.
        REVOKED: Ended early.
    """

    PENDING_APPROVAL = "PENDING_APPROVAL"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"


class SecurableKind(StrEnum):
    """Object types a subscription can grant on.

    Attributes:
        CATALOG: Unity Catalog catalog.
        SCHEMA: Unity Catalog schema.
        TABLE: Table or view.
        VOLUME: Unity Catalog volume.
        FUNCTION: Unity Catalog function.
        MODEL: Registered model.
        EXTERNAL_LOCATION: Unity Catalog external location.
        WORKSPACE_FOLDER: Workspace folder (workspace permissions, not a UC grant).
        DASHBOARD: AI/BI dashboard (workspace permissions).
        GENIE_SPACE: Genie space (workspace permissions).
        APP: Databricks app (workspace permissions).
        SQL_WAREHOUSE: SQL warehouse (workspace permissions).
    """

    CATALOG = "CATALOG"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    VOLUME = "VOLUME"
    FUNCTION = "FUNCTION"
    MODEL = "MODEL"
    EXTERNAL_LOCATION = "EXTERNAL_LOCATION"
    WORKSPACE_FOLDER = "WORKSPACE_FOLDER"
    DASHBOARD = "DASHBOARD"
    GENIE_SPACE = "GENIE_SPACE"
    APP = "APP"
    SQL_WAREHOUSE = "SQL_WAREHOUSE"


WORKSPACE_KINDS: frozenset[SecurableKind] = frozenset(
    {
        SecurableKind.WORKSPACE_FOLDER,
        SecurableKind.DASHBOARD,
        SecurableKind.GENIE_SPACE,
        SecurableKind.APP,
        SecurableKind.SQL_WAREHOUSE,
    }
)
"""Kinds granted through the workspace permissions API; one permission level per object."""

DATA_KINDS: frozenset[SecurableKind] = frozenset(
    {
        SecurableKind.CATALOG,
        SecurableKind.SCHEMA,
        SecurableKind.TABLE,
        SecurableKind.VOLUME,
        SecurableKind.FUNCTION,
        SecurableKind.MODEL,
    }
)
"""Kinds inside a catalog: they carry tags, need USE CATALOG / USE SCHEMA, and pass the review gate."""

# Catalog, schema, table, function and external location lists were checked against GRANT on
# a live metastore. Workspace kinds list permission levels lowest first.
UC_PRIVILEGES: dict[SecurableKind, tuple[str, ...]] = {
    SecurableKind.CATALOG: (
        "USE CATALOG",
        "USE SCHEMA",
        "APPLY TAG",
        "BROWSE",
        "READ METADATA",
        "EXECUTE",
        "READ FEATURE",
        "READ SECRET",
        "READ VOLUME",
        "REFERENCE SECRET",
        "SELECT",
        "USE CONNECTION",
        "MODIFY",
        "REFRESH",
        "WRITE SECRET",
        "WRITE VOLUME",
        "CREATE CONNECTION",
        "CREATE FEATURE",
        "CREATE FUNCTION",
        "CREATE MATERIALIZED VIEW",
        "CREATE MEMORY STORE",
        "CREATE MODEL",
        "CREATE MODEL VERSION",
        "CREATE SCHEMA",
        "CREATE SECRET",
        "CREATE SERVICE",
        "CREATE TABLE",
        "CREATE VOLUME",
        "EXTERNAL USE SCHEMA",
        "ALL PRIVILEGES",
        "MANAGE",
    ),
    SecurableKind.SCHEMA: (
        "USE SCHEMA",
        "APPLY TAG",
        "READ METADATA",
        "EXECUTE",
        "READ FEATURE",
        "READ VOLUME",
        "SELECT",
        "USE CONNECTION",
        "MODIFY",
        "REFRESH",
        "WRITE VOLUME",
        "CREATE CONNECTION",
        "CREATE FEATURE",
        "CREATE FUNCTION",
        "CREATE MATERIALIZED VIEW",
        "CREATE MEMORY STORE",
        "CREATE MODEL",
        "CREATE MODEL VERSION",
        "CREATE SERVICE",
        "CREATE TABLE",
        "CREATE VOLUME",
        "ALL PRIVILEGES",
        "MANAGE",
    ),
    SecurableKind.TABLE: (
        "APPLY TAG",
        "READ METADATA",
        "SELECT",
        "MODIFY",
        "REFRESH",
        "ALL PRIVILEGES",
        "MANAGE",
    ),
    SecurableKind.VOLUME: ("APPLY TAG", "READ VOLUME", "WRITE VOLUME", "ALL PRIVILEGES", "MANAGE"),
    SecurableKind.FUNCTION: ("APPLY TAG", "READ METADATA", "EXECUTE", "ALL PRIVILEGES", "MANAGE"),
    SecurableKind.MODEL: ("APPLY TAG", "EXECUTE", "ALL PRIVILEGES", "MANAGE"),
    SecurableKind.EXTERNAL_LOCATION: (
        "BROWSE",
        "READ METADATA",
        "READ FILES",
        "WRITE FILES",
        "CREATE EXTERNAL TABLE",
        "CREATE EXTERNAL VOLUME",
        "CREATE FOREIGN SECURABLE",
        "CREATE MANAGED STORAGE",
        "EXTERNAL USE LOCATION",
        "ALL PRIVILEGES",
        "MANAGE",
    ),
    SecurableKind.WORKSPACE_FOLDER: ("CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"),
    SecurableKind.DASHBOARD: ("CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"),
    SecurableKind.GENIE_SPACE: ("CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"),
    SecurableKind.APP: ("CAN_USE", "CAN_MANAGE"),
    SecurableKind.SQL_WAREHOUSE: ("CAN_VIEW", "CAN_MONITOR", "CAN_USE", "CAN_MANAGE"),
}

ALL_PRIVILEGES: tuple[str, ...] = tuple(
    dict.fromkeys(p for privileges in UC_PRIVILEGES.values() for p in privileges)
)

ADMIN_ONLY_PRIVILEGES: frozenset[str] = frozenset({"ALL PRIVILEGES", "MANAGE", "CAN_MANAGE"})
"""Privileges only app admins may put in a subscription."""


class PrivilegePreset(StrEnum):
    """Named privilege bundles offered in the UI; CUSTOM uses the explicit list.

    Attributes:
        READ: Query and read (SELECT, READ VOLUME, EXECUTE; run/use on workspace objects).
        BUILD: Matches Databricks' Data Editor preset: read and change data and create objects.
        VOLUME_READ_WRITE: Read and write volume and external location files.
        ADMIN: ALL PRIVILEGES and MANAGE (CAN_MANAGE on workspace objects). App admins only.
        CUSTOM: Exactly the privileges listed on the resource.
    """

    READ = "READ"
    BUILD = "BUILD"
    VOLUME_READ_WRITE = "VOLUME_READ_WRITE"
    ADMIN = "ADMIN"
    CUSTOM = "CUSTOM"


PRESET_PRIVILEGES: dict[PrivilegePreset, tuple[str, ...]] = {
    PrivilegePreset.READ: ("SELECT", "READ VOLUME", "EXECUTE", "READ FILES"),
    PrivilegePreset.BUILD: (
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
        "READ FILES",
        "WRITE FILES",
        "CREATE EXTERNAL TABLE",
        "CREATE EXTERNAL VOLUME",
    ),
    PrivilegePreset.VOLUME_READ_WRITE: ("READ VOLUME", "WRITE VOLUME", "READ FILES", "WRITE FILES"),
    PrivilegePreset.ADMIN: ("ALL PRIVILEGES", "MANAGE"),
}
"""UC privileges per preset; each object keeps only the ones valid for its kind."""

WORKSPACE_PRESET_LEVELS: dict[SecurableKind, dict[PrivilegePreset, str]] = {
    SecurableKind.WORKSPACE_FOLDER: {
        PrivilegePreset.READ: "CAN_READ",
        PrivilegePreset.BUILD: "CAN_EDIT",
        PrivilegePreset.VOLUME_READ_WRITE: "CAN_READ",
        PrivilegePreset.ADMIN: "CAN_MANAGE",
    },
    SecurableKind.DASHBOARD: {
        PrivilegePreset.READ: "CAN_RUN",
        PrivilegePreset.BUILD: "CAN_EDIT",
        PrivilegePreset.ADMIN: "CAN_MANAGE",
    },
    SecurableKind.GENIE_SPACE: {
        PrivilegePreset.READ: "CAN_RUN",
        PrivilegePreset.BUILD: "CAN_EDIT",
        PrivilegePreset.ADMIN: "CAN_MANAGE",
    },
    SecurableKind.APP: {
        PrivilegePreset.READ: "CAN_USE",
        PrivilegePreset.BUILD: "CAN_USE",
        PrivilegePreset.ADMIN: "CAN_MANAGE",
    },
    SecurableKind.SQL_WAREHOUSE: {
        PrivilegePreset.READ: "CAN_USE",
        PrivilegePreset.BUILD: "CAN_USE",
        PrivilegePreset.ADMIN: "CAN_MANAGE",
    },
}
"""Permission level each preset gives on a workspace object."""


def privileges_for(kind: SecurableKind, preset: PrivilegePreset, custom: list[str]) -> list[str]:
    """Privileges a resource grants on one object of ``kind``.

    Presets and custom lists span object types; each object only gets the privileges valid
    for its own type, so one list can cover a catalog and the tables under it.

    Args:
        kind: Object type receiving the grant.
        preset: Preset chosen for the resource.
        custom: Explicit privileges (used when ``preset`` is CUSTOM).

    Returns:
        Valid privileges for ``kind``, in the canonical order of ``UC_PRIVILEGES``.
    """
    if preset == PrivilegePreset.CUSTOM:
        wanted = set(custom)
    elif kind in WORKSPACE_KINDS:
        level = WORKSPACE_PRESET_LEVELS[kind].get(preset)
        wanted = {level} if level else set()
    else:
        wanted = set(PRESET_PRIVILEGES[preset])
    return [p for p in UC_PRIVILEGES[kind] if p in wanted]


class TagMatchMode(StrEnum):
    """How a tag condition compares values.

    Attributes:
        PREFIX: Value equals the listed value or sits under it in the hierarchy
            (``people`` matches ``people.ia.nemis``).
        EXACT: Value equals one of the listed values (case-insensitive).
    """

    PREFIX = "PREFIX"
    EXACT = "EXACT"


class TagCondition(ApiModel):
    """One governed-tag test; an object matches when its tag value matches any listed value.

    Attributes:
        tag_key: Governed tag key (e.g. ``access_path``).
        values: Allowed values (1-50).
        match: PREFIX for hierarchical tags, EXACT otherwise.
    """

    tag_key: str = Field(min_length=1, max_length=255)
    values: list[str] = Field(min_length=1, max_length=50)
    match: TagMatchMode = TagMatchMode.EXACT

    @field_validator("tag_key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        """Strip whitespace from the tag key and reject blank keys."""
        text = value.strip()
        if not text:
            raise ValueError("Tag key cannot be empty")
        return text

    @field_validator("values")
    @classmethod
    def _clean_values(cls, values: list[str]) -> list[str]:
        """Strip values, drop blanks and duplicates, and require at least one."""
        cleaned = list(dict.fromkeys(v.strip() for v in values if v and v.strip()))
        if not cleaned:
            raise ValueError("At least one tag value is required")
        return cleaned


class ResourceSelector(StrEnum):
    """How a subscription resource picks its objects.

    Attributes:
        OBJECT: One object chosen by name.
        TAG_MATCH: Every object whose tags meet all conditions, re-matched on each sync.
    """

    OBJECT = "OBJECT"
    TAG_MATCH = "TAG_MATCH"


TAG_MATCH_KINDS: frozenset[SecurableKind] = frozenset(
    {SecurableKind.CATALOG, SecurableKind.SCHEMA, SecurableKind.TABLE, SecurableKind.VOLUME}
)


class SubscriptionResource(ApiModel):
    """What a subscription reaches and with which privileges.

    Attributes:
        selector: OBJECT or TAG_MATCH.
        securable_kind: Object type, for OBJECT resources.
        full_name: ``catalog[.schema[.object]]``, an external location name, a workspace folder
            path, or the ID of a dashboard, Genie space, app or SQL warehouse, for OBJECT.
        label: Display name for objects picked by ID (dashboards, Genie spaces, warehouses).
        conditions: Tag conditions (all must match), for TAG_MATCH.
        levels: Object types TAG_MATCH grants on (catalogs, schemas, tables, volumes).
        preset: Privilege bundle.
        privileges: Explicit privileges when ``preset`` is CUSTOM.
    """

    selector: ResourceSelector
    securable_kind: SecurableKind | None = None
    full_name: str | None = Field(default=None, max_length=1024)
    label: str | None = Field(default=None, max_length=512)
    conditions: list[TagCondition] = Field(default_factory=list, max_length=10)
    levels: list[SecurableKind] = Field(default_factory=list)
    preset: PrivilegePreset = PrivilegePreset.READ
    privileges: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> SubscriptionResource:
        """Validate the fields each selector needs and the privilege names."""
        if self.selector == ResourceSelector.OBJECT:
            if self.securable_kind is None:
                raise ValueError("securableKind is required for an object resource")
            name = (self.full_name or "").strip()
            if not name:
                raise ValueError("fullName is required for an object resource")
            self.full_name = name
            _check_object_name(self.securable_kind, name)
            self.conditions = []
            self.levels = []
        else:
            if not self.conditions:
                raise ValueError("At least one tag condition is required")
            levels = list(dict.fromkeys(self.levels)) or [SecurableKind.TABLE]
            bad = [k.value for k in levels if k not in TAG_MATCH_KINDS]
            if bad:
                raise ValueError(f"Tag matches cannot grant on {', '.join(bad)}")
            self.levels = levels
            self.securable_kind = None
            self.full_name = None
            self.label = None
        if self.preset == PrivilegePreset.CUSTOM:
            privileges = list(dict.fromkeys(p.strip().upper() for p in self.privileges if p.strip()))
            unknown = [p for p in privileges if p not in ALL_PRIVILEGES]
            if unknown:
                raise ValueError(f"Unknown privileges: {', '.join(unknown)}")
            if not privileges:
                raise ValueError("Choose at least one privilege")
            self.privileges = privileges
        else:
            self.privileges = []
        if not any(privileges_for(kind, self.preset, self.privileges) for kind in self.kinds()):
            raise ValueError("None of the chosen privileges apply to this object type")
        return self

    def kinds(self) -> list[SecurableKind]:
        """Object types this resource can grant on."""
        if self.selector == ResourceSelector.OBJECT and self.securable_kind is not None:
            return [self.securable_kind]
        return list(self.levels)


def _check_object_name(kind: SecurableKind, name: str) -> None:
    """Reject names with the wrong number of parts for their object type."""
    if kind == SecurableKind.WORKSPACE_FOLDER:
        if not name.startswith("/"):
            raise ValueError("Workspace folder paths must start with /")
        return
    if kind not in DATA_KINDS:
        return
    parts = name.split(".")
    expected = {SecurableKind.CATALOG: 1, SecurableKind.SCHEMA: 2}.get(kind, 3)
    if len(parts) != expected or any(not p.strip() for p in parts):
        raise ValueError(f"{kind.value.lower()} names need {expected} dot-separated part(s)")


class SubscriptionSubject(ApiModel):
    """A principal covered by a subscription.

    Attributes:
        principal_name: User name, group name, or service principal application ID.
        principal_type: Kind of principal.
    """

    principal_name: str = Field(min_length=1, max_length=256)
    principal_type: PrincipalKind

    @field_validator("principal_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Strip whitespace and reject blank names."""
        text = value.strip()
        if not text:
            raise ValueError("Principal name cannot be empty")
        return text


class ConferredAttribute(ApiModel):
    """An Identity attribute value the subscription group holds while active.

    Sensitivity unmasks columns, row-filter dimensions widen row filters, and a data-access
    value adds the group to that ``dac_*`` group. They expire with the subscription.

    Attributes:
        attribute_key: Attribute definition key.
        attribute_value: Value to confer.
    """

    attribute_key: str = Field(min_length=1, max_length=128)
    attribute_value: str = Field(min_length=1, max_length=512)


class SubscriptionGrant(ApiModel):
    """One grant the engine holds for a subscription group.

    Attributes:
        securable_kind: Object type.
        full_name: Object name or folder path.
        privilege: Privilege (UC) or permission level (workspace folders).
    """

    securable_kind: SecurableKind
    full_name: str
    privilege: str


class SubscriptionSyncState(ApiModel):
    """What the last engine run did for one subscription.

    Attributes:
        synced_at: When the run finished.
        ok: True when every step succeeded.
        active: Whether the subscription was in effect for that run.
        matched_objects: Objects the resources resolved to.
        grants: Grants the engine holds (used to revoke ones no longer wanted).
        members: Member count after the run.
        grants_added: GRANTs issued.
        grants_revoked: REVOKEs issued.
        members_added: Members added.
        members_removed: Members removed.
        errors: Failure messages (first 20).
    """

    synced_at: datetime
    ok: bool = True
    active: bool = False
    matched_objects: int = 0
    grants: list[SubscriptionGrant] = Field(default_factory=list)
    members: int = 0
    grants_added: int = 0
    grants_revoked: int = 0
    members_added: int = 0
    members_removed: int = 0
    errors: list[str] = Field(default_factory=list)


class Subscription(ApiModel):
    """A time-boxed access bundle owned by one account group.

    Attributes:
        subscription_id: Unique identifier.
        name: Unique slug (lowercase letters, digits, single underscores).
        display_name: Human-friendly title.
        description: What the access is for.
        group_name: Account group the engine manages (``dac_subscription_<name>``).
        status: Decision state.
        starts_at: When access begins.
        expires_at: When access ends.
        review_exempt: When true, objects need not carry the review tag (admin only).
        subjects: Who is covered.
        resources: What is reached and with which privileges.
        attributes: Identity attribute values the group holds while active.
        created_by: Creator user name.
        created_at: Creation time.
        updated_at: Last edit time.
        decided_by: Admin who approved or denied it; None when auto-approved.
        decided_at: Decision time.
        decision_note: Optional note left with the decision.
        revoked_by: Who revoked it.
        revoked_at: When it was revoked.
        last_sync: Outcome of the last engine run, or None before the first.
    """

    subscription_id: str
    name: str
    display_name: str
    description: str | None = None
    group_name: str
    status: SubscriptionStatus
    starts_at: datetime
    expires_at: datetime
    review_exempt: bool = False
    subjects: list[SubscriptionSubject] = Field(default_factory=list)
    resources: list[SubscriptionResource] = Field(default_factory=list)
    attributes: list[ConferredAttribute] = Field(default_factory=list)
    created_by: str
    created_at: datetime
    updated_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None
    last_sync: SubscriptionSyncState | None = None

    def in_effect(self, now: datetime | None = None) -> bool:
        """Whether the subscription should be granting access at ``now``."""
        if self.status != SubscriptionStatus.APPROVED:
            return False
        moment = now or datetime.now(UTC)
        return _utc(self.starts_at) <= moment < _utc(self.expires_at)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def phase(self) -> SubscriptionPhase:
        """Status combined with the time window."""
        if self.status != SubscriptionStatus.APPROVED:
            return SubscriptionPhase(self.status.value)
        now = datetime.now(UTC)
        if now < _utc(self.starts_at):
            return SubscriptionPhase.SCHEDULED
        if now >= _utc(self.expires_at):
            return SubscriptionPhase.EXPIRED
        if (_utc(self.expires_at) - now).days < EXPIRING_WINDOW_DAYS:
            return SubscriptionPhase.EXPIRING
        return SubscriptionPhase.ACTIVE


def _utc(value: datetime) -> datetime:
    """Read naive datetimes as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SubscriptionDraft(ApiModel):
    """Request body to create or edit a subscription.

    Attributes:
        name: Unique slug; becomes ``dac_subscription_<name>``. Ignored on edit.
        display_name: Title shown in the UI.
        description: What the access is for.
        starts_at: Start time; defaults to now.
        expires_at: End time; required, after ``starts_at``, within the app's maximum.
        review_exempt: Skip the review-tag gate (admins only).
        subjects: Principals covered (1-200).
        resources: Objects and privileges (1-50).
        attributes: Identity attribute values conferred (0-20).
    """

    name: str = Field(min_length=1, max_length=SUBSCRIPTION_NAME_MAX)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    expires_at: datetime
    review_exempt: bool = False
    subjects: list[SubscriptionSubject] = Field(min_length=1, max_length=200)
    resources: list[SubscriptionResource] = Field(min_length=1, max_length=50)
    attributes: list[ConferredAttribute] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        """Require a lowercase slug of letters, digits, and single underscores."""
        text = value.strip().lower()
        if not SUBSCRIPTION_NAME_PATTERN.match(text):
            raise ValueError(
                "Use lowercase letters and digits separated by single underscores "
                "(no spaces or special characters)"
            )
        return text

    @field_validator("display_name")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        """Strip whitespace and reject blank titles."""
        text = value.strip()
        if not text:
            raise ValueError("Display name cannot be empty")
        return text

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        """Strip whitespace; blank descriptions become None."""
        if value is None:
            return None
        return value.strip() or None


class SubscriptionDecision(ApiModel):
    """Request body to approve or deny a pending subscription.

    Attributes:
        approve: True to approve, False to deny.
        note: Optional note shown on the subscription.
    """

    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class SubscriptionExtendRequest(ApiModel):
    """Request body to move a subscription's end time.

    Attributes:
        expires_at: New end time; must be in the future and within the app's maximum.
    """

    expires_at: datetime


class SubscriptionList(ApiModel):
    """Subscriptions visible to the caller.

    Attributes:
        subscriptions: Matching subscriptions, newest first.
        total: Number returned.
    """

    subscriptions: list[Subscription]
    total: int


class MatchedObject(ApiModel):
    """One object a resource resolves to in a preview.

    Attributes:
        securable_kind: Object type.
        full_name: Object name, folder path, or object ID.
        label: Display name for objects picked by ID.
        privileges: Privileges it would receive.
        access_path: Effective ``access_path`` tag value, when tagged.
        reviewed: Whether it passes the review gate (or the gate is waived).
    """

    securable_kind: SecurableKind
    full_name: str
    label: str | None = None
    privileges: list[str]
    access_path: str | None = None
    reviewed: bool = True


class SubscriptionPreview(ApiModel):
    """What a draft would reach right now.

    Attributes:
        group_name: Group the subscription would own.
        objects: Matched objects that would be granted (first 500).
        total_objects: Total matched and granted.
        skipped_unreviewed: Matched objects held back by the review gate.
        grant_count: Grants including USE CATALOG / USE SCHEMA traversal.
        truncated: True when ``objects`` was cut at 500.
        warnings: Non-blocking issues (e.g. a picked object without an access tag).
    """

    group_name: str
    objects: list[MatchedObject]
    total_objects: int
    skipped_unreviewed: int = 0
    grant_count: int = 0
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class SubscriptionOptions(ApiModel):
    """Choices the wizard needs for the signed-in user.

    Attributes:
        can_create: Whether the caller may create subscriptions.
        is_admin: Whether the caller is an app admin.
        requires_approval: Whether the caller's subscriptions wait for an admin.
        max_days: Longest allowed window in days.
        group_prefix: Prefix of subscription groups (``dac_subscription_``).
        access_tag_key: Hierarchical access tag (``access_path``).
        allowed_paths: Access paths the caller may use; empty means any (admins).
        tag_keys: Governed tag keys usable in tag conditions.
        tag_values: Allowed values per governed tag key (lower-case keys).
        privileges: Valid privileges per object type.
        presets: Privileges each preset grants, per object type.
    """

    can_create: bool
    is_admin: bool
    requires_approval: bool
    max_days: int
    group_prefix: str
    access_tag_key: str
    allowed_paths: list[str] = Field(default_factory=list)
    tag_keys: list[str] = Field(default_factory=list)
    tag_values: dict[str, list[str]] = Field(default_factory=dict)
    privileges: dict[str, list[str]] = Field(default_factory=dict)
    presets: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class SubscriptionSyncResult(ApiModel):
    """Summary of one engine pass over all subscriptions.

    Attributes:
        processed: Subscriptions examined.
        active: Subscriptions in effect.
        grants_added: GRANTs issued.
        grants_revoked: REVOKEs issued.
        members_added: Members added.
        members_removed: Members removed.
        failed: Subscriptions with at least one error.
        errors: First 50 error messages, prefixed by subscription name.
    """

    processed: int = 0
    active: int = 0
    grants_added: int = 0
    grants_revoked: int = 0
    members_added: int = 0
    members_removed: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)

"""Models for outstanding data-access work, tag change detection, and per-assignment access status."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from backend.app.models.base import ApiModel


class GapKind(StrEnum):
    """One kind of change the sync still has to make."""

    GROUP_CREATE = "group_create"
    GROUP_NEST = "group_nest"
    MEMBER_ADD = "member_add"
    MEMBER_REMOVE = "member_remove"
    GRANT_ADD = "grant_add"
    GRANT_REVOKE = "grant_revoke"


class GapStatus(StrEnum):
    """Pending until the next apply; failed when the last apply tried and errored."""

    PENDING = "pending"
    FAILED = "failed"


class GapItem(ApiModel):
    """One outstanding change between desired and actual data-access state.

    Attributes:
        item_key: Stable identity of the change (kind plus its target), lowercase.
        kind: What has to change.
        status: Pending, or failed on the last apply.
        group_name: Managed ``dac_*`` group involved (grantee for grants).
        principal_name: Member to add or remove, or the nested parent group.
        principal_type: ``USER``, ``GROUP``, or ``SERVICE_PRINCIPAL`` for membership items.
        securable_type: ``catalog``, ``schema``, or ``table`` for grant items.
        full_name: Securable full name for grant items.
        privilege: Privilege for grant items.
        value: Data-access path the item belongs to (e.g. ``finance.budget``), when known.
        reason: Why the change is needed.
        error: Last apply error, when failed.
        run_id: Sync run that computed the item, or None for app-recorded items.
        computed_at: When the item was computed.
    """

    item_key: str
    kind: GapKind
    status: GapStatus = GapStatus.PENDING
    group_name: str | None = None
    principal_name: str | None = None
    principal_type: str | None = None
    securable_type: str | None = None
    full_name: str | None = None
    privilege: str | None = None
    value: str | None = None
    reason: str = ""
    error: str | None = None
    run_id: str | None = None
    computed_at: datetime


class GapMeta(ApiModel):
    """When the outstanding-work list was last computed and by which run.

    Attributes:
        run_id: Sync run that computed it.
        mode: ``dry_run`` (everything pending) or ``apply`` (only failures remain).
        computed_at: Inventory time of that run.
        tables_tracked: Tables carrying the access tag in the tag snapshot.
        tag_baseline: True when that run created the first tag snapshot (no diff yet).
    """

    run_id: str
    mode: str
    computed_at: datetime
    tables_tracked: int = 0
    tag_baseline: bool = False


class TagChangeKind(StrEnum):
    """How a table's data-access tagging changed between two syncs."""

    NEWLY_ELIGIBLE = "newly_eligible"
    NO_LONGER_ELIGIBLE = "no_longer_eligible"
    VALUE_CHANGED = "value_changed"


class TagSnapshotRow(ApiModel):
    """Last-seen data-access tagging for one table.

    Attributes:
        full_name: Table full name.
        access_value: Value of the access tag, or None when absent.
        reviewed: Whether the review tag carries the required value.
        eligible: Access tag present and reviewed (the table gets grants).
    """

    full_name: str
    access_value: str | None = None
    reviewed: bool = False
    eligible: bool = False


class TagChange(ApiModel):
    """One table whose tagging changed since the previous sync.

    Attributes:
        run_id: Sync run that detected the change.
        detected_at: Inventory time of that run.
        full_name: Table full name.
        change: Newly eligible, no longer eligible, or value changed.
        old_value: Access value before (eligible tables only).
        new_value: Access value after (eligible tables only).
    """

    run_id: str
    detected_at: datetime
    full_name: str
    change: TagChangeKind
    old_value: str | None = None
    new_value: str | None = None


class MembershipEventStatus(StrEnum):
    """Outcome of applying one assignment's group membership immediately."""

    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"


class MembershipEvent(ApiModel):
    """Record of an immediate group membership change made on approve, assign, or revoke.

    Attributes:
        recorded_at: When the change was attempted.
        assignment_id: Identity assignment that triggered it.
        principal_name: Principal added or removed.
        principal_type: ``USER``, ``GROUP``, or ``SERVICE_PRINCIPAL``.
        group_name: Managed group.
        value: Data-access path of the assignment.
        action: ``add`` or ``remove``.
        status: Applied, failed (sync retries it), or skipped (sync handles it).
        group_created: True when the group did not exist and was created for this change.
        error: Failure or skip reason.
        actor: User who approved, assigned, or revoked.
    """

    recorded_at: datetime
    assignment_id: str
    principal_name: str
    principal_type: str
    group_name: str
    value: str
    action: str
    status: MembershipEventStatus
    group_created: bool = False
    error: str | None = None
    actor: str | None = None


class PendingCounts(ApiModel):
    """Outstanding items by kind, plus failures and expiries not yet removed.

    Attributes:
        group_create: Groups to create.
        group_nest: Missing parent-into-child nesting links.
        member_add: Members to add.
        member_remove: Members to remove.
        grant_add: Grants to add.
        grant_revoke: Grants to revoke.
        failed: Items the last apply tried and could not complete (included in the kinds above).
        expired_since_sync: Data-access assignments that expired after the last sync; removed at the next one.
    """

    group_create: int = 0
    group_nest: int = 0
    member_add: int = 0
    member_remove: int = 0
    grant_add: int = 0
    grant_revoke: int = 0
    failed: int = 0
    expired_since_sync: int = 0


class TagChangeCounts(ApiModel):
    """Tag changes detected by the last sync.

    Attributes:
        newly_eligible: Tables that started receiving grants.
        no_longer_eligible: Tables that lost the access tag or review.
        value_changed: Eligible tables whose access value changed.
    """

    newly_eligible: int = 0
    no_longer_eligible: int = 0
    value_changed: int = 0


class PendingSummary(ApiModel):
    """Headline view of what the sync still has to do.

    Attributes:
        meta: When the list was computed, or None before the first sync.
        counts: Outstanding items by kind.
        tag_changes: Tag changes detected by the last sync.
        total: Sum of outstanding items (excluding expiries not yet seen by a sync).
    """

    meta: GapMeta | None = None
    counts: PendingCounts = Field(default_factory=PendingCounts)
    tag_changes: TagChangeCounts = Field(default_factory=TagChangeCounts)
    total: int = 0


class GapItemPage(ApiModel):
    """One page of outstanding items.

    Attributes:
        items: Page rows.
        total: Matching items across pages.
        page: 1-based page.
        page_size: Rows per page.
    """

    items: list[GapItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25


class TagChangePage(ApiModel):
    """One page of detected tag changes, newest first.

    Attributes:
        items: Page rows.
        total: Matching changes across pages.
        page: 1-based page.
        page_size: Rows per page.
    """

    items: list[TagChange] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25


class MembershipState(StrEnum):
    """Whether the assignee is in the assignment's ``dac_*`` group."""

    MEMBER = "member"
    PENDING_SYNC = "pending_sync"
    FAILED = "failed"
    REMOVAL_PENDING = "removal_pending"
    REMOVED = "removed"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class StructureState(StrEnum):
    """Whether the groups, nesting, and grants under the assignment's path are in place."""

    READY = "ready"
    PENDING_SYNC = "pending_sync"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class AccessStatus(ApiModel):
    """Whether one data-access assignment actually gives access yet.

    Attributes:
        assignment_id: Identity assignment.
        principal_name: Assignee.
        value: Data-access path.
        group_name: Managed group for the path, when the value maps to one.
        membership: Assignee's membership in that group.
        structure: Groups, nesting, and grants under the path.
        detail: Plain-language explanation.
    """

    assignment_id: str
    principal_name: str
    value: str
    group_name: str | None = None
    membership: MembershipState
    structure: StructureState
    detail: str = ""


class AccessStatusList(ApiModel):
    """Access status for a set of data-access assignments.

    Attributes:
        statuses: One entry per data-access assignment (other attributes are omitted).
    """

    statuses: list[AccessStatus] = Field(default_factory=list)


class AccessStatusRequest(ApiModel):
    """Assignments to report on.

    Attributes:
        assignment_ids: Up to 200 assignment ids.
    """

    assignment_ids: list[str] = Field(default_factory=list, max_length=200)

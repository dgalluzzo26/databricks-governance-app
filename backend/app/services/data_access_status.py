"""Pure helper: does a data-access assignment actually give access yet?

Two things must both be true for an assignee to read a path's tables. They must be a
member of the path's ``dac_*`` group (applied immediately on approve, or by sync), and
the path's structure must exist: the group itself, parent nesting, and table grants.
Structure is only built by the sync, so a fresh approval can be "member, structure
pending" until the next run.
"""

from __future__ import annotations

from datetime import datetime

from backend.app.models.data_access_pending import (
    AccessStatus,
    GapItem,
    GapKind,
    GapMeta,
    MembershipEvent,
    MembershipEventStatus,
    MembershipState,
    StructureState,
)
from backend.app.models.identity_attribute import ALL_VALUES, IdentityAttributeAssignment
from backend.app.services.data_access_planner import group_name


def _path_pending(value: str, separator: str, structure_values: set[str]) -> int:
    """Count outstanding structure values equal to ``value`` or beneath it."""
    prefix = f"{value}{separator}".lower()
    needle = value.lower()
    return sum(1 for v in structure_values if v.lower() == needle or v.lower().startswith(prefix))


def compute_access_status(
    assignment: IdentityAttributeAssignment,
    *,
    prefix: str,
    separator: str,
    meta: GapMeta | None,
    structure_values: set[str],
    groups_created_since_sync: set[str],
    event: MembershipEvent | None,
    member_items: list[GapItem],
    now: datetime,
) -> AccessStatus:
    """Combine membership and structure state for one data-access assignment.

    Args:
        assignment: Data-access assignment (any state).
        prefix: Managed group prefix.
        separator: Hierarchy separator.
        meta: Last stored gap metadata, or None before the first sync.
        structure_values: Values with outstanding group, nesting, or grant work.
        groups_created_since_sync: Lowercase groups created by approvals after the last sync.
        event: Newest immediate membership event for this assignment, if any.
        member_items: Outstanding membership items for this assignee.
        now: Current time (expiry check).

    Returns:
        The assignment's access status.
    """
    value = assignment.attribute_value.strip()
    base = {
        "assignment_id": assignment.assignment_id,
        "principal_name": assignment.principal_name,
        "value": value,
    }
    if value == ALL_VALUES:
        return AccessStatus(
            **base,
            membership=MembershipState.NOT_APPLICABLE,
            structure=StructureState.NOT_APPLICABLE,
            detail="Wildcard values do not map to a single group",
        )
    group = group_name(prefix, value)
    if group is None:
        return AccessStatus(
            **base,
            membership=MembershipState.NOT_APPLICABLE,
            structure=StructureState.NOT_APPLICABLE,
            detail="Value cannot be turned into a group name",
        )
    group_l = group.lower()
    principal_l = assignment.principal_name.lower()
    mine = {
        i.kind: i
        for i in member_items
        if (i.group_name or "").lower() == group_l and (i.principal_name or "").lower() == principal_l
    }
    pending_kinds = set(mine)

    if assignment.revoked_at is not None or (
        assignment.expires_at is not None and assignment.expires_at <= now
    ):
        removed = (
            event is not None and event.action == "remove" and event.status == MembershipEventStatus.APPLIED
        )
        if removed and GapKind.MEMBER_REMOVE not in pending_kinds:
            membership, detail = MembershipState.REMOVED, "Removed from the group when revoked"
        elif event is not None and event.action == "remove" and event.status == MembershipEventStatus.FAILED:
            membership, detail = (
                MembershipState.FAILED,
                f"Removal failed; the next sync retries: {event.error}",
            )
        elif assignment.revoked_at is None:
            membership, detail = (
                MembershipState.REMOVAL_PENDING,
                "Expired; removed from the group at the next sync",
            )
        else:
            membership, detail = (
                MembershipState.REMOVAL_PENDING,
                "Revoked; removed from the group at the next sync",
            )
        return AccessStatus(
            **base,
            group_name=group,
            membership=membership,
            structure=StructureState.NOT_APPLICABLE,
            detail=detail,
        )

    notes: list[str] = []
    if GapKind.MEMBER_ADD in pending_kinds:
        item = mine[GapKind.MEMBER_ADD]
        if item.status.value == "failed":
            membership = MembershipState.FAILED
            notes.append(f"Adding to {group} failed; the next sync retries: {item.error}")
        else:
            membership = MembershipState.PENDING_SYNC
            notes.append(f"Added to {group} at the next sync")
    elif event is not None and event.action == "add" and event.status == MembershipEventStatus.APPLIED:
        membership = MembershipState.MEMBER
    elif event is not None and event.action == "add" and event.status == MembershipEventStatus.FAILED:
        membership = MembershipState.FAILED
        notes.append(f"Adding to {group} failed; the next sync retries: {event.error}")
    elif event is not None and event.action == "add":
        membership = MembershipState.PENDING_SYNC
        notes.append(event.error or f"Added to {group} at the next sync")
    elif meta is None:
        membership = MembershipState.UNKNOWN
        notes.append("No sync has run yet")
    elif assignment.created_at > meta.computed_at:
        membership = MembershipState.PENDING_SYNC
        notes.append(f"Added to {group} at the next sync")
    else:
        membership = MembershipState.MEMBER

    pending = _path_pending(value, separator, structure_values)
    if meta is None:
        structure = StructureState.UNKNOWN
    elif group_l in groups_created_since_sync:
        structure = StructureState.PENDING_SYNC
        notes.append("Group was created at approval; nesting and table grants are added by the next sync")
    elif pending:
        structure = StructureState.PENDING_SYNC
        notes.append(
            f"{pending} path(s) under {value} still need groups, nesting, or grants from the next sync"
        )
    else:
        structure = StructureState.READY

    if not notes and membership == MembershipState.MEMBER and structure == StructureState.READY:
        notes.append(f"Member of {group}; groups and grants are in place")
    return AccessStatus(
        **base, group_name=group, membership=membership, structure=structure, detail=" ".join(notes)
    )

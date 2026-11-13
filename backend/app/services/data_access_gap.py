"""Pure helpers: full plan → outstanding items, and tag inventory → snapshot and diff.

No Databricks or storage calls. The sync stores the full outstanding list (not the 25-row
plan samples) so admins can page through every change, and diffs table tagging between
syncs so they can see which tables changed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from backend.app.models.data_access import (
    DataAccessPlan,
    DataAccessSettings,
    MembershipAction,
    PlannedGrant,
)
from backend.app.models.data_access_pending import (
    GapItem,
    GapKind,
    GapStatus,
    TagChange,
    TagChangeKind,
    TagSnapshotRow,
)
from backend.app.services.data_access_planner import (
    _SYSTEM_CATALOGS,
    ActualMember,
    TagRow,
    normalize_privilege,
)


def group_key(kind: GapKind, group: str) -> str:
    """Key for a group create item."""
    return f"{kind.value}|{group.lower()}"


def nest_key(child: str, parent: str) -> str:
    """Key for a parent-into-child nesting item."""
    return f"{GapKind.GROUP_NEST.value}|{child.lower()}|{parent.lower()}"


def member_key(action: MembershipAction | str, group: str, principal: str) -> str:
    """Key for a member add or remove item."""
    kind = GapKind.MEMBER_ADD if str(getattr(action, "value", action)) == "add" else GapKind.MEMBER_REMOVE
    return f"{kind.value}|{group.lower()}|{principal.lower()}"


def grant_key(kind: GapKind, grant: PlannedGrant) -> str:
    """Key for a grant add or revoke item."""
    return (
        f"{kind.value}|{grant.securable_type.lower()}|{grant.full_name.lower()}|"
        f"{grant.principal.lower()}|{normalize_privilege(grant.privilege)}"
    )


def missing_nesting(plan: DataAccessPlan, actual_members: list[ActualMember]) -> list[tuple[str, str, str]]:
    """Return ``(child, parent, value)`` nesting links the plan wants but the account lacks.

    Args:
        plan: Full plan from ``plan_data_access``.
        actual_members: Current members of managed groups (GROUP members carry nesting).

    Returns:
        Missing links, sorted by child group.
    """
    present = {
        (m.group_name.lower(), m.principal_name.lower())
        for m in actual_members
        if (m.principal_type or "").upper() == "GROUP"
    }
    out = []
    for group in plan.groups:
        if group.parent_group and (group.name.lower(), group.parent_group.lower()) not in present:
            out.append((group.name, group.parent_group, group.value))
    return sorted(out, key=lambda row: row[0].lower())


def build_gap(
    plan: DataAccessPlan,
    actual_members: list[ActualMember],
    *,
    applied: bool,
    failures: dict[str, str] | None,
    run_id: str,
    computed_at: datetime,
) -> list[GapItem]:
    """Turn a full plan into the stored outstanding list.

    A dry run leaves everything outstanding. After apply, only items whose key is in
    ``failures`` remain (with the error), since everything else was just applied.

    Args:
        plan: Full (untruncated) plan.
        actual_members: Membership inventory loaded before apply.
        applied: True when this run applied the plan.
        failures: Item key → error for steps that failed during apply.
        run_id: Run that computed the plan.
        computed_at: Inventory time.

    Returns:
        Outstanding items.
    """
    errors = failures or {}
    value_by_group = {g.name.lower(): g.value for g in plan.groups}
    items: list[GapItem] = []

    def add(item: GapItem) -> None:
        if not applied:
            items.append(item)
            return
        error = errors.get(item.item_key)
        if error is not None:
            items.append(item.model_copy(update={"status": GapStatus.FAILED, "error": error}))

    base = {"run_id": run_id, "computed_at": computed_at}
    for group in plan.groups:
        if not group.exists:
            add(
                GapItem(
                    item_key=group_key(GapKind.GROUP_CREATE, group.name),
                    kind=GapKind.GROUP_CREATE,
                    group_name=group.name,
                    value=group.value,
                    reason="group does not exist in the account",
                    **base,
                )
            )
    for child, parent, value in missing_nesting(plan, actual_members):
        add(
            GapItem(
                item_key=nest_key(child, parent),
                kind=GapKind.GROUP_NEST,
                group_name=child,
                principal_name=parent,
                principal_type="GROUP",
                value=value,
                reason=f"{parent} must be a member of {child} so parent access includes this path",
                **base,
            )
        )
    for change in plan.membership_changes:
        kind = GapKind.MEMBER_ADD if change.action == MembershipAction.ADD else GapKind.MEMBER_REMOVE
        add(
            GapItem(
                item_key=member_key(change.action, change.group, change.principal_name),
                kind=kind,
                group_name=change.group,
                principal_name=change.principal_name,
                principal_type=change.principal_type,
                value=value_by_group.get(change.group.lower()),
                reason=change.reason,
                **base,
            )
        )
    for kind, grants in (
        (GapKind.GRANT_ADD, plan.grants_to_add),
        (GapKind.GRANT_REVOKE, plan.grants_to_revoke),
    ):
        for grant in grants:
            add(
                GapItem(
                    item_key=grant_key(kind, grant),
                    kind=kind,
                    group_name=grant.principal,
                    securable_type=grant.securable_type,
                    full_name=grant.full_name,
                    privilege=normalize_privilege(grant.privilege),
                    value=value_by_group.get(grant.principal.lower()),
                    reason=grant.reason,
                    **base,
                )
            )
    return items


def snapshot_from_tags(settings: DataAccessSettings, tags: list[TagRow]) -> list[TagSnapshotRow]:
    """Collapse table-level tag rows into one snapshot row per table carrying the access tag.

    Uses the same rules as the planner: the access tag and the review tag must both be on
    the table itself for it to be eligible.

    Args:
        settings: Data-access settings (tag keys and review value).
        tags: Inventory tag rows.

    Returns:
        Snapshot rows sorted by full name.
    """
    access_key = settings.access_tag_key.lower()
    review_key = settings.review_tag_key.lower()
    review_value = settings.review_tag_value.strip().lower()
    by_table: dict[str, dict[str, str]] = defaultdict(dict)
    names: dict[str, str] = {}
    for row in tags:
        if row.level != "table" or row.catalog.lower() in _SYSTEM_CATALOGS:
            continue
        key = row.full_name.lower()
        names[key] = row.full_name
        by_table[key][row.tag_name.strip().lower()] = row.tag_value.strip()
    rows = []
    for key, tags_on in by_table.items():
        access = tags_on.get(access_key)
        if not access:
            continue
        reviewed = (tags_on.get(review_key) or "").lower() == review_value
        rows.append(
            TagSnapshotRow(full_name=names[key], access_value=access, reviewed=reviewed, eligible=reviewed)
        )
    return sorted(rows, key=lambda r: r.full_name.lower())


def diff_snapshots(
    previous: dict[str, TagSnapshotRow],
    current: list[TagSnapshotRow],
    *,
    run_id: str,
    detected_at: datetime,
) -> list[TagChange]:
    """Compare two snapshots and report tables whose grants would change.

    Only eligibility and the access value matter: a table that gains or loses the access
    tag or review, or whose value moves to another path.

    Args:
        previous: Last stored snapshot keyed by lowercase full name.
        current: New snapshot rows.
        run_id: Run detecting the change.
        detected_at: Inventory time.

    Returns:
        Changes sorted by full name.
    """
    current_by_key = {r.full_name.lower(): r for r in current}
    changes: list[TagChange] = []
    for key in sorted(set(previous) | set(current_by_key)):
        old = previous.get(key)
        new = current_by_key.get(key)
        old_eligible = bool(old and old.eligible)
        new_eligible = bool(new and new.eligible)
        full_name = (new or old).full_name  # type: ignore[union-attr]
        change: TagChangeKind | None = None
        if new_eligible and not old_eligible:
            change = TagChangeKind.NEWLY_ELIGIBLE
        elif old_eligible and not new_eligible:
            change = TagChangeKind.NO_LONGER_ELIGIBLE
        elif old_eligible and new_eligible and (old.access_value or "") != (new.access_value or ""):  # type: ignore[union-attr]
            change = TagChangeKind.VALUE_CHANGED
        if change is None:
            continue
        changes.append(
            TagChange(
                run_id=run_id,
                detected_at=detected_at,
                full_name=full_name,
                change=change,
                old_value=old.access_value if old_eligible and old else None,
                new_value=new.access_value if new_eligible and new else None,
            )
        )
    return changes

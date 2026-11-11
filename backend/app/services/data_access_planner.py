"""Pure planner: tagged objects + current grants → desired groups and GRANT/REVOKE diffs.

Does not call Databricks. Callers supply inventory rows so tests can cover the rules
without a warehouse: data privileges (SELECT) only on reviewed tagged tables, traversal
grants for those tables, nested groups via longest known parent prefix. Catalog/schema
tags still create groups for nesting but never receive SELECT.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from backend.app.models.data_access import (
    PLAN_ITEM_LIMIT,
    DataAccessConflict,
    DataAccessPlan,
    DataAccessPlanSummary,
    DataAccessSettings,
    PlannedGrant,
    PlannedGroup,
)

_GROUP_SLUG = re.compile(r"[^a-z0-9]+")
_SYSTEM_CATALOGS = frozenset({"system", "samples", "__databricks_internal"})


@dataclass(frozen=True)
class TagRow:
    """One tag assignment on a catalog, schema, or table.

    Attributes:
        level: ``catalog``, ``schema``, or ``table``.
        catalog: Catalog name.
        schema: Schema name; None for catalog-level tags.
        table: Table name; None unless ``level`` is ``table``.
        tag_name: Tag key as stored in information_schema.
        tag_value: Tag value.
    """

    level: str
    catalog: str
    schema: str | None
    table: str | None
    tag_name: str
    tag_value: str

    @property
    def full_name(self) -> str:
        """Return the Unity Catalog full name for this object."""
        if self.level == "catalog":
            return self.catalog
        if self.level == "schema":
            return f"{self.catalog}.{self.schema}"
        return f"{self.catalog}.{self.schema}.{self.table}"


@dataclass(frozen=True)
class ActualGrant:
    """A direct privilege assignment already present in Unity Catalog.

    Attributes:
        securable_type: ``catalog``, ``schema``, or ``table``.
        full_name: Object full name.
        principal: Grantee display name.
        privilege: Privilege type (e.g. ``SELECT``).
    """

    securable_type: str
    full_name: str
    principal: str
    privilege: str


@dataclass(frozen=True)
class _Object:
    level: str
    full_name: str
    catalog: str
    schema: str | None
    table: str | None


def group_name(prefix: str, value: str) -> str | None:
    """Build a managed group name from an access-tag value.

    Args:
        prefix: Group prefix from settings (e.g. ``dac_``).
        value: Raw tag value.

    Returns:
        ``prefix`` plus a lowercase slug, or None when the value slugs to empty.
    """
    slug = _GROUP_SLUG.sub("_", value.strip().lower()).strip("_")
    if not slug:
        return None
    head = prefix if prefix.endswith("_") else f"{prefix}_"
    return f"{head}{slug}"


def parent_value(value: str, separator: str, known: set[str]) -> str | None:
    """Return the longest proper prefix of ``value`` that is itself a known value.

    Args:
        value: Access-tag value (e.g. ``finance.budget``).
        separator: Hierarchy separator (e.g. ``.``).
        known: Values that have (or will have) a group.

    Returns:
        The parent value, or None when ``value`` is a root.
    """
    parts = value.split(separator)
    for end in range(len(parts) - 1, 0, -1):
        candidate = separator.join(parts[:end])
        if candidate in known:
            return candidate
    return None


def plan_data_access(
    settings: DataAccessSettings,
    tags: list[TagRow],
    actual_grants: list[ActualGrant],
    existing_groups: set[str],
    previous_fingerprint: str | None = None,
    previous_summary: DataAccessPlanSummary | None = None,
) -> DataAccessPlan:
    """Compute the dry-run plan from inventory snapshots.

    Args:
        settings: Current data-access settings.
        tags: Catalog/schema/table tag rows for the access and review keys.
        actual_grants: Direct grants whose principal matches the managed prefix.
        existing_groups: Group display names already in the account.
        previous_fingerprint: Fingerprint from the last successful run, if any.
        previous_summary: Summary from the last successful run, if any.

    Returns:
        Truncated plan with full summary counts and drift notes.
    """
    access_key = settings.access_tag_key.lower()
    review_key = settings.review_tag_key.lower()
    review_value = settings.review_tag_value.strip().lower()
    prefix = settings.group_prefix.lower()

    by_object: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    objects: dict[tuple[str, str], _Object] = {}
    for row in tags:
        if row.catalog.lower() in _SYSTEM_CATALOGS:
            continue
        if settings.allowed_catalogs:
            allowed = {c.lower() for c in settings.allowed_catalogs}
            if row.catalog.lower() not in allowed:
                continue
        obj = _Object(row.level, row.full_name, row.catalog, row.schema, row.table)
        key = (obj.level, obj.full_name.lower())
        objects[key] = obj
        by_object[key][row.tag_name.strip().lower()] = row.tag_value.strip()

    skipped_unreviewed = 0
    eligible: list[tuple[_Object, str]] = []
    for key, obj in objects.items():
        tags_on = by_object[key]
        access = tags_on.get(access_key)
        if not access:
            continue
        review = (tags_on.get(review_key) or "").lower()
        if review != review_value:
            skipped_unreviewed += 1
            continue
        eligible.append((obj, access))

    values = sorted({value for _, value in eligible})
    known_values = set(values)

    invalid_values: list[str] = []
    value_to_group: dict[str, str] = {}
    group_to_value: dict[str, str] = {}
    for value in values:
        name = group_name(settings.group_prefix, value)
        if name is None:
            invalid_values.append(value)
            continue
        other = group_to_value.get(name.lower())
        if other and other != value:
            invalid_values.append(value)
            continue
        value_to_group[value] = name
        group_to_value[name.lower()] = value

    existing_lower = {g.lower() for g in existing_groups}
    groups: list[PlannedGroup] = []
    for value in values:
        name = value_to_group.get(value)
        if name is None:
            continue
        parent = parent_value(value, settings.hierarchy_separator, known_values)
        parent_group = value_to_group.get(parent) if parent else None
        groups.append(
            PlannedGroup(
                name=name,
                value=value,
                parent_group=parent_group,
                exists=name.lower() in existing_lower,
            )
        )

    # Informational only: schema/catalog tags no longer drive SELECT.
    conflicts: list[DataAccessConflict] = []
    for obj, value in eligible:
        if obj.level == "table":
            continue
        conflicts.extend(
            _descendant_conflicts(
                obj, value, objects, by_object, access_key, review_key, review_value
            )
        )

    desired: set[tuple[str, str, str, str]] = set()
    reasons: dict[tuple[str, str, str, str], str] = {}
    for obj, value in eligible:
        if obj.level != "table":
            continue
        group = value_to_group.get(value)
        if group is None:
            continue
        for grant in _grants_for_table(obj, group, settings.data_privileges):
            key = (grant.securable_type, grant.full_name.lower(), grant.principal.lower(), grant.privilege)
            desired.add(key)
            reasons[key] = grant.reason

    actual: set[tuple[str, str, str, str]] = set()
    actual_display: dict[tuple[str, str, str, str], ActualGrant] = {}
    prefix_match = prefix if prefix.endswith("_") else f"{prefix}_"
    for grant in actual_grants:
        if not grant.principal.lower().startswith(prefix_match):
            continue
        key = (
            grant.securable_type.lower(),
            grant.full_name.lower(),
            grant.principal.lower(),
            grant.privilege.upper(),
        )
        actual.add(key)
        actual_display[key] = grant

    add_keys = sorted(desired - actual)
    revoke_keys = sorted(actual - desired)
    unchanged = len(desired & actual)

    grants_to_add = _restore_desired_grants(add_keys, reasons, groups, objects)

    grants_to_revoke = []
    for k in revoke_keys:
        found = actual_display[k]
        grants_to_revoke.append(
            PlannedGrant(
                securable_type=found.securable_type,
                full_name=found.full_name,
                principal=found.principal,
                privilege=found.privilege,
                reason="managed-prefix grant is not in the desired set",
            )
        )

    fingerprint = _fingerprint(desired, groups)
    drift = _drift(
        fingerprint,
        previous_fingerprint,
        previous_summary,
        DataAccessPlanSummary(
            eligible_objects=len(eligible),
            skipped_unreviewed=skipped_unreviewed,
            unique_values=len(values),
            groups_to_create=sum(1 for g in groups if not g.exists),
            groups_existing=sum(1 for g in groups if g.exists),
            grants_to_add=len(grants_to_add),
            grants_to_revoke=len(grants_to_revoke),
            grants_unchanged=unchanged,
            conflicts=len(conflicts),
            invalid_values=len(invalid_values),
        ),
    )

    summary = DataAccessPlanSummary(
        eligible_objects=len(eligible),
        skipped_unreviewed=skipped_unreviewed,
        unique_values=len(values),
        groups_to_create=sum(1 for g in groups if not g.exists),
        groups_existing=sum(1 for g in groups if g.exists),
        grants_to_add=len(grants_to_add),
        grants_to_revoke=len(grants_to_revoke),
        grants_unchanged=unchanged,
        conflicts=len(conflicts),
        invalid_values=len(invalid_values),
    )
    truncated = any(
        len(items) > PLAN_ITEM_LIMIT
        for items in (groups, grants_to_add, grants_to_revoke, conflicts, invalid_values)
    )
    return DataAccessPlan(
        summary=summary,
        fingerprint=fingerprint,
        groups=groups[:PLAN_ITEM_LIMIT],
        grants_to_add=grants_to_add[:PLAN_ITEM_LIMIT],
        grants_to_revoke=grants_to_revoke[:PLAN_ITEM_LIMIT],
        conflicts=conflicts[:PLAN_ITEM_LIMIT],
        invalid_values=invalid_values[:PLAN_ITEM_LIMIT],
        truncated=truncated,
        drift_since_last_run=drift,
    )


def _descendant_conflicts(
    ancestor: _Object,
    ancestor_value: str,
    objects: dict[tuple[str, str], _Object],
    by_object: dict[tuple[str, str], dict[str, str]],
    access_key: str,
    review_key: str,
    review_value: str,
) -> list[DataAccessConflict]:
    """Find descendants whose access tag disagrees with an ancestor grant."""
    found: list[DataAccessConflict] = []
    for obj in objects.values():
        if not _is_descendant(ancestor, obj):
            continue
        tags_on = by_object[(obj.level, obj.full_name.lower())]
        child_value = tags_on.get(access_key)
        if not child_value:
            continue
        child_review = (tags_on.get(review_key) or "").lower()
        if child_value.lower() != ancestor_value.lower():
            found.append(
                DataAccessConflict(
                    ancestor_type=ancestor.level,
                    ancestor_name=ancestor.full_name,
                    ancestor_value=ancestor_value,
                    descendant_name=obj.full_name,
                    descendant_value=child_value,
                    reason=(
                        f"Granting at {ancestor.level} {ancestor.full_name} "
                        f"({ancestor_value}) would also expose {obj.full_name} "
                        f"tagged {child_value}"
                    ),
                )
            )
        elif child_review != review_value:
            found.append(
                DataAccessConflict(
                    ancestor_type=ancestor.level,
                    ancestor_name=ancestor.full_name,
                    ancestor_value=ancestor_value,
                    descendant_name=obj.full_name,
                    descendant_value=child_value,
                    reason=(
                        f"Granting at {ancestor.level} {ancestor.full_name} would expose "
                        f"unreviewed descendant {obj.full_name}"
                    ),
                )
            )
    return found


def _is_descendant(ancestor: _Object, obj: _Object) -> bool:
    """Return whether ``obj`` sits under ``ancestor`` in the UC tree."""
    if obj.full_name.lower() == ancestor.full_name.lower():
        return False
    if ancestor.level == "catalog":
        return obj.catalog.lower() == ancestor.catalog.lower()
    if ancestor.level == "schema":
        return (
            obj.level == "table"
            and obj.catalog.lower() == ancestor.catalog.lower()
            and (obj.schema or "").lower() == (ancestor.schema or "").lower()
        )
    return False


def _grants_for_table(obj: _Object, group: str, data_privileges: list[str]) -> list[PlannedGrant]:
    """Build USE CATALOG / USE SCHEMA plus table data privileges for one tagged table."""
    grants: list[PlannedGrant] = []
    reason = f"tagged table {obj.full_name}"
    grants.append(
        PlannedGrant(
            securable_type="catalog",
            full_name=obj.catalog,
            principal=group,
            privilege="USE CATALOG",
            reason=reason,
        )
    )
    schema_name = f"{obj.catalog}.{obj.schema}"
    grants.append(
        PlannedGrant(
            securable_type="schema",
            full_name=schema_name,
            principal=group,
            privilege="USE SCHEMA",
            reason=reason,
        )
    )
    for privilege in data_privileges:
        grants.append(
            PlannedGrant(
                securable_type="table",
                full_name=obj.full_name,
                principal=group,
                privilege=privilege,
                reason=reason,
            )
        )
    return grants


def _restore_desired_grants(
    keys: list[tuple[str, str, str, str]],
    reasons: dict[tuple[str, str, str, str], str],
    groups: list[PlannedGroup],
    objects: dict[tuple[str, str], _Object],
) -> list[PlannedGrant]:
    """Rebuild PlannedGrant rows with original casing from the object map."""
    object_by_lower = {k[1]: obj.full_name for k, obj in objects.items()}
    catalog_by_lower = {obj.catalog.lower(): obj.catalog for obj in objects.values()}
    schema_by_lower = {
        f"{obj.catalog}.{obj.schema}".lower(): f"{obj.catalog}.{obj.schema}"
        for obj in objects.values()
        if obj.schema
    }
    principal_by_lower = {g.name.lower(): g.name for g in groups}
    out: list[PlannedGrant] = []
    for securable_type, name_l, principal_l, privilege in keys:
        if securable_type == "catalog":
            full_name = catalog_by_lower.get(name_l, name_l)
        elif securable_type == "schema":
            full_name = schema_by_lower.get(name_l, name_l)
        else:
            full_name = object_by_lower.get(name_l, name_l)
        out.append(
            PlannedGrant(
                securable_type=securable_type,
                full_name=full_name,
                principal=principal_by_lower.get(principal_l, principal_l),
                privilege=privilege,
                reason=reasons.get((securable_type, name_l, principal_l, privilege), ""),
            )
        )
    return out


def _fingerprint(desired: set[tuple[str, str, str, str]], groups: list[PlannedGroup]) -> str:
    """Hash the desired grant set and group nesting so later runs can detect drift."""
    parts = [f"g:{g.name}:{g.parent_group or ''}" for g in sorted(groups, key=lambda x: x.name)]
    parts.extend(f"{a}|{b}|{c}|{d}" for a, b, c, d in sorted(desired))
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return digest[:16]


def _drift(
    fingerprint: str,
    previous_fingerprint: str | None,
    previous: DataAccessPlanSummary | None,
    current: DataAccessPlanSummary,
) -> list[str]:
    """Describe how this plan differs from the last successful run."""
    if previous_fingerprint is None or previous is None:
        return ["No previous successful dry-run to compare"]
    if fingerprint == previous_fingerprint:
        return ["Desired state matches the last successful dry-run"]
    notes: list[str] = ["Desired state changed since the last successful dry-run"]
    pairs = [
        ("eligible objects", previous.eligible_objects, current.eligible_objects),
        ("grants to add", previous.grants_to_add, current.grants_to_add),
        ("grants to revoke", previous.grants_to_revoke, current.grants_to_revoke),
        ("conflicts", previous.conflicts, current.conflicts),
        ("groups to create", previous.groups_to_create, current.groups_to_create),
    ]
    for label, old, new in pairs:
        if old != new:
            notes.append(f"{label}: {old} → {new}")
    return notes

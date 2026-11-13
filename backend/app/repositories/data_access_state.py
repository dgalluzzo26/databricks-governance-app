"""Storage for outstanding data-access work, the tag snapshot, and immediate membership events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any

from backend.app.models.data_access_pending import (
    GapItem,
    GapKind,
    GapMeta,
    MembershipEvent,
    TagChange,
    TagSnapshotRow,
)
from backend.app.services.table_query import TableQuery, sort_and_filter

STRUCTURE_KINDS = (GapKind.GROUP_CREATE, GapKind.GROUP_NEST, GapKind.GRANT_ADD)
MEMBER_KINDS = (GapKind.MEMBER_ADD, GapKind.MEMBER_REMOVE)


def _matches_query(item: GapItem, needle: str) -> bool:
    """True when any searchable field of ``item`` contains ``needle`` (lowercase)."""
    fields = (item.group_name, item.principal_name, item.full_name, item.value)
    return any(needle in (f or "").lower() for f in fields)


# In-memory sort and filter readers. Keys match the frontend column keys and the SQL maps in
# ``lakebase.py`` so both backends sort and filter the same text.
GAP_ITEM_READERS: dict[str, Callable[[GapItem], Any]] = {
    "kind": lambda item: item.kind.value,
    "change": lambda item: " ".join(
        part
        for part in (
            item.group_name,
            item.principal_name,
            item.full_name,
            item.privilege,
            item.securable_type,
        )
        if part
    ),
    "path": lambda item: item.value,
    "status": lambda item: item.status.value,
    "reason": lambda item: " ".join(part for part in (item.reason, item.error) if part),
}
TAG_CHANGE_READERS: dict[str, Callable[[TagChange], Any]] = {
    "table": lambda change: change.full_name,
    "change": lambda change: change.change.value,
    "before": lambda change: change.old_value,
    "after": lambda change: change.new_value,
    "detected": lambda change: change.detected_at,
}


class DataAccessStateRepository(ABC):
    """Outstanding sync work (the gap), last-seen table tagging, and membership events."""

    @abstractmethod
    def replace_gap(self, items: list[GapItem], meta: GapMeta) -> None:
        """Replace every outstanding item and the gap metadata in one step.

        Args:
            items: Full outstanding list computed by a sync run.
            meta: Run that computed it.
        """

    @abstractmethod
    def gap_meta(self) -> GapMeta | None:
        """Return metadata of the last stored gap, or None before the first sync."""

    @abstractmethod
    def gap_counts(self) -> dict[str, int]:
        """Return outstanding item counts keyed by kind, plus ``failed`` for failed items."""

    @abstractmethod
    def gap_page(
        self,
        *,
        kind: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
        table_query: TableQuery | None = None,
    ) -> tuple[list[GapItem], int]:
        """Return one page of outstanding items and the matching total.

        Args:
            kind: Optional kind filter.
            status: Optional ``pending`` or ``failed`` filter.
            query: Optional case-insensitive substring over group, principal, securable, and value.
            limit: Page size.
            offset: Rows to skip.
            table_query: Optional column sort and filters.

        Returns:
            The page rows (ordered by kind then key) and the total.
        """

    @abstractmethod
    def gap_structure_values(self) -> set[str]:
        """Return distinct access values that still have group, nesting, or grant work."""

    @abstractmethod
    def member_gap_items(self, principal_names: list[str]) -> list[GapItem]:
        """Return outstanding membership items for the given principals (case-insensitive)."""

    @abstractmethod
    def upsert_gap_items(self, items: list[GapItem]) -> None:
        """Insert or replace outstanding items by ``item_key``."""

    @abstractmethod
    def delete_gap_items(self, item_keys: list[str]) -> None:
        """Delete outstanding items by ``item_key`` (missing keys are ignored)."""

    @abstractmethod
    def load_tag_snapshot(self) -> dict[str, TagSnapshotRow]:
        """Return the last-seen tagging keyed by lowercase table full name."""

    @abstractmethod
    def replace_tag_snapshot(self, rows: list[TagSnapshotRow]) -> None:
        """Replace the stored tag snapshot."""

    @abstractmethod
    def add_tag_changes(self, changes: list[TagChange]) -> None:
        """Append detected tag changes."""

    @abstractmethod
    def tag_changes_page(
        self,
        *,
        change: str | None,
        run_id: str | None,
        limit: int,
        offset: int,
        table_query: TableQuery | None = None,
    ) -> tuple[list[TagChange], int]:
        """Return one page of tag changes, newest first, and the matching total."""

    @abstractmethod
    def tag_change_counts(self, run_id: str) -> dict[str, int]:
        """Return tag change counts by change kind for one run."""

    @abstractmethod
    def prune_tag_changes(self, before: datetime) -> int:
        """Delete tag changes detected before ``before``; return rows removed."""

    @abstractmethod
    def record_membership_event(self, event: MembershipEvent) -> None:
        """Append one immediate membership event."""

    @abstractmethod
    def latest_membership_events(self, assignment_ids: list[str]) -> dict[str, MembershipEvent]:
        """Return the newest event per assignment id."""

    @abstractmethod
    def membership_events_since(self, since: datetime) -> list[MembershipEvent]:
        """Return events recorded at or after ``since``, oldest first."""

    @abstractmethod
    def prune_membership_events(self, before: datetime) -> int:
        """Delete events recorded before ``before``; return rows removed."""


class InMemoryDataAccessStateRepository(DataAccessStateRepository):
    """Process-local state for the memory and Delta backends and for tests."""

    def __init__(self) -> None:
        self._gap: dict[str, GapItem] = {}
        self._meta: GapMeta | None = None
        self._snapshot: dict[str, TagSnapshotRow] = {}
        self._changes: list[TagChange] = []
        self._events: list[MembershipEvent] = []

    def replace_gap(self, items: list[GapItem], meta: GapMeta) -> None:
        """Swap the outstanding list and metadata."""
        self._gap = {i.item_key: i for i in items}
        self._meta = meta

    def gap_meta(self) -> GapMeta | None:
        """Return the stored metadata."""
        return self._meta

    def gap_counts(self) -> dict[str, int]:
        """Count items by kind and failed status."""
        counts: dict[str, int] = {}
        for item in self._gap.values():
            counts[item.kind.value] = counts.get(item.kind.value, 0) + 1
            if item.status.value == "failed":
                counts["failed"] = counts.get("failed", 0) + 1
        return counts

    def gap_page(
        self,
        *,
        kind: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
        table_query: TableQuery | None = None,
    ) -> tuple[list[GapItem], int]:
        """Filter, order, and slice the outstanding list."""
        needle = (query or "").strip().lower()
        rows = [
            i
            for i in self._gap.values()
            if (not kind or i.kind.value == kind)
            and (not status or i.status.value == status)
            and (not needle or _matches_query(i, needle))
        ]
        rows = sort_and_filter(
            rows,
            table_query or TableQuery(),
            GAP_ITEM_READERS,
            default_key=lambda i: (i.kind.value, i.item_key),
        )
        return rows[offset : offset + limit], len(rows)

    def gap_structure_values(self) -> set[str]:
        """Collect values that still have structural work."""
        return {i.value for i in self._gap.values() if i.kind in STRUCTURE_KINDS and i.value}

    def member_gap_items(self, principal_names: list[str]) -> list[GapItem]:
        """Membership items for the given principals."""
        wanted = {p.lower() for p in principal_names}
        return [
            i
            for i in self._gap.values()
            if i.kind in MEMBER_KINDS and (i.principal_name or "").lower() in wanted
        ]

    def upsert_gap_items(self, items: list[GapItem]) -> None:
        """Insert or replace by key."""
        for item in items:
            self._gap[item.item_key] = item

    def delete_gap_items(self, item_keys: list[str]) -> None:
        """Remove by key."""
        for key in item_keys:
            self._gap.pop(key, None)

    def load_tag_snapshot(self) -> dict[str, TagSnapshotRow]:
        """Return a copy of the snapshot."""
        return dict(self._snapshot)

    def replace_tag_snapshot(self, rows: list[TagSnapshotRow]) -> None:
        """Swap the snapshot."""
        self._snapshot = {r.full_name.lower(): r for r in rows}

    def add_tag_changes(self, changes: list[TagChange]) -> None:
        """Append changes."""
        self._changes.extend(changes)

    def tag_changes_page(
        self,
        *,
        change: str | None,
        run_id: str | None,
        limit: int,
        offset: int,
        table_query: TableQuery | None = None,
    ) -> tuple[list[TagChange], int]:
        """Filter, order newest first (unless sorted), and slice."""
        rows = [
            c
            for c in self._changes
            if (not change or c.change.value == change) and (not run_id or c.run_id == run_id)
        ]
        rows = sort_and_filter(
            rows,
            table_query or TableQuery(),
            TAG_CHANGE_READERS,
            default_key=lambda c: (c.detected_at, c.full_name),
            default_descending=True,
        )
        return rows[offset : offset + limit], len(rows)

    def tag_change_counts(self, run_id: str) -> dict[str, int]:
        """Count one run's changes by kind."""
        counts: dict[str, int] = {}
        for c in self._changes:
            if c.run_id == run_id:
                counts[c.change.value] = counts.get(c.change.value, 0) + 1
        return counts

    def prune_tag_changes(self, before: datetime) -> int:
        """Drop changes older than ``before``."""
        kept = [c for c in self._changes if c.detected_at >= before]
        removed = len(self._changes) - len(kept)
        self._changes = kept
        return removed

    def record_membership_event(self, event: MembershipEvent) -> None:
        """Append an event."""
        self._events.append(event)

    def latest_membership_events(self, assignment_ids: list[str]) -> dict[str, MembershipEvent]:
        """Newest event per assignment."""
        wanted = set(assignment_ids)
        latest: dict[str, MembershipEvent] = {}
        for event in self._events:
            if event.assignment_id not in wanted:
                continue
            current = latest.get(event.assignment_id)
            if current is None or event.recorded_at >= current.recorded_at:
                latest[event.assignment_id] = event
        return latest

    def membership_events_since(self, since: datetime) -> list[MembershipEvent]:
        """Events at or after ``since``."""
        return sorted((e for e in self._events if e.recorded_at >= since), key=lambda e: e.recorded_at)

    def prune_membership_events(self, before: datetime) -> int:
        """Drop events older than ``before``."""
        kept = [e for e in self._events if e.recorded_at >= before]
        removed = len(self._events) - len(kept)
        self._events = kept
        return removed

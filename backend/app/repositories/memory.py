"""In-process, non-persistent Unity Catalog governance storage.

Used for local development and tests. All repositories keep data in thread-safe
dictionaries that are discarded when the process exits.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from backend.app.models.access_request import AccessRequest, AccessRequestStatus, CategoryApprover
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    IdentityAttributeAssignment,
)
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.models.policy_exception import PolicyException
from backend.app.repositories.base import (
    AccessRequestRepository,
    AuditRepository,
    CategoryApproverRepository,
    IdentityAttributeRepository,
    MaskRuleRepository,
    PolicyExceptionRepository,
    SettingsRepository,
    StorageProvider,
)


class InMemorySettingsRepository(SettingsRepository):
    """Thread-safe key/value store for application settings."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_all(self) -> dict[str, Any]:
        """Return a shallow copy of all stored settings."""
        with self._lock:
            return dict(self._values)

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        """Merge ``values`` into the in-memory settings map.

        Args:
            values: Settings keys and values to upsert.
            actor: Principal performing the write; accepted for the interface, not stored.
        """
        with self._lock:
            self._values.update(values)


class InMemoryAuditRepository(AuditRepository):
    """Thread-safe in-memory list of audit events."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        """Append ``event`` to the in-memory audit log.

        Args:
            event: Audit record to retain until process exit.
        """
        with self._lock:
            self._events.append(event)

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Return the newest events, newest first.

        Args:
            limit: Maximum number of events to return.

        Returns:
            A new list of at most ``limit`` events ordered by ``created_at`` descending.
        """
        with self._lock:
            return sorted(self._events, key=lambda e: e.created_at, reverse=True)[:limit]


class InMemoryIdentityAttributeRepository(IdentityAttributeRepository):
    """Thread-safe store for attribute definitions and principal assignments."""

    def __init__(self) -> None:
        self._definitions: dict[str, AttributeDefinition] = {}
        self._assignments: dict[str, IdentityAttributeAssignment] = {}
        self._lock = threading.Lock()

    def list_definitions(self) -> list[AttributeDefinition]:
        """Return all definitions sorted by attribute key."""
        with self._lock:
            return sorted(self._definitions.values(), key=lambda d: d.key)

    def get_definition(self, key: str) -> AttributeDefinition | None:
        """Return the definition for ``key``, or None if it is not stored.

        Args:
            key: Attribute definition key.

        Returns:
            The matching definition, or None.
        """
        with self._lock:
            return self._definitions.get(key)

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        """Insert or replace the definition keyed by ``definition.key``.

        Args:
            definition: Definition to store.

        Returns:
            The same ``definition`` instance that was stored.
        """
        with self._lock:
            self._definitions[definition.key] = definition
            return definition

    def delete_definition(self, key: str) -> bool:
        """Remove the definition for ``key`` if it exists.

        Args:
            key: Attribute definition key.

        Returns:
            True if a definition was removed, False if ``key`` was unknown.
        """
        with self._lock:
            return self._definitions.pop(key, None) is not None

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        """Return assignments matching ``criteria``, newest first.

        Snapshot is taken under the lock; filtering runs on the copy. Principal
        name comparison is case-insensitive. Inactive assignments are omitted
        unless ``criteria.include_inactive`` is true.

        Args:
            criteria: Optional filters for principal, attribute, and active status.

        Returns:
            Matching assignments ordered by ``created_at`` descending.
        """
        with self._lock:
            items = list(self._assignments.values())
        if criteria.principal_name:
            needle = criteria.principal_name.lower()
            items = [a for a in items if a.principal_name.lower() == needle]
        if criteria.principal_search:
            contains = criteria.principal_search.lower()
            items = [a for a in items if contains in a.principal_name.lower()]
        if criteria.principal_type:
            items = [a for a in items if a.principal_type == criteria.principal_type]
        if criteria.attribute_key:
            items = [a for a in items if a.attribute_key == criteria.attribute_key]
        if not criteria.include_inactive:
            items = [a for a in items if a.active]
        return sorted(items, key=lambda a: a.created_at, reverse=True)

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        """Return the assignment with ``assignment_id``, or None if missing.

        Args:
            assignment_id: Assignment identifier.

        Returns:
            The stored assignment, or None.
        """
        with self._lock:
            return self._assignments.get(assignment_id)

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        """Store ``assignment`` keyed by ``assignment.assignment_id``, replacing any prior row.

        Args:
            assignment: Assignment to insert or replace.

        Returns:
            The same ``assignment`` instance that was stored.
        """
        with self._lock:
            self._assignments[assignment.assignment_id] = assignment
        return assignment

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        """Mark the assignment as revoked without removing it.

        Args:
            assignment_id: Assignment to revoke.
            actor: Principal recorded as ``revoked_by``.
            at: Timestamp recorded as ``revoked_at``.

        Returns:
            The updated assignment, or None if ``assignment_id`` is unknown.
        """
        with self._lock:
            current = self._assignments.get(assignment_id)
            if current is None:
                return None
            updated = current.model_copy(update={"revoked_at": at, "revoked_by": actor})
            self._assignments[assignment_id] = updated
            return updated

    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        """Set ``expires_at`` and record ``actor`` as ``granted_by``.

        Args:
            assignment_id: Assignment to update.
            expires_at: New expiry timestamp.
            actor: Principal recorded as the granter.

        Returns:
            The updated assignment, or None if ``assignment_id`` is unknown.
        """
        with self._lock:
            current = self._assignments.get(assignment_id)
            if current is None:
                return None
            updated = current.model_copy(update={"expires_at": expires_at, "granted_by": actor})
            self._assignments[assignment_id] = updated
            return updated


class InMemoryCategoryApproverRepository(CategoryApproverRepository):
    """Thread-safe store of category-to-approver mappings."""

    def __init__(self) -> None:
        self._items: dict[str, CategoryApprover] = {}
        self._lock = threading.Lock()

    def list_approvers(
        self,
        category_path: str | None = None,
        *,
        attribute_key: str | None = None,
    ) -> list[CategoryApprover]:
        """Return approvers, optionally filtered by path and/or attribute key.

        Matching is case-insensitive. Results are sorted by attribute, path, principal
        type, then principal name.

        Args:
            category_path: When set, only approvers for this path are returned.
            attribute_key: When set, only approvers for this attribute are returned.

        Returns:
            Matching approvers in stable sort order.
        """
        with self._lock:
            items = list(self._items.values())
        if attribute_key:
            key = attribute_key.lower()
            items = [a for a in items if a.attribute_key.lower() == key]
        if category_path:
            needle = category_path.lower()
            items = [a for a in items if a.category_path.lower() == needle]
        return sorted(
            items,
            key=lambda a: (
                a.attribute_key.lower(),
                a.category_path.lower(),
                a.principal_type.value,
                a.principal_name.lower(),
            ),
        )

    def list_configured_categories(self, *, attribute_key: str | None = None) -> list[str]:
        """Return unique paths with at least one approver.

        First-seen casing is preserved; the list is ordered case-insensitively.

        Args:
            attribute_key: When set, only paths for this attribute are returned.

        Returns:
            Distinct category / dimension paths.
        """
        with self._lock:
            items = list(self._items.values())
        if attribute_key:
            key = attribute_key.lower()
            items = [a for a in items if a.attribute_key.lower() == key]
        paths = {a.category_path for a in items}
        by_lower: dict[str, str] = {}
        for path in paths:
            by_lower.setdefault(path.lower(), path)
        return [by_lower[k] for k in sorted(by_lower)]

    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        """Return the approver with ``approver_id``, or None if missing.

        Args:
            approver_id: Approver identifier.

        Returns:
            The stored approver, or None.
        """
        with self._lock:
            return self._items.get(approver_id)

    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        """Insert ``approver`` unless an equivalent row already exists.

        Equivalence is case-insensitive category path and principal name plus
        matching principal type. An existing match is returned unchanged.

        Args:
            approver: Approver to insert.

        Returns:
            The existing equivalent row, or the newly stored ``approver``.
        """
        with self._lock:
            for existing in self._items.values():
                if (
                    existing.attribute_key.lower() == approver.attribute_key.lower()
                    and existing.category_path.lower() == approver.category_path.lower()
                    and existing.principal_name.lower() == approver.principal_name.lower()
                    and existing.principal_type == approver.principal_type
                ):
                    return existing
            self._items[approver.approver_id] = approver
            return approver

    def delete_approver(self, approver_id: str) -> bool:
        """Remove the approver with ``approver_id`` if it exists.

        Args:
            approver_id: Approver identifier.

        Returns:
            True if a row was removed, False if ``approver_id`` was unknown.
        """
        with self._lock:
            return self._items.pop(approver_id, None) is not None


class InMemoryAccessRequestRepository(AccessRequestRepository):
    """Thread-safe store of access requests and renewal-dismissal markers."""

    def __init__(self) -> None:
        self._items: dict[str, AccessRequest] = {}
        self._dismissals: dict[tuple[str, datetime], tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def create(self, request: AccessRequest) -> AccessRequest:
        """Insert ``request`` keyed by ``request.request_id``.

        Args:
            request: Access request to store.

        Returns:
            The same ``request`` instance that was stored.
        """
        with self._lock:
            self._items[request.request_id] = request
        return request

    def get(self, request_id: str) -> AccessRequest | None:
        """Return the request with ``request_id``, or None if missing.

        Args:
            request_id: Request identifier.

        Returns:
            The stored request, or None.
        """
        with self._lock:
            return self._items.get(request_id)

    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        """Return requests whose requester matches ``requester_name`` (case-insensitive).

        Args:
            requester_name: Requester principal name.

        Returns:
            Matching requests ordered by ``created_at`` descending.
        """
        needle = requester_name.lower()
        with self._lock:
            items = [r for r in self._items.values() if r.requester_name.lower() == needle]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        """Return requests in ``status``, newest first.

        Args:
            status: Status to match exactly.

        Returns:
            Matching requests ordered by ``created_at`` descending.
        """
        with self._lock:
            items = [r for r in self._items.values() if r.status == status]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def save(self, request: AccessRequest) -> AccessRequest:
        """Replace the stored row for ``request.request_id``.

        Args:
            request: Request to persist.

        Returns:
            The same ``request`` instance that was stored.
        """
        with self._lock:
            self._items[request.request_id] = request
        return request

    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool:
        """Return whether a pending request exists for the given attribute grant.

        Requester name and attribute value are compared case-insensitively;
        ``attribute_key`` is compared exactly.

        Args:
            requester_name: Requester principal name.
            attribute_key: Attribute definition key.
            attribute_value: Requested attribute value.

        Returns:
            True if a matching PENDING request is stored.
        """
        with self._lock:
            for item in self._items.values():
                if (
                    item.status == AccessRequestStatus.PENDING
                    and item.requester_name.lower() == requester_name.lower()
                    and item.attribute_key == attribute_key
                    and item.attribute_value.lower() == attribute_value.lower()
                ):
                    return True
        return False

    def dismiss_renewal(
        self, assignment_id: str, expires_at: datetime, actor: str, at: datetime
    ) -> None:
        """Record that a renewal reminder for this assignment expiry was dismissed.

        The marker is keyed by ``(assignment_id, expires_at)``.

        Args:
            assignment_id: Assignment whose reminder is dismissed.
            expires_at: Expiry the reminder was tied to.
            actor: Principal who dismissed the reminder.
            at: Time of dismissal.
        """
        with self._lock:
            self._dismissals[(assignment_id, expires_at)] = (actor, at)

    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        """Return whether the renewal reminder for this expiry was dismissed.

        Args:
            assignment_id: Assignment identifier.
            expires_at: Expiry the reminder is tied to.

        Returns:
            True if ``dismiss_renewal`` was called for this pair.
        """
        with self._lock:
            return (assignment_id, expires_at) in self._dismissals


class InMemoryPolicyExceptionRepository(PolicyExceptionRepository):
    """Thread-safe store of principal-level policy exceptions."""

    def __init__(self) -> None:
        self._items: dict[str, PolicyException] = {}
        self._lock = threading.Lock()

    def list_exceptions(self) -> list[PolicyException]:
        """Return all exceptions sorted by principal type then name."""
        with self._lock:
            return sorted(
                self._items.values(),
                key=lambda item: (item.principal_type.value, item.principal_name.lower()),
            )

    def get_exception(self, exception_id: str) -> PolicyException | None:
        """Return the exception with ``exception_id``, or None if missing.

        Args:
            exception_id: Exception identifier.

        Returns:
            The stored exception, or None.
        """
        with self._lock:
            return self._items.get(exception_id)

    def upsert_exception(self, exception: PolicyException) -> PolicyException:
        """Insert ``exception`` or update the existing row for the same principal.

        A match is the same principal type and case-insensitive principal name.
        On match, ``note`` is replaced and ``created_by`` is updated when the
        incoming value is truthy; the original ``exception_id`` is kept.

        Args:
            exception: Exception to insert or merge.

        Returns:
            The stored (possibly updated) exception.
        """
        with self._lock:
            for existing in self._items.values():
                if (
                    existing.principal_name.lower() == exception.principal_name.lower()
                    and existing.principal_type == exception.principal_type
                ):
                    updated = existing.model_copy(
                        update={
                            "note": exception.note,
                            "created_by": exception.created_by or existing.created_by,
                        }
                    )
                    self._items[existing.exception_id] = updated
                    return updated
            self._items[exception.exception_id] = exception
            return exception

    def delete_exception(self, exception_id: str) -> bool:
        """Remove the exception with ``exception_id`` if it exists.

        Args:
            exception_id: Exception identifier.

        Returns:
            True if a row was removed, False if ``exception_id`` was unknown.
        """
        with self._lock:
            return self._items.pop(exception_id, None) is not None


class InMemoryMaskRuleRepository(MaskRuleRepository):
    """Thread-safe store of mask rule definitions and string config values."""

    def __init__(self) -> None:
        self._rules: dict[str, MaskRuleDefinition] = {}
        self._config: dict[str, str] = {}
        self._lock = threading.Lock()

    def list_rules(self) -> list[MaskRuleDefinition]:
        """Return all mask rules sorted by ``rule_key``."""
        with self._lock:
            return sorted(self._rules.values(), key=lambda r: r.rule_key)

    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        """Return the rule for ``rule_key``, trying a lowercased key first.

        Args:
            rule_key: Rule key as stored or in a different case.

        Returns:
            The matching rule, or None.
        """
        with self._lock:
            return self._rules.get(rule_key.lower()) or self._rules.get(rule_key)

    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        """Insert or replace the rule keyed by ``rule.rule_key``.

        Args:
            rule: Rule to store.

        Returns:
            The same ``rule`` instance that was stored.
        """
        with self._lock:
            self._rules[rule.rule_key] = rule
            return rule

    def delete_rule(self, rule_key: str) -> bool:
        """Remove the rule for ``rule_key``, trying a lowercased key first.

        Args:
            rule_key: Rule key as stored or in a different case.

        Returns:
            True if a rule was removed, False if neither key variant was present.
        """
        with self._lock:
            key = rule_key.lower()
            if key in self._rules:
                self._rules.pop(key)
                return True
            if rule_key in self._rules:
                self._rules.pop(rule_key)
                return True
            return False

    def get_config(self, key: str) -> str | None:
        """Return the mask-rule config value for ``key``, or None if unset.

        Args:
            key: Config key.

        Returns:
            The stored string, or None.
        """
        with self._lock:
            return self._config.get(key)

    def put_config(self, key: str, value: str, actor: str) -> None:
        """Set a mask-rule config string.

        Args:
            key: Config key.
            value: Value to store.
            actor: Principal performing the write; accepted for the interface, not stored.
        """
        with self._lock:
            self._config[key] = value


class InMemoryStorageProvider(StorageProvider):
    """Non-persistent storage for local development and tests.

    Attributes:
        kind: Always ``memory``.
    """

    kind = "memory"

    def __init__(self) -> None:
        self._settings = InMemorySettingsRepository()
        self._audit = InMemoryAuditRepository()
        self._attributes = InMemoryIdentityAttributeRepository()
        self._category_approvers = InMemoryCategoryApproverRepository()
        self._access_requests = InMemoryAccessRequestRepository()
        self._mask_rules = InMemoryMaskRuleRepository()
        self._policy_exceptions = InMemoryPolicyExceptionRepository()

    @property
    def settings(self) -> SettingsRepository:
        """Return the in-memory settings repository."""
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        """Return the in-memory audit repository."""
        return self._audit

    @property
    def identity_attributes(self) -> IdentityAttributeRepository:
        """Return the in-memory identity-attribute repository."""
        return self._attributes

    @property
    def category_approvers(self) -> CategoryApproverRepository:
        """Return the in-memory category-approver repository."""
        return self._category_approvers

    @property
    def access_requests(self) -> AccessRequestRepository:
        """Return the in-memory access-request repository."""
        return self._access_requests

    @property
    def mask_rules(self) -> MaskRuleRepository:
        """Return the in-memory mask-rule repository."""
        return self._mask_rules

    @property
    def policy_exceptions(self) -> PolicyExceptionRepository:
        """Return the in-memory policy-exception repository."""
        return self._policy_exceptions

    def health(self) -> StorageHealth:
        """Return a healthy status noting that data is not persisted."""
        return StorageHealth(
            backend=self.kind, healthy=True, detail="In-memory storage; data is not persisted"
        )

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
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        with self._lock:
            self._values.update(values)


class InMemoryAuditRepository(AuditRepository):
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        with self._lock:
            return sorted(self._events, key=lambda e: e.created_at, reverse=True)[:limit]


class InMemoryIdentityAttributeRepository(IdentityAttributeRepository):
    def __init__(self) -> None:
        self._definitions: dict[str, AttributeDefinition] = {}
        self._assignments: dict[str, IdentityAttributeAssignment] = {}
        self._lock = threading.Lock()

    def list_definitions(self) -> list[AttributeDefinition]:
        with self._lock:
            return sorted(self._definitions.values(), key=lambda d: d.key)

    def get_definition(self, key: str) -> AttributeDefinition | None:
        with self._lock:
            return self._definitions.get(key)

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        with self._lock:
            self._definitions[definition.key] = definition
            return definition

    def delete_definition(self, key: str) -> bool:
        with self._lock:
            return self._definitions.pop(key, None) is not None

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        with self._lock:
            items = list(self._assignments.values())
        if criteria.principal_name:
            needle = criteria.principal_name.lower()
            items = [a for a in items if a.principal_name.lower() == needle]
        if criteria.principal_type:
            items = [a for a in items if a.principal_type == criteria.principal_type]
        if criteria.attribute_key:
            items = [a for a in items if a.attribute_key == criteria.attribute_key]
        if not criteria.include_inactive:
            items = [a for a in items if a.active]
        return sorted(items, key=lambda a: a.created_at, reverse=True)

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        with self._lock:
            return self._assignments.get(assignment_id)

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        with self._lock:
            self._assignments[assignment.assignment_id] = assignment
        return assignment

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
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
        with self._lock:
            current = self._assignments.get(assignment_id)
            if current is None:
                return None
            updated = current.model_copy(update={"expires_at": expires_at, "granted_by": actor})
            self._assignments[assignment_id] = updated
            return updated


class InMemoryCategoryApproverRepository(CategoryApproverRepository):
    def __init__(self) -> None:
        self._items: dict[str, CategoryApprover] = {}
        self._lock = threading.Lock()

    def list_approvers(self, category_path: str | None = None) -> list[CategoryApprover]:
        with self._lock:
            items = list(self._items.values())
        if category_path:
            needle = category_path.lower()
            items = [a for a in items if a.category_path.lower() == needle]
        return sorted(
            items,
            key=lambda a: (a.category_path.lower(), a.principal_type.value, a.principal_name.lower()),
        )

    def list_configured_categories(self) -> list[str]:
        with self._lock:
            paths = {a.category_path for a in self._items.values()}
        # Preserve first-seen casing by sorting case-insensitively on unique lower keys.
        by_lower: dict[str, str] = {}
        for path in paths:
            by_lower.setdefault(path.lower(), path)
        return [by_lower[k] for k in sorted(by_lower)]

    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        with self._lock:
            return self._items.get(approver_id)

    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        with self._lock:
            for existing in self._items.values():
                if (
                    existing.category_path.lower() == approver.category_path.lower()
                    and existing.principal_name.lower() == approver.principal_name.lower()
                    and existing.principal_type == approver.principal_type
                ):
                    return existing
            self._items[approver.approver_id] = approver
            return approver

    def delete_approver(self, approver_id: str) -> bool:
        with self._lock:
            return self._items.pop(approver_id, None) is not None


class InMemoryAccessRequestRepository(AccessRequestRepository):
    def __init__(self) -> None:
        self._items: dict[str, AccessRequest] = {}
        self._dismissals: dict[tuple[str, datetime], tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def create(self, request: AccessRequest) -> AccessRequest:
        with self._lock:
            self._items[request.request_id] = request
        return request

    def get(self, request_id: str) -> AccessRequest | None:
        with self._lock:
            return self._items.get(request_id)

    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        needle = requester_name.lower()
        with self._lock:
            items = [r for r in self._items.values() if r.requester_name.lower() == needle]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        with self._lock:
            items = [r for r in self._items.values() if r.status == status]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def save(self, request: AccessRequest) -> AccessRequest:
        with self._lock:
            self._items[request.request_id] = request
        return request

    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool:
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
        with self._lock:
            self._dismissals[(assignment_id, expires_at)] = (actor, at)

    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        with self._lock:
            return (assignment_id, expires_at) in self._dismissals


class InMemoryPolicyExceptionRepository(PolicyExceptionRepository):
    def __init__(self) -> None:
        self._items: dict[str, PolicyException] = {}
        self._lock = threading.Lock()

    def list_exceptions(self) -> list[PolicyException]:
        with self._lock:
            return sorted(
                self._items.values(),
                key=lambda item: (item.principal_type.value, item.principal_name.lower()),
            )

    def get_exception(self, exception_id: str) -> PolicyException | None:
        with self._lock:
            return self._items.get(exception_id)

    def upsert_exception(self, exception: PolicyException) -> PolicyException:
        with self._lock:
            for existing in self._items.values():
                if (
                    existing.principal_name.lower() == exception.principal_name.lower()
                    and existing.principal_type == exception.principal_type
                ):
                    updated = existing.model_copy(
                        update={"note": exception.note, "created_by": exception.created_by or existing.created_by}
                    )
                    self._items[existing.exception_id] = updated
                    return updated
            self._items[exception.exception_id] = exception
            return exception

    def delete_exception(self, exception_id: str) -> bool:
        with self._lock:
            return self._items.pop(exception_id, None) is not None


class InMemoryMaskRuleRepository(MaskRuleRepository):
    def __init__(self) -> None:
        self._rules: dict[str, MaskRuleDefinition] = {}
        self._config: dict[str, str] = {}
        self._lock = threading.Lock()

    def list_rules(self) -> list[MaskRuleDefinition]:
        with self._lock:
            return sorted(self._rules.values(), key=lambda r: r.rule_key)

    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        with self._lock:
            return self._rules.get(rule_key.lower()) or self._rules.get(rule_key)

    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        with self._lock:
            self._rules[rule.rule_key] = rule
            return rule

    def delete_rule(self, rule_key: str) -> bool:
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
        with self._lock:
            return self._config.get(key)

    def put_config(self, key: str, value: str, actor: str) -> None:
        with self._lock:
            self._config[key] = value


class InMemoryStorageProvider(StorageProvider):
    """Non-persistent storage for local development and tests."""

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
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    @property
    def identity_attributes(self) -> IdentityAttributeRepository:
        return self._attributes

    @property
    def category_approvers(self) -> CategoryApproverRepository:
        return self._category_approvers

    @property
    def access_requests(self) -> AccessRequestRepository:
        return self._access_requests

    @property
    def mask_rules(self) -> MaskRuleRepository:
        return self._mask_rules

    @property
    def policy_exceptions(self) -> PolicyExceptionRepository:
        return self._policy_exceptions

    def health(self) -> StorageHealth:
        return StorageHealth(
            backend=self.kind, healthy=True, detail="In-memory storage; data is not persisted"
        )

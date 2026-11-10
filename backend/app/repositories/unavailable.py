from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from backend.app.core.errors import NotConfiguredError
from backend.app.models.access_request import AccessRequest, AccessRequestStatus, CategoryApprover
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    IdentityAttributeAssignment,
)
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.repositories.base import (
    AccessRequestRepository,
    AuditRepository,
    CategoryApproverRepository,
    IdentityAttributeRepository,
    MaskRuleRepository,
    SettingsRepository,
    StorageProvider,
)


class _Unavailable:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _fail(self) -> NoReturn:
        raise NotConfiguredError(f"Storage is unavailable: {self._reason}")


class _UnavailableSettings(_Unavailable, SettingsRepository):
    def get_all(self) -> dict[str, Any]:
        self._fail()

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        self._fail()


class _UnavailableAudit(_Unavailable, AuditRepository):
    def record(self, event: AuditEvent) -> None:
        self._fail()

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        self._fail()


class _UnavailableIdentityAttributes(_Unavailable, IdentityAttributeRepository):
    def list_definitions(self) -> list[AttributeDefinition]:
        self._fail()

    def get_definition(self, key: str) -> AttributeDefinition | None:
        self._fail()

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        self._fail()

    def delete_definition(self, key: str) -> bool:
        self._fail()

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        self._fail()

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        self._fail()

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        self._fail()

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        self._fail()

    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        self._fail()


class _UnavailableCategoryApprovers(_Unavailable, CategoryApproverRepository):
    def list_approvers(self, category_path: str | None = None) -> list[CategoryApprover]:
        self._fail()

    def list_configured_categories(self) -> list[str]:
        self._fail()

    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        self._fail()

    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        self._fail()

    def delete_approver(self, approver_id: str) -> bool:
        self._fail()


class _UnavailableAccessRequests(_Unavailable, AccessRequestRepository):
    def create(self, request: AccessRequest) -> AccessRequest:
        self._fail()

    def get(self, request_id: str) -> AccessRequest | None:
        self._fail()

    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        self._fail()

    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        self._fail()

    def save(self, request: AccessRequest) -> AccessRequest:
        self._fail()

    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool:
        self._fail()

    def dismiss_renewal(
        self, assignment_id: str, expires_at: datetime, actor: str, at: datetime
    ) -> None:
        self._fail()

    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        self._fail()


class _UnavailableMaskRules(_Unavailable, MaskRuleRepository):
    def list_rules(self) -> list[MaskRuleDefinition]:
        self._fail()

    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        self._fail()

    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        self._fail()

    def delete_rule(self, rule_key: str) -> bool:
        self._fail()

    def get_config(self, key: str) -> str | None:
        self._fail()

    def put_config(self, key: str, value: str, actor: str) -> None:
        self._fail()


class UnavailableStorageProvider(StorageProvider):
    """Stands in for a backend that could not be built, so the app starts and reports why."""

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self._reason = reason
        self._settings = _UnavailableSettings(reason)
        self._audit = _UnavailableAudit(reason)
        self._attributes = _UnavailableIdentityAttributes(reason)
        self._category_approvers = _UnavailableCategoryApprovers(reason)
        self._access_requests = _UnavailableAccessRequests(reason)
        self._mask_rules = _UnavailableMaskRules(reason)

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

    def health(self) -> StorageHealth:
        return StorageHealth(backend=self.kind, healthy=False, detail=self._reason)

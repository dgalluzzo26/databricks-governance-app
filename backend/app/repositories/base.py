from __future__ import annotations

from abc import ABC, abstractmethod
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


class SettingsRepository(ABC):
    @abstractmethod
    def get_all(self) -> dict[str, Any]: ...

    @abstractmethod
    def put_many(self, values: dict[str, Any], actor: str) -> None: ...


class AuditRepository(ABC):
    @abstractmethod
    def record(self, event: AuditEvent) -> None: ...

    @abstractmethod
    def list_recent(self, limit: int = 100) -> list[AuditEvent]: ...


class IdentityAttributeRepository(ABC):
    @abstractmethod
    def list_definitions(self) -> list[AttributeDefinition]: ...

    @abstractmethod
    def get_definition(self, key: str) -> AttributeDefinition | None: ...

    @abstractmethod
    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition: ...

    @abstractmethod
    def delete_definition(self, key: str) -> bool: ...

    @abstractmethod
    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]: ...

    @abstractmethod
    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None: ...

    @abstractmethod
    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment: ...

    @abstractmethod
    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None: ...

    @abstractmethod
    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None: ...


class CategoryApproverRepository(ABC):
    @abstractmethod
    def list_approvers(self, category_path: str | None = None) -> list[CategoryApprover]: ...

    @abstractmethod
    def list_configured_categories(self) -> list[str]: ...

    @abstractmethod
    def get_approver(self, approver_id: str) -> CategoryApprover | None: ...

    @abstractmethod
    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover: ...

    @abstractmethod
    def delete_approver(self, approver_id: str) -> bool: ...


class AccessRequestRepository(ABC):
    @abstractmethod
    def create(self, request: AccessRequest) -> AccessRequest: ...

    @abstractmethod
    def get(self, request_id: str) -> AccessRequest | None: ...

    @abstractmethod
    def list_for_requester(self, requester_name: str) -> list[AccessRequest]: ...

    @abstractmethod
    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]: ...

    @abstractmethod
    def save(self, request: AccessRequest) -> AccessRequest: ...

    @abstractmethod
    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool: ...

    @abstractmethod
    def dismiss_renewal(
        self, assignment_id: str, expires_at: datetime, actor: str, at: datetime
    ) -> None: ...

    @abstractmethod
    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool: ...


class StorageProvider(ABC):
    """Strategy for where app state lives. One provider supplies every repository."""

    kind: str

    @property
    @abstractmethod
    def settings(self) -> SettingsRepository: ...

    @property
    @abstractmethod
    def audit(self) -> AuditRepository: ...

    @property
    @abstractmethod
    def identity_attributes(self) -> IdentityAttributeRepository: ...

    @property
    @abstractmethod
    def category_approvers(self) -> CategoryApproverRepository: ...

    @property
    @abstractmethod
    def access_requests(self) -> AccessRequestRepository: ...

    def initialize(self) -> None:
        """Prepare storage (create schema, open pools). Must be idempotent."""

    @abstractmethod
    def health(self) -> StorageHealth: ...

    def close(self) -> None:
        """Release connections."""

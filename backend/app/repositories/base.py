"""Abstract repository interfaces for application state.

These contracts define how settings, audit events, identity attributes, access-request
workflow data, mask rules, and policy exceptions are persisted. Concrete backends
(Delta, Lakebase, in-memory) implement the same surface so services stay storage-agnostic.
"""

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
from backend.app.models.mask_rule import MaskRuleDefinition
from backend.app.models.policy_exception import PolicyException


class SettingsRepository(ABC):
    """Persistent key-value store for application settings."""

    @abstractmethod
    def get_all(self) -> dict[str, Any]:
        """Load every stored setting.

        Returns:
            Mapping of setting keys to deserialized values. Empty when nothing is stored.
        """

    @abstractmethod
    def put_many(self, values: dict[str, Any], actor: str) -> None:
        """Insert or replace settings for the given keys.

        Keys present in ``values`` are upserted; other existing keys are left unchanged.

        Args:
            values: Setting keys mapped to JSON-serializable values.
            actor: Identity recorded as having performed the update.
        """


class AuditRepository(ABC):
    """Append-only store for application audit events."""

    @abstractmethod
    def record(self, event: AuditEvent) -> None:
        """Persist a single audit event.

        Args:
            event: Event to append; ``event_id`` must be unique for the backend.
        """

    @abstractmethod
    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Return the newest audit events.

        Args:
            limit: Maximum number of events to return. Defaults to 100.

        Returns:
            Events ordered newest-first. Empty when no events exist.
        """


class IdentityAttributeRepository(ABC):
    """Store for identity attribute definitions and principal assignments."""

    @abstractmethod
    def list_definitions(self) -> list[AttributeDefinition]:
        """List every attribute definition.

        Returns:
            Definitions ordered by attribute key. Empty when none are configured.
        """

    @abstractmethod
    def get_definition(self, key: str) -> AttributeDefinition | None:
        """Fetch one attribute definition by key.

        Args:
            key: Attribute definition key.

        Returns:
            The matching definition, or None when no definition exists for ``key``.
        """

    @abstractmethod
    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        """Insert or replace an attribute definition keyed by ``definition.key``.

        Args:
            definition: Definition to persist.

        Returns:
            The definition as stored after the upsert.
        """

    @abstractmethod
    def delete_definition(self, key: str) -> bool:
        """Remove an attribute definition.

        Args:
            key: Attribute definition key to delete.

        Returns:
            True when a definition was deleted; False when none existed for ``key``.
        """

    @abstractmethod
    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        """List assignments matching the filter.

        When ``criteria.include_inactive`` is False, revoked and expired assignments are
        omitted. Unset filter fields do not constrain the result.

        Args:
            criteria: Optional principal, type, attribute-key, and active-only filters.

        Returns:
            Matching assignments, typically newest-first. Empty when none match.
        """

    @abstractmethod
    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        """Fetch one assignment by id.

        Args:
            assignment_id: Stable assignment identifier.

        Returns:
            The matching assignment, or None when no row exists for ``assignment_id``.
        """

    @abstractmethod
    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        """Persist a new (or replacement) assignment keyed by ``assignment.assignment_id``.

        Args:
            assignment: Assignment to store.

        Returns:
            The assignment as stored.
        """

    @abstractmethod
    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        """Soft-revoke an assignment by setting ``revoked_at`` and ``revoked_by``.

        Args:
            assignment_id: Assignment to revoke.
            actor: Identity recorded as the revoker.
            at: Revocation timestamp.

        Returns:
            The updated assignment, or None when ``assignment_id`` does not exist.
        """

    @abstractmethod
    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        """Change an assignment's expiry and record who updated it.

        Args:
            assignment_id: Assignment to update.
            expires_at: New expiry timestamp.
            actor: Identity recorded as having granted or extended the assignment.

        Returns:
            The updated assignment, or None when ``assignment_id`` does not exist.
        """


class CategoryApproverRepository(ABC):
    """Store mapping sensitivity category paths to their designated approvers."""

    @abstractmethod
    def list_approvers(self, category_path: str | None = None) -> list[CategoryApprover]:
        """List category approvers, optionally for one path.

        Args:
            category_path: When set, only approvers for this sensitivity path; when None,
                return every configured approver.

        Returns:
            Matching approver rows. Empty when none are configured (or none match the path).
        """

    @abstractmethod
    def list_configured_categories(self) -> list[str]:
        """List distinct sensitivity category paths that have at least one approver.

        Returns:
            Category paths. Empty when no approvers are configured.
        """

    @abstractmethod
    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        """Fetch one category approver by id.

        Args:
            approver_id: Approver row identifier.

        Returns:
            The matching approver, or None when no row exists for ``approver_id``.
        """

    @abstractmethod
    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        """Insert or replace a category approver keyed by ``approver.approver_id``.

        Args:
            approver: Approver row to persist.

        Returns:
            The approver as stored after the upsert.
        """

    @abstractmethod
    def delete_approver(self, approver_id: str) -> bool:
        """Remove a category approver.

        Args:
            approver_id: Approver row identifier to delete.

        Returns:
            True when a row was deleted; False when none existed for ``approver_id``.
        """


class AccessRequestRepository(ABC):
    """Store for sensitivity access requests and renewal-dismissal markers."""

    @abstractmethod
    def create(self, request: AccessRequest) -> AccessRequest:
        """Insert a new access request.

        Args:
            request: Request to persist; ``request_id`` must be unique.

        Returns:
            The request as stored.
        """

    @abstractmethod
    def get(self, request_id: str) -> AccessRequest | None:
        """Fetch one access request by id.

        Args:
            request_id: Request identifier.

        Returns:
            The matching request, or None when no row exists for ``request_id``.
        """

    @abstractmethod
    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        """List access requests submitted by a principal.

        Matching is case-insensitive on the requester name.

        Args:
            requester_name: Principal that opened the requests.

        Returns:
            That requester's requests, typically newest-first. Empty when none exist.
        """

    @abstractmethod
    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        """List access requests with a given status.

        Args:
            status: Status to filter on (for example pending or approved).

        Returns:
            Matching requests, typically newest-first. Empty when none match.
        """

    @abstractmethod
    def save(self, request: AccessRequest) -> AccessRequest:
        """Replace an existing access request by ``request.request_id``.

        Args:
            request: Full request state to persist.

        Returns:
            The request as stored.
        """

    @abstractmethod
    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool:
        """Report whether a pending request already exists for the same grant.

        Matching is case-insensitive on requester name and attribute value.

        Args:
            requester_name: Principal that would request the grant.
            attribute_key: Attribute definition key.
            attribute_value: Requested attribute value.

        Returns:
            True when a pending request already covers that combination; False otherwise.
        """

    @abstractmethod
    def dismiss_renewal(
        self, assignment_id: str, expires_at: datetime, actor: str, at: datetime
    ) -> None:
        """Record that a renewal prompt was dismissed for an assignment expiry.

        The dismissal is keyed by ``(assignment_id, expires_at)`` so a later re-grant
        with a new expiry can surface a fresh renewal notice.

        Args:
            assignment_id: Assignment whose renewal inbox entry was dismissed.
            expires_at: Expiry that identified the renewal notice.
            actor: Identity that dismissed the notice.
            at: Dismissal timestamp.
        """

    @abstractmethod
    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        """Check whether a renewal notice was dismissed for this assignment expiry.

        Args:
            assignment_id: Assignment id of the renewal notice.
            expires_at: Expiry that identified the renewal notice.

        Returns:
            True when ``dismiss_renewal`` was previously called for this pair; False otherwise.
        """


class MaskRuleRepository(ABC):
    """Store for column-mask rule definitions and related string configuration."""

    @abstractmethod
    def list_rules(self) -> list[MaskRuleDefinition]:
        """List every mask rule definition.

        Returns:
            All stored rules. Empty when none are configured.
        """

    @abstractmethod
    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        """Fetch one mask rule by key.

        Args:
            rule_key: Rule identifier.

        Returns:
            The matching rule, or None when no rule exists for ``rule_key``.
        """

    @abstractmethod
    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        """Insert or replace a mask rule keyed by ``rule.rule_key``.

        Args:
            rule: Rule definition to persist.

        Returns:
            The rule as stored after the upsert.
        """

    @abstractmethod
    def delete_rule(self, rule_key: str) -> bool:
        """Remove a mask rule.

        Args:
            rule_key: Rule identifier to delete.

        Returns:
            True when a rule was deleted; False when none existed for ``rule_key``.
        """

    @abstractmethod
    def get_config(self, key: str) -> str | None:
        """Read a mask-rule related configuration string.

        Args:
            key: Configuration key (for example salt material).

        Returns:
            Stored string value, or None when the key is unset.
        """

    @abstractmethod
    def put_config(self, key: str, value: str, actor: str) -> None:
        """Insert or replace a mask-rule related configuration string.

        Args:
            key: Configuration key.
            value: String value to store.
            actor: Identity recorded as having performed the update.
        """


class PolicyExceptionRepository(ABC):
    """Store for principals exempted from ABAC masks and row filters."""

    @abstractmethod
    def list_exceptions(self) -> list[PolicyException]:
        """List every policy exception.

        Returns:
            All stored exceptions. Empty when none are configured.
        """

    @abstractmethod
    def get_exception(self, exception_id: str) -> PolicyException | None:
        """Fetch one policy exception by id.

        Args:
            exception_id: Exception row identifier.

        Returns:
            The matching exception, or None when no row exists for ``exception_id``.
        """

    @abstractmethod
    def upsert_exception(self, exception: PolicyException) -> PolicyException:
        """Insert or replace a policy exception keyed by ``exception.exception_id``.

        Args:
            exception: Exception row to persist.

        Returns:
            The exception as stored after the upsert.
        """

    @abstractmethod
    def delete_exception(self, exception_id: str) -> bool:
        """Remove a policy exception.

        Args:
            exception_id: Exception row identifier to delete.

        Returns:
            True when a row was deleted; False when none existed for ``exception_id``.
        """


class StorageProvider(ABC):
    """Strategy for where app state lives. One provider supplies every repository.

    Attributes:
        kind: Short backend label used in health and diagnostics (for example ``delta``).
    """

    kind: str

    @property
    @abstractmethod
    def settings(self) -> SettingsRepository:
        """Settings key-value repository for this backend.

        Returns:
            Repository used for application settings.
        """

    @property
    @abstractmethod
    def audit(self) -> AuditRepository:
        """Audit-event repository for this backend.

        Returns:
            Repository used to record and list audit events.
        """

    @property
    @abstractmethod
    def identity_attributes(self) -> IdentityAttributeRepository:
        """Identity-attribute repository for this backend.

        Returns:
            Repository used for attribute definitions and assignments.
        """

    @property
    @abstractmethod
    def category_approvers(self) -> CategoryApproverRepository:
        """Category-approver repository for this backend.

        Returns:
            Repository used for sensitivity-category approvers.
        """

    @property
    @abstractmethod
    def access_requests(self) -> AccessRequestRepository:
        """Access-request repository for this backend.

        Returns:
            Repository used for access requests and renewal dismissals.
        """

    @property
    @abstractmethod
    def mask_rules(self) -> MaskRuleRepository:
        """Mask-rule repository for this backend.

        Returns:
            Repository used for mask rule definitions and related config.
        """

    @property
    @abstractmethod
    def policy_exceptions(self) -> PolicyExceptionRepository:
        """Policy-exception repository for this backend.

        Returns:
            Repository used for global ABAC policy exceptions.
        """

    def initialize(self) -> None:
        """Prepare storage (create schema, open pools). Must be idempotent."""

    @abstractmethod
    def health(self) -> StorageHealth:
        """Probe whether this backend is reachable and usable.

        Returns:
            Health snapshot including backend kind, healthy flag, and detail text.
        """

    def close(self) -> None:
        """Release connections."""

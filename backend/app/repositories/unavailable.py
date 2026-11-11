"""Storage provider that fails every repository call when a backend cannot start.

Used when the configured persistent store cannot be constructed. The app still
boots; repository methods raise ``NotConfiguredError`` and ``health`` reports
the original failure reason.
"""

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


class _Unavailable:
    """Mixin that raises ``NotConfiguredError`` for every storage operation.

    Args:
        reason: Human-readable explanation of why storage is unavailable.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _fail(self) -> NoReturn:
        """Raise ``NotConfiguredError`` including the stored reason.

        Raises:
            NotConfiguredError: Always raised; storage is not configured.
        """
        raise NotConfiguredError(f"Storage is unavailable: {self._reason}")


class _UnavailableSettings(_Unavailable, SettingsRepository):
    """Settings repository that always raises ``NotConfiguredError``."""

    def get_all(self) -> dict[str, Any]:
        """Raise because no settings store is configured.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        """Raise because no settings store is configured.

        Args:
            values: Settings mapping that would have been stored.
            actor: Principal that would have been recorded as the writer.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailableAudit(_Unavailable, AuditRepository):
    """Audit repository that always raises ``NotConfiguredError``."""

    def record(self, event: AuditEvent) -> None:
        """Raise because no audit store is configured.

        Args:
            event: Audit record that would have been appended.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Raise because no audit store is configured.

        Args:
            limit: Maximum number of events that would have been returned.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailableIdentityAttributes(_Unavailable, IdentityAttributeRepository):
    """Identity-attribute repository that always raises ``NotConfiguredError``."""

    def list_definitions(self) -> list[AttributeDefinition]:
        """Raise because no identity-attribute store is configured.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_definition(self, key: str) -> AttributeDefinition | None:
        """Raise because no identity-attribute store is configured.

        Args:
            key: Attribute definition key that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        """Raise because no identity-attribute store is configured.

        Args:
            definition: Definition that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def delete_definition(self, key: str) -> bool:
        """Raise because no identity-attribute store is configured.

        Args:
            key: Attribute definition key that would have been removed.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        """Raise because no identity-attribute store is configured.

        Args:
            criteria: Filter that would have been applied.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        """Raise because no identity-attribute store is configured.

        Args:
            assignment_id: Assignment identifier that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        """Raise because no identity-attribute store is configured.

        Args:
            assignment: Assignment that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        """Raise because no identity-attribute store is configured.

        Args:
            assignment_id: Assignment that would have been revoked.
            actor: Principal that would have been recorded as ``revoked_by``.
            at: Timestamp that would have been recorded as ``revoked_at``.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        """Raise because no identity-attribute store is configured.

        Args:
            assignment_id: Assignment that would have been updated.
            expires_at: Expiry that would have been stored.
            actor: Principal that would have been recorded as the granter.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailableCategoryApprovers(_Unavailable, CategoryApproverRepository):
    """Category-approver repository that always raises ``NotConfiguredError``."""

    def list_approvers(self, category_path: str | None = None) -> list[CategoryApprover]:
        """Raise because no category-approver store is configured.

        Args:
            category_path: Optional category filter that would have been applied.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def list_configured_categories(self) -> list[str]:
        """Raise because no category-approver store is configured.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        """Raise because no category-approver store is configured.

        Args:
            approver_id: Approver identifier that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        """Raise because no category-approver store is configured.

        Args:
            approver: Approver that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def delete_approver(self, approver_id: str) -> bool:
        """Raise because no category-approver store is configured.

        Args:
            approver_id: Approver identifier that would have been removed.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailableAccessRequests(_Unavailable, AccessRequestRepository):
    """Access-request repository that always raises ``NotConfiguredError``."""

    def create(self, request: AccessRequest) -> AccessRequest:
        """Raise because no access-request store is configured.

        Args:
            request: Access request that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get(self, request_id: str) -> AccessRequest | None:
        """Raise because no access-request store is configured.

        Args:
            request_id: Request identifier that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        """Raise because no access-request store is configured.

        Args:
            requester_name: Requester principal that would have been filtered on.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        """Raise because no access-request store is configured.

        Args:
            status: Status that would have been filtered on.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def save(self, request: AccessRequest) -> AccessRequest:
        """Raise because no access-request store is configured.

        Args:
            request: Request that would have been persisted.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def has_pending_for(
        self, *, requester_name: str, attribute_key: str, attribute_value: str
    ) -> bool:
        """Raise because no access-request store is configured.

        Args:
            requester_name: Requester principal that would have been matched.
            attribute_key: Attribute key that would have been matched.
            attribute_value: Attribute value that would have been matched.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def dismiss_renewal(
        self, assignment_id: str, expires_at: datetime, actor: str, at: datetime
    ) -> None:
        """Raise because no access-request store is configured.

        Args:
            assignment_id: Assignment whose reminder would have been dismissed.
            expires_at: Expiry the reminder would have been tied to.
            actor: Principal that would have been recorded as dismissing.
            at: Time that would have been recorded as dismissal.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        """Raise because no access-request store is configured.

        Args:
            assignment_id: Assignment identifier that would have been looked up.
            expires_at: Expiry the reminder would have been tied to.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailablePolicyExceptions(_Unavailable, PolicyExceptionRepository):
    """Policy-exception repository that always raises ``NotConfiguredError``."""

    def list_exceptions(self) -> list[PolicyException]:
        """Raise because no policy-exception store is configured.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_exception(self, exception_id: str) -> PolicyException | None:
        """Raise because no policy-exception store is configured.

        Args:
            exception_id: Exception identifier that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def upsert_exception(self, exception: PolicyException) -> PolicyException:
        """Raise because no policy-exception store is configured.

        Args:
            exception: Exception that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def delete_exception(self, exception_id: str) -> bool:
        """Raise because no policy-exception store is configured.

        Args:
            exception_id: Exception identifier that would have been removed.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class _UnavailableMaskRules(_Unavailable, MaskRuleRepository):
    """Mask-rule repository that always raises ``NotConfiguredError``."""

    def list_rules(self) -> list[MaskRuleDefinition]:
        """Raise because no mask-rule store is configured.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        """Raise because no mask-rule store is configured.

        Args:
            rule_key: Rule key that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        """Raise because no mask-rule store is configured.

        Args:
            rule: Rule that would have been stored.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def delete_rule(self, rule_key: str) -> bool:
        """Raise because no mask-rule store is configured.

        Args:
            rule_key: Rule key that would have been removed.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def get_config(self, key: str) -> str | None:
        """Raise because no mask-rule store is configured.

        Args:
            key: Config key that would have been looked up.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()

    def put_config(self, key: str, value: str, actor: str) -> None:
        """Raise because no mask-rule store is configured.

        Args:
            key: Config key that would have been written.
            value: Value that would have been stored.
            actor: Principal that would have been recorded as the writer.

        Raises:
            NotConfiguredError: Storage is unavailable.
        """
        self._fail()


class UnavailableStorageProvider(StorageProvider):
    """Stands in for a backend that could not be built, so the app starts and reports why.

    Repository accessors return stubs that raise ``NotConfiguredError`` on use.
    ``health`` reports ``healthy=False`` with the original reason.

    Args:
        kind: Backend identifier that failed to initialize.
        reason: Human-readable explanation shown in health checks and errors.
    """

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self._reason = reason
        self._settings = _UnavailableSettings(reason)
        self._audit = _UnavailableAudit(reason)
        self._attributes = _UnavailableIdentityAttributes(reason)
        self._category_approvers = _UnavailableCategoryApprovers(reason)
        self._access_requests = _UnavailableAccessRequests(reason)
        self._mask_rules = _UnavailableMaskRules(reason)
        self._policy_exceptions = _UnavailablePolicyExceptions(reason)

    @property
    def settings(self) -> SettingsRepository:
        """Return a settings repository that raises ``NotConfiguredError`` on use."""
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        """Return an audit repository that raises ``NotConfiguredError`` on use."""
        return self._audit

    @property
    def identity_attributes(self) -> IdentityAttributeRepository:
        """Return an identity-attribute repository that raises ``NotConfiguredError`` on use."""
        return self._attributes

    @property
    def category_approvers(self) -> CategoryApproverRepository:
        """Return a category-approver repository that raises ``NotConfiguredError`` on use."""
        return self._category_approvers

    @property
    def access_requests(self) -> AccessRequestRepository:
        """Return an access-request repository that raises ``NotConfiguredError`` on use."""
        return self._access_requests

    @property
    def mask_rules(self) -> MaskRuleRepository:
        """Return a mask-rule repository that raises ``NotConfiguredError`` on use."""
        return self._mask_rules

    @property
    def policy_exceptions(self) -> PolicyExceptionRepository:
        """Return a policy-exception repository that raises ``NotConfiguredError`` on use."""
        return self._policy_exceptions

    def health(self) -> StorageHealth:
        """Return an unhealthy status whose detail is the construction failure reason."""
        return StorageHealth(backend=self.kind, healthy=False, detail=self._reason)

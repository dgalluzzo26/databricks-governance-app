"""Sensitivity access requests and category-level approvers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from backend.app.models.base import ApiModel
from backend.app.models.identity_attribute import IdentityAttributeAssignment
from backend.app.models.principal import PrincipalKind

SENSITIVITY_ATTRIBUTE_KEY = "sensitivity"
# Stored attribute_key on path-only approver rows. Routing ignores this column.
ACCESS_LEAF_APPROVER_KEY = "access"
RENEWAL_WINDOW_DAYS = 30


class AccessRequestStatus(StrEnum):
    """Lifecycle state of an access request.

    Attributes:
        PENDING: Awaiting a decision.
        APPROVED: Approved; the attribute was granted to the requester.
        DENIED: Rejected by an approver or app admin.
        CANCELLED: Withdrawn by the requester (or an app admin) before a decision.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


class ApprovalItemKind(StrEnum):
    """Type of item in an approver's inbox.

    Attributes:
        REQUEST: A pending access request.
        RENEWAL: An active sensitivity assignment expiring within ``RENEWAL_WINDOW_DAYS``.
    """

    REQUEST = "REQUEST"
    RENEWAL = "RENEWAL"


class CategoryApprover(ApiModel):
    """Principal allowed to decide requests for an access path.

    One registration covers data-access, sensitivity, and row-filter requests whose path
    falls under ``category_path`` (longest dotted-prefix match).

    Attributes:
        approver_id: Unique identifier of the approver entry.
        attribute_key: Stored as ``access`` for new rows; ignored by routing.
        category_path: Access-path prefix (domain or domain.subdomain).
        principal_name: User name or group name of the approver.
        principal_type: ``USER`` or ``GROUP``.
        created_by: Admin who registered the approver, or None when unknown.
        created_at: When the approver was registered.
    """

    approver_id: str
    attribute_key: str = ACCESS_LEAF_APPROVER_KEY
    category_path: str
    principal_name: str
    principal_type: PrincipalKind
    created_by: str | None = None
    created_at: datetime


class ApproverPrincipal(ApiModel):
    """One USER or GROUP to register as an access-path approver.

    Attributes:
        principal_name: User name or group name; stripped, 1-256 chars.
        principal_type: ``USER`` or ``GROUP``.
    """

    principal_name: str = Field(min_length=1, max_length=256)
    principal_type: PrincipalKind

    @field_validator("principal_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Strip and reject blank principal names."""
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty")
        return text


class UpsertCategoryApproverRequest(ApiModel):
    """Request body to register one or more approvers for an access path.

    ``principals`` is the preferred field. A single ``principal_name`` / ``principal_type``
    is still accepted and folded into ``principals``.

    Attributes:
        attribute_key: Ignored; stored as ``access``. Kept so older clients still parse.
        category_path: Access-path prefix; stripped, 1-256 chars.
        principal_name: Optional single principal (legacy).
        principal_type: Kind for the legacy single principal.
        principals: Users and/or groups to register for this path.
    """

    attribute_key: str = Field(default=ACCESS_LEAF_APPROVER_KEY, min_length=1, max_length=128)
    category_path: str = Field(min_length=1, max_length=256)
    principal_name: str | None = Field(default=None, max_length=256)
    principal_type: PrincipalKind | None = None
    principals: list[ApproverPrincipal] = Field(default_factory=list)

    @field_validator("category_path", "attribute_key")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Strip whitespace and reject blank values."""
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty")
        return text

    @field_validator("principal_name")
    @classmethod
    def _strip_optional_name(cls, value: str | None) -> str | None:
        """Strip optional legacy principal name."""
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("attribute_key")
    @classmethod
    def _lower_attribute_key(cls, value: str) -> str:
        """Normalize the attribute key to stripped lower case."""
        return value.strip().lower()

    @model_validator(mode="after")
    def _collect_principals(self) -> UpsertCategoryApproverRequest:
        """Fold the legacy single principal into ``principals`` and require at least one."""
        collected = list(self.principals)
        if self.principal_name and self.principal_type is not None:
            collected.append(
                ApproverPrincipal(principal_name=self.principal_name, principal_type=self.principal_type)
            )
        seen: set[tuple[str, str]] = set()
        unique: list[ApproverPrincipal] = []
        for item in collected:
            key = (item.principal_name.lower(), item.principal_type.value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        if not unique:
            raise ValueError("At least one USER or GROUP principal is required")
        self.principals = unique
        return self


class CategoryApproverList(ApiModel):
    """Registered category approvers.

    Attributes:
        approvers: Approver entries.
        total: Number of entries.
    """

    approvers: list[CategoryApprover]
    total: int


class AccessRequestRouteKind(StrEnum):
    """Who reviews an access request.

    Attributes:
        CATEGORY_APPROVERS: Designated approvers (sensitivity category or row-filter
            dimension). Falls back to ``APP_ADMINS`` when none are configured.
        APP_ADMINS: App admins, used when no designated approver applies.
    """

    CATEGORY_APPROVERS = "category_approvers"
    APP_ADMINS = "app_admins"


class AccessRequestApprover(ApiModel):
    """Approver a request is routed to.

    Attributes:
        principal_name: User name or group name.
        principal_type: Kind of principal.
    """

    principal_name: str
    principal_type: PrincipalKind


class AccessRequest(ApiModel):
    """Request by a user to be granted an identity attribute value.

    The routing fields are recomputed from current approvers when returned by the API.

    Attributes:
        request_id: Unique identifier of the request.
        requester_name: User name of the requester.
        attribute_key: Attribute key requested (``sensitivity`` or a row-filter dimension).
        attribute_value: Requested value, canonicalized to the definition's casing.
        requested_expires_at: Expiry the requester asked for.
        justification: Business justification.
        status: Lifecycle state.
        decided_by: User who approved, denied or cancelled it; None while pending.
        decided_at: When it was decided or cancelled; None while pending.
        decision_note: Optional note from the decider.
        approved_expires_at: Expiry actually granted on approval; None otherwise.
        created_at: Submission time.
        resolved_category: Sensitivity category the request routes to; for row-filter
            dimensions this is the dimension key when designated approvers exist, else None.
        routed_to: Who reviews the request.
        approvers: Designated approvers reviewing it; empty when routed to app admins.
    """

    request_id: str
    requester_name: str
    attribute_key: str
    attribute_value: str
    requested_expires_at: datetime
    justification: str
    status: AccessRequestStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    approved_expires_at: datetime | None = None
    created_at: datetime
    resolved_category: str | None = None
    routed_to: AccessRequestRouteKind | None = None
    approvers: list[AccessRequestApprover] = []


class CreateAccessRequestBody(ApiModel):
    """Request body to submit an access request.

    Attributes:
        attribute_key: Attribute key; stripped and lower-cased, defaults to ``sensitivity``.
        attribute_value: Requested value; stripped, 1-512 chars.
        requested_expires_at: Desired expiry; must be in the future.
        justification: Business justification; stripped, 1-2000 chars.
    """

    attribute_key: str = Field(default=SENSITIVITY_ATTRIBUTE_KEY, min_length=1, max_length=128)
    attribute_value: str = Field(min_length=1, max_length=512)
    requested_expires_at: datetime
    justification: str = Field(min_length=1, max_length=2000)

    @field_validator("attribute_key", "attribute_value", "justification")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Strip whitespace and reject blank values.

        Args:
            value: Raw field value.

        Returns:
            The stripped value.

        Raises:
            ValueError: If the value is empty after stripping.
        """
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty")
        return text

    @field_validator("attribute_key")
    @classmethod
    def _lower_key(cls, value: str) -> str:
        """Normalize the attribute key to stripped lower case.

        Args:
            value: Attribute key.

        Returns:
            The lower-cased key.
        """
        return value.strip().lower()


class DecideAccessRequestBody(ApiModel):
    """Request body to approve or deny a request, or approve a renewal.

    Attributes:
        expires_at: Expiry to grant on approval; None keeps the requested (or current)
            expiry. Ignored on denial.
        note: Optional decision note up to 2000 chars.
    """

    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class AccessRequestList(ApiModel):
    """List of access requests.

    Attributes:
        requests: Access requests.
        total: Number of requests.
    """

    requests: list[AccessRequest]
    total: int


class ApprovalInboxItem(ApiModel):
    """Item awaiting action by the current approver.

    Attributes:
        kind: Whether this is a pending request or an expiring assignment to renew.
        request: The pending request when ``kind`` is ``REQUEST``; otherwise None.
        assignment: The expiring assignment when ``kind`` is ``RENEWAL``; otherwise None.
        resolved_category: Sensitivity category that routed the item to this approver, or
            None.
        attribute_value: Requested or currently held value.
        principal_name: Requester or assignment holder.
        expires_at: Requested expiry (requests) or current expiry (renewals).
    """

    kind: ApprovalItemKind
    request: AccessRequest | None = None
    assignment: IdentityAttributeAssignment | None = None
    resolved_category: str | None = None
    attribute_value: str
    principal_name: str
    expires_at: datetime | None = None


class ApprovalInbox(ApiModel):
    """Items the current user can decide.

    Attributes:
        items: Pending requests and renewals.
        total: Number of items.
    """

    items: list[ApprovalInboxItem]
    total: int

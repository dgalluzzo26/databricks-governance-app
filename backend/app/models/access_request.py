"""Sensitivity access requests and category-level approvers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel
from backend.app.models.identity_attribute import IdentityAttributeAssignment
from backend.app.models.principal import PrincipalKind

SENSITIVITY_ATTRIBUTE_KEY = "sensitivity"
RENEWAL_WINDOW_DAYS = 30


class AccessRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


class ApprovalItemKind(StrEnum):
    REQUEST = "REQUEST"
    RENEWAL = "RENEWAL"


class CategoryApprover(ApiModel):
    approver_id: str
    category_path: str
    principal_name: str
    principal_type: PrincipalKind
    created_by: str | None = None
    created_at: datetime


class UpsertCategoryApproverRequest(ApiModel):
    category_path: str = Field(min_length=1, max_length=256)
    principal_name: str = Field(min_length=1, max_length=256)
    principal_type: PrincipalKind

    @field_validator("category_path", "principal_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty")
        return text


class CategoryApproverList(ApiModel):
    approvers: list[CategoryApprover]
    total: int


class AccessRequest(ApiModel):
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


class CreateAccessRequestBody(ApiModel):
    attribute_value: str = Field(min_length=1, max_length=512)
    requested_expires_at: datetime
    justification: str = Field(min_length=1, max_length=2000)

    @field_validator("attribute_value", "justification")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty")
        return text


class DecideAccessRequestBody(ApiModel):
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class AccessRequestList(ApiModel):
    requests: list[AccessRequest]
    total: int


class ApprovalInboxItem(ApiModel):
    kind: ApprovalItemKind
    request: AccessRequest | None = None
    assignment: IdentityAttributeAssignment | None = None
    resolved_category: str | None = None
    attribute_value: str
    principal_name: str
    expires_at: datetime | None = None


class ApprovalInbox(ApiModel):
    items: list[ApprovalInboxItem]
    total: int

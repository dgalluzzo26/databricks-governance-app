"""Global ABAC policy exceptions (principals exempt from masks and row filters)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class PolicyException(ApiModel):
    exception_id: str
    principal_name: str
    principal_type: PrincipalKind
    note: str | None = None
    created_by: str | None = None
    created_at: datetime
    system: bool = False


class UpsertPolicyExceptionRequest(ApiModel):
    principal_name: str = Field(min_length=1, max_length=256)
    principal_type: PrincipalKind
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("principal_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Principal name cannot be empty")
        return text

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class PolicyExceptionList(ApiModel):
    exceptions: list[PolicyException]
    total: int
    system_principals: list[str] = []


class ApplyPolicyExceptionsResult(ApiModel):
    except_principals: list[str]
    mask_pack_synced: bool = False
    row_filter_pack_synced: bool = False

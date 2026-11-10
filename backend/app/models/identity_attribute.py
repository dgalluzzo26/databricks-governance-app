"""App-managed identity attributes.

Unity Catalog cannot attach attributes to identities yet, so the app stores them. Each
assignment says principal X holds value V of attribute K. Attribute definitions are tied to
one or more governed tags; allowed values come from those tag policies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, computed_field, field_validator, model_validator

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class AttributeSource(StrEnum):
    MANUAL = "MANUAL"
    ACCESS_REQUEST = "ACCESS_REQUEST"
    IDENTITY_POLICY = "IDENTITY_POLICY"


class ValueSource(StrEnum):
    GOVERNED_TAGS = "governed_tags"
    FREE_FORM = "free_form"
    SQL = "sql"


ALL_VALUES = "*"


class GovernedTagSummary(ApiModel):
    tag_key: str
    description: str | None = None
    values: list[str] = []
    value_count: int = 0


class GovernedTagList(ApiModel):
    tags: list[GovernedTagSummary]
    total: int
    truncated: bool = False


class AttributeDefinition(ApiModel):
    key: str
    description: str | None = None
    governed_tag_keys: list[str] = []
    value_source: ValueSource = ValueSource.GOVERNED_TAGS
    # Seed query for value_source=sql; refreshed into allowed_values.
    value_sql: str | None = None
    # Governed tags: resolved live. Free-form / SQL: stored, and always include "*".
    allowed_values: list[str] = []
    created_by: str | None = None
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


def _clean_list(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


class UpsertAttributeDefinitionRequest(ApiModel):
    key: str | None = Field(default=None, max_length=256)
    description: str | None = None
    value_source: ValueSource = ValueSource.GOVERNED_TAGS
    governed_tag_keys: list[str] = []
    allowed_values: list[str] = []
    value_sql: str | None = None

    @field_validator("governed_tag_keys", "allowed_values")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @model_validator(mode="after")
    def _validate_source(self) -> UpsertAttributeDefinitionRequest:
        key = (self.key or "").strip()
        if self.value_source == ValueSource.GOVERNED_TAGS:
            if not self.governed_tag_keys:
                raise ValueError("At least one governed tag key is required")
            key = key or self.governed_tag_keys[0]
        else:
            self.governed_tag_keys = []
            if not key:
                raise ValueError("key is required for free-form and SQL definitions")
        if self.value_source == ValueSource.SQL:
            if not (self.value_sql or "").strip():
                raise ValueError("value_sql is required when value_source is sql")
            self.value_sql = self.value_sql.strip()
        else:
            self.value_sql = None
        self.key = key
        return self


class IdentityAttributeAssignment(ApiModel):
    assignment_id: str
    principal_name: str
    principal_type: PrincipalKind
    attribute_key: str
    attribute_value: str
    source: AttributeSource = AttributeSource.MANUAL
    granted_by: str | None = None
    justification: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires > datetime.now(UTC)


class AssignAttributeRequest(ApiModel):
    principal_name: str = Field(min_length=1)
    principal_type: PrincipalKind
    attribute_key: str = Field(min_length=1)
    attribute_value: str = Field(min_length=1)
    justification: str | None = None
    expires_at: datetime | None = None


class AttributeAssignmentFilter(ApiModel):
    principal_name: str | None = None
    principal_type: PrincipalKind | None = None
    attribute_key: str | None = None
    include_inactive: bool = False


class AttributeDefinitionList(ApiModel):
    definitions: list[AttributeDefinition]
    total: int


class AttributeAssignmentList(ApiModel):
    assignments: list[IdentityAttributeAssignment]
    total: int

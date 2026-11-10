"""Row-filter dimensions (e.g. ``state``, ``region``) enforced by one metastore ABAC policy.

A column tagged ``row_filter=<dimension>`` is filtered so each user only sees rows whose
value matches one of their active ``<dimension>`` identity attributes. ``*`` grants all rows;
no assignment means no rows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel
from backend.app.models.identity_attribute import ValueSource

DIMENSION_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"


class RowFilterDimension(ApiModel):
    key: str
    description: str | None = None
    value_source: ValueSource
    value_sql: str | None = None
    allowed_value_count: int = 0
    sample_values: list[str] = []
    attachment_count: int = 0


class RowFilterDimensionList(ApiModel):
    dimensions: list[RowFilterDimension]
    total: int


class UpsertRowFilterDimensionRequest(ApiModel):
    key: str = Field(pattern=DIMENSION_KEY_PATTERN)
    description: str | None = None
    value_source: Literal[ValueSource.FREE_FORM, ValueSource.SQL]
    allowed_values: list[str] = []
    value_sql: str | None = None

    @field_validator("key", mode="before")
    @classmethod
    def _lower_key(cls, value: str) -> str:
        return str(value or "").strip().lower()


class SeedPreviewRequest(ApiModel):
    value_sql: str = Field(min_length=1)


class SeedPreview(ApiModel):
    values: list[str]
    total: int


class RowFilterPackStatus(ApiModel):
    tag_key: str
    tag_values: list[str] = []
    function_name: str
    policy_name: str
    policy_exists: bool = False
    policy_match_columns: str | None = None
    policy_except_principals: list[str] = []
    dimensions: list[str] = []
    missing_tag_values: list[str] = []
    in_sync: bool = False


class AttachRowFilterRequest(ApiModel):
    table_full_name: str = Field(min_length=5)
    column_name: str = Field(min_length=1)
    dimension: str = Field(pattern=DIMENSION_KEY_PATTERN)


class RowFilterAttachment(ApiModel):
    table_full_name: str
    column_name: str
    dimension: str


class RowFilterAttachmentList(ApiModel):
    attachments: list[RowFilterAttachment]
    total: int

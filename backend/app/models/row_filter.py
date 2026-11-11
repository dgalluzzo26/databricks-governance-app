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
    """Row-filter dimension backed by a free-form or SQL-seeded attribute definition.

    Attributes:
        key: Dimension key; also the identity attribute key and the ``row_filter`` tag value.
        description: Optional human-readable description.
        value_source: Where allowed values come from (``free_form`` or ``sql``).
        value_sql: Seed query when ``value_source`` is ``sql``; otherwise None.
        allowed_value_count: Number of allowed values, excluding the ``*`` wildcard.
        sample_values: Up to 20 allowed values (excluding ``*``) for display.
        attachment_count: Number of columns currently tagged with this dimension.
    """

    key: str
    description: str | None = None
    value_source: ValueSource
    value_sql: str | None = None
    allowed_value_count: int = 0
    sample_values: list[str] = []
    attachment_count: int = 0


class RowFilterDimensionList(ApiModel):
    """All registered row-filter dimensions.

    Attributes:
        dimensions: Registered dimensions.
        total: Number of dimensions.
    """

    dimensions: list[RowFilterDimension]
    total: int


class UpsertRowFilterDimensionRequest(ApiModel):
    """Request body to create or update a row-filter dimension.

    Attributes:
        key: Dimension key; lower-cased and stripped, must match ``DIMENSION_KEY_PATTERN``.
        description: Optional human-readable description.
        value_source: ``free_form`` (admin-entered values) or ``sql`` (seeded by a query).
        allowed_values: Allowed values for free-form dimensions.
        value_sql: Seed query returning allowed values; required for ``sql`` dimensions.
    """

    key: str = Field(pattern=DIMENSION_KEY_PATTERN)
    description: str | None = None
    value_source: Literal[ValueSource.FREE_FORM, ValueSource.SQL]
    allowed_values: list[str] = []
    value_sql: str | None = None

    @field_validator("key", mode="before")
    @classmethod
    def _lower_key(cls, value: str) -> str:
        """Normalize the key to stripped lower case before pattern validation.

        Args:
            value: Raw key; None is treated as an empty string.

        Returns:
            The normalized key.
        """
        return str(value or "").strip().lower()


class SeedPreviewRequest(ApiModel):
    """Request body to preview the values a seed query would produce.

    Attributes:
        value_sql: SQL query whose first column yields candidate values.
    """

    value_sql: str = Field(min_length=1)


class SeedPreview(ApiModel):
    """Preview of values returned by a seed query.

    Attributes:
        values: Up to the first 100 values returned.
        total: Total number of values the query returned.
    """

    values: list[str]
    total: int


class RowFilterPackStatus(ApiModel):
    """Drift report for the row-filter tag, UDF and metastore ABAC policy.

    Attributes:
        tag_key: Governed tag key used to attach dimensions to columns.
        tag_values: Values currently allowed by that governed tag.
        function_name: Fully qualified name of the row-filter UDF.
        policy_name: Name of the metastore-level row-filter policy.
        policy_exists: True when the policy exists on the metastore.
        policy_match_columns: Raw ``match_columns`` of the deployed policy; None if absent.
        policy_except_principals: ``EXCEPT`` principals of the deployed policy.
        dimensions: Keys of the registered dimensions.
        missing_tag_values: Dimensions not yet allowed as values of the governed tag.
        in_sync: True when tag values, policy existence, matched dimensions and exceptions
            all match the registered dimensions.
    """

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
    """Request body to attach a dimension to a table column via the ``row_filter`` tag.

    Attributes:
        table_full_name: Dotted ``catalog.schema.table`` name.
        column_name: Column whose values are compared to the user's attribute values.
        dimension: Registered dimension key to attach.
    """

    table_full_name: str = Field(min_length=5)
    column_name: str = Field(min_length=1)
    dimension: str = Field(pattern=DIMENSION_KEY_PATTERN)


class RowFilterAttachment(ApiModel):
    """Column tagged with a row-filter dimension.

    Attributes:
        table_full_name: Dotted ``catalog.schema.table`` name.
        column_name: Tagged column.
        dimension: Dimension key the column is tagged with.
    """

    table_full_name: str
    column_name: str
    dimension: str


class RowFilterAttachmentList(ApiModel):
    """Columns currently tagged with a registered row-filter dimension.

    Attributes:
        attachments: Tagged columns.
        total: Number of attachments.
    """

    attachments: list[RowFilterAttachment]
    total: int

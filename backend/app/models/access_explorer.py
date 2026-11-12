"""Access Explorer: per-table view of masks, row filters, and suggested attributes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from backend.app.models.base import ApiModel


class ColumnAccessState(StrEnum):
    """What the current user sees for a column.

    Attributes:
        UNMASKED: The user holds the required sensitivity path and sees raw values.
        MASKED: Values are replaced by the column's mask rule handler.
        NOT_GOVERNED: The column lacks the sensitivity / data_type tags needed to match the
            metastore mask policy, so it is not masked.
        GEO_MASKED: Geometry / geography column the user may not see; values become NULL.
    """

    UNMASKED = "unmasked"
    MASKED = "masked"
    NOT_GOVERNED = "not_governed"
    GEO_MASKED = "geo_masked"


class SuggestedAttributeKind(StrEnum):
    """Type of attribute the explorer suggests requesting.

    Attributes:
        SENSITIVITY: A ``sensitivity`` path that would unmask columns.
        ROW_FILTER: A row-filter dimension value that would reveal rows.
        DATA_ACCESS: A ``data_access_control`` (or configured) value for tagged-level grants.
    """

    SENSITIVITY = "sensitivity"
    ROW_FILTER = "row_filter"
    DATA_ACCESS = "data_access"


class RequestRouteKind(StrEnum):
    """Who would review a suggested request.

    Attributes:
        CATEGORY_APPROVERS: Approvers of the matching sensitivity category.
        APP_ADMINS: App admins (no category approvers apply, or a row-filter attribute).
    """

    CATEGORY_APPROVERS = "category_approvers"
    APP_ADMINS = "app_admins"


class ApproverPreview(ApiModel):
    """Approver who would review a suggested request.

    Attributes:
        principal_name: User name or group name.
        principal_type: Principal kind as a string (``USER`` or ``GROUP``).
    """

    principal_name: str
    principal_type: str


class TableSearchHit(ApiModel):
    """Table matching an explorer search.

    Attributes:
        catalog_name: Catalog containing the table.
        schema_name: Schema containing the table.
        table_name: Table name.
    """

    catalog_name: str
    schema_name: str
    table_name: str

    @property
    def full_name(self) -> str:
        """Dotted ``catalog.schema.table`` name."""
        return f"{self.catalog_name}.{self.schema_name}.{self.table_name}"


class TableSearchResult(ApiModel):
    """Result of a table search.

    Attributes:
        tables: Matching tables.
        total: Number of tables returned.
    """

    tables: list[TableSearchHit]
    total: int


class ColumnTagMap(ApiModel):
    """Governance tag values on a column; None when the tag is not set.

    Attributes:
        sensitivity_category: ``sensitivity_category`` tag (category part of the path).
        sensitivity: ``sensitivity`` tag (level within the category).
        mask_rule: ``mask_rule`` tag selecting how denied values look.
        data_type: ``data_type`` tag used by the mask policies to pick a masking function.
        row_filter: ``row_filter`` tag naming the row-filter dimension on this column.
    """

    sensitivity_category: str | None = None
    sensitivity: str | None = None
    mask_rule: str | None = None
    data_type: str | None = None
    row_filter: str | None = None


class ColumnAccessInsight(ApiModel):
    """How masking applies to one column for the current user.

    Attributes:
        column_name: Column name.
        data_type: SQL type of the column, or None when unknown.
        tags: Governance tag values on the column.
        access_state: What the user sees.
        required_sensitivity_path: ``<sensitivity_category>.<sensitivity>`` path needed to
            unmask; None when either tag is missing.
        user_has_access: True when the user holds that path or a parent of it (also True for
            columns without sensitivity tags).
        mask_rule_key: Effective mask rule key (defaults to ``full``); None for geo or
            ungoverned columns.
        mask_handler: Handler of that rule; ``full`` when the rule is unknown.
        mask_kind: ``builtin`` or ``custom``; None when no mask rule applies.
        mask_description: Human-readable description of the denied presentation.
        policy_name: ABAC policy that masks the column, if any.
        notes: Explanation shown in the UI, or None.
    """

    column_name: str
    data_type: str | None = None
    tags: ColumnTagMap
    access_state: ColumnAccessState
    required_sensitivity_path: str | None = None
    user_has_access: bool = False
    mask_rule_key: str | None = None
    mask_handler: str | None = None
    mask_kind: str | None = None
    mask_description: str | None = None
    policy_name: str | None = None
    notes: str | None = None


class RowFilterInsight(ApiModel):
    """How the row-filter policy applies to a table for the current user.

    Attributes:
        attached: True when a column of the table is tagged with a row-filter dimension.
        dimension_key: Dimension attached to the table, or None.
        column_name: Tagged column, or None.
        policy_name: Name of the metastore row-filter policy.
        function_name: Fully qualified name of the row-filter UDF.
        user_has_wildcard: True when the user holds ``*`` for the dimension (sees all rows).
        user_values: Dimension values the user holds, sorted case-insensitively.
        user_can_see_rows: True when the user holds any value; with none, every row is hidden.
        notes: Explanation shown in the UI, or None.
    """

    attached: bool = False
    dimension_key: str | None = None
    column_name: str | None = None
    policy_name: str | None = None
    function_name: str | None = None
    user_has_wildcard: bool = False
    user_values: list[str] = []
    user_can_see_rows: bool = False
    notes: str | None = None


class SuggestedAttribute(ApiModel):
    """Attribute value the user could request to see more of a table.

    Attributes:
        kind: Sensitivity or row-filter suggestion.
        attribute_key: Attribute key to request (``sensitivity`` or the dimension key).
        attribute_value: Value to request (a sensitivity path, dimension value or ``*``).
        reason: Human-readable explanation, including who would review it.
        requestable: True when a request can be submitted now (value is allowed and, for
            row filters, the dimension definition exists).
        request_path: Frontend route that pre-fills the request form; None when not
            requestable.
        column_names: Columns the suggestion would unmask or reveal rows for.
        covers_paths: Required sensitivity paths (or dimension values) this value satisfies.
        is_hierarchy_parent: True for a broader parent path covering several required paths.
        routed_to: Who would review the request.
        resolved_category: Sensitivity category the request would route to, or None.
        approvers: Category approvers who would review it; empty for app admins.
    """

    kind: SuggestedAttributeKind
    attribute_key: str
    attribute_value: str
    reason: str
    requestable: bool = False
    request_path: str | None = None
    column_names: list[str] = []
    covers_paths: list[str] = []
    is_hierarchy_parent: bool = False
    routed_to: RequestRouteKind | None = None
    resolved_category: str | None = None
    approvers: list[ApproverPreview] = []


class TableAccessInsight(ApiModel):
    """Access analysis of a catalog, schema, or table for the current user.

    Attributes:
        table_full_name: Object full name (1–3 parts). Kept for wire compatibility.
        securable_level: ``catalog``, ``schema``, or ``table``.
        catalog_name: Catalog containing the object.
        schema_name: Schema name; empty for catalog-level analysis.
        table_name: Table name; empty for catalog/schema analysis.
        table_type: Unity Catalog table type (``MANAGED``, ``EXTERNAL``, ``VIEW``, ...).
        columns: Per-column masking insight. For views, inherited from source columns via lineage.
        row_filter: First row-filter insight, or a not-attached placeholder.
        row_filters: Every row-filter dimension in effect (views can inherit several).
        suggestions: Attribute values the user could request.
        summary: One-line human-readable summary of the analysis.
    """

    table_full_name: str
    securable_level: str = "table"
    catalog_name: str
    schema_name: str = ""
    table_name: str = ""
    table_type: str = ""
    columns: list[ColumnAccessInsight] = Field(default_factory=list)
    row_filter: RowFilterInsight = Field(default_factory=RowFilterInsight)
    row_filters: list[RowFilterInsight] = Field(default_factory=list)
    suggestions: list[SuggestedAttribute] = Field(default_factory=list)
    summary: str


class AnalyzeTableRequest(ApiModel):
    """Request body to analyze a catalog, schema, or table.

    Attributes:
        full_name: ``catalog``, ``catalog.schema``, or ``catalog.schema.table``.
        table_full_name: Deprecated alias accepted for older clients.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=512)
    table_full_name: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _require_one_name(self) -> AnalyzeTableRequest:
        """Require ``full_name`` or the legacy ``table_full_name``."""
        if not (self.full_name or "").strip() and not (self.table_full_name or "").strip():
            raise ValueError("full_name is required")
        return self

    def resolved_name(self) -> str:
        """Return the object name from ``full_name`` or the legacy ``table_full_name`` field."""
        return (self.full_name or self.table_full_name or "").strip()


class TreeCatalog(ApiModel):
    """Catalog node in the explorer tree.

    Attributes:
        catalog_name: Catalog name.
    """

    catalog_name: str


class TreeCatalogList(ApiModel):
    """Catalogs for the explorer tree.

    Attributes:
        catalogs: Returned catalogs.
        total: Number of catalogs returned.
        truncated: True when more catalogs exist than were returned.
    """

    catalogs: list[TreeCatalog]
    total: int
    truncated: bool = False


class TreeSchema(ApiModel):
    """Schema node in the explorer tree.

    Attributes:
        catalog_name: Parent catalog.
        schema_name: Schema name.
    """

    catalog_name: str
    schema_name: str


class TreeSchemaList(ApiModel):
    """Schemas of a catalog for the explorer tree.

    Attributes:
        schemas: Returned schemas.
        total: Number of schemas returned.
        truncated: True when more schemas exist than were returned.
    """

    schemas: list[TreeSchema]
    total: int
    truncated: bool = False


class TreeTable(ApiModel):
    """Table node in the explorer tree.

    Attributes:
        catalog_name: Parent catalog.
        schema_name: Parent schema.
        table_name: Table name.
        table_type: Unity Catalog table type, e.g. ``MANAGED``, ``EXTERNAL`` or ``VIEW``.
    """

    catalog_name: str
    schema_name: str
    table_name: str
    table_type: str


class TreeTableList(ApiModel):
    """Tables of a schema for the explorer tree.

    Attributes:
        tables: Returned tables.
        total: Number of tables returned.
        truncated: True when more tables exist than were returned.
    """

    tables: list[TreeTable]
    total: int
    truncated: bool = False

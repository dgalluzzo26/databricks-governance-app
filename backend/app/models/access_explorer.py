"""Access Explorer: per-table view of masks, row filters, and suggested attributes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from backend.app.models.base import ApiModel


class ColumnAccessState(StrEnum):
    UNMASKED = "unmasked"
    MASKED = "masked"
    NOT_GOVERNED = "not_governed"
    GEO_MASKED = "geo_masked"


class SuggestedAttributeKind(StrEnum):
    SENSITIVITY = "sensitivity"
    ROW_FILTER = "row_filter"


class TableSearchHit(ApiModel):
    catalog_name: str
    schema_name: str
    table_name: str

    @property
    def full_name(self) -> str:
        return f"{self.catalog_name}.{self.schema_name}.{self.table_name}"


class TableSearchResult(ApiModel):
    tables: list[TableSearchHit]
    total: int


class ColumnTagMap(ApiModel):
    sensitivity_category: str | None = None
    sensitivity: str | None = None
    mask_rule: str | None = None
    data_type: str | None = None
    row_filter: str | None = None


class ColumnAccessInsight(ApiModel):
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
    kind: SuggestedAttributeKind
    attribute_key: str
    attribute_value: str
    reason: str
    requestable: bool = False
    request_path: str | None = None
    column_names: list[str] = []


class TableAccessInsight(ApiModel):
    table_full_name: str
    catalog_name: str
    schema_name: str
    table_name: str
    columns: list[ColumnAccessInsight]
    row_filter: RowFilterInsight
    suggestions: list[SuggestedAttribute]
    summary: str


class AnalyzeTableRequest(ApiModel):
    table_full_name: str = Field(min_length=5, max_length=512)


class TreeCatalog(ApiModel):
    catalog_name: str


class TreeCatalogList(ApiModel):
    catalogs: list[TreeCatalog]
    total: int
    truncated: bool = False


class TreeSchema(ApiModel):
    catalog_name: str
    schema_name: str


class TreeSchemaList(ApiModel):
    schemas: list[TreeSchema]
    total: int
    truncated: bool = False


class TreeTable(ApiModel):
    catalog_name: str
    schema_name: str
    table_name: str
    table_type: str


class TreeTableList(ApiModel):
    tables: list[TreeTable]
    total: int
    truncated: bool = False

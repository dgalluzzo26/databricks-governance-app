from __future__ import annotations

from backend.app.models.base import ApiModel


class TagRef(ApiModel):
    key: str
    value: str | None = None


class MatchColumnInfo(ApiModel):
    alias: str
    condition: str | None = None


class ColumnMaskInfo(ApiModel):
    function_name: str
    on_column: str


class RowFilterInfo(ApiModel):
    function_name: str
    using: list[str] = []


class GrantDenyInfo(ApiModel):
    privileges: list[str] = []


class AffectedAsset(ApiModel):
    asset_type: str
    full_name: str
    catalog_name: str | None = None
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    matched_tag_key: str | None = None
    matched_tag_value: str | None = None


class PolicySummary(ApiModel):
    id: str
    name: str
    policy_type: str
    scope_kind: str  # metastore | catalog
    on_securable_type: str
    on_securable_fullname: str
    catalog_name: str | None = None
    for_securable_type: str | None = None
    to_principals: list[str] = []
    except_principals: list[str] = []
    when_condition: str | None = None
    governed_tags: list[TagRef] = []
    privileges: list[str] = []
    created_by: str | None = None


class PolicyDetail(PolicySummary):
    comment: str | None = None
    match_columns: list[MatchColumnInfo] = []
    column_mask: ColumnMaskInfo | None = None
    row_filter: RowFilterInfo | None = None
    grant: GrantDenyInfo | None = None
    deny: GrantDenyInfo | None = None
    created_at: int | None = None
    updated_at: int | None = None
    updated_by: str | None = None
    affected_assets: list[AffectedAsset] = []
    affected_assets_truncated: bool = False
    affected_assets_note: str | None = None


class PolicyListResponse(ApiModel):
    """Policies are returned in two buckets: metastore-level vs catalog-specific."""

    metastore_name: str | None = None
    metastore: list[PolicySummary]
    catalog: list[PolicySummary]
    total: int

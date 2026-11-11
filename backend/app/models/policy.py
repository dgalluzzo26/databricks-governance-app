"""Read-only models for Unity Catalog ABAC policies and the assets they affect."""

from __future__ import annotations

from backend.app.models.base import ApiModel


class TagRef(ApiModel):
    """Governed tag referenced by a policy.

    Attributes:
        key: Governed tag key.
        value: Specific tag value the policy matches; None means any value of ``key``.
    """

    key: str
    value: str | None = None


class MatchColumnInfo(ApiModel):
    """Column-matching clause of an ABAC policy (``MATCH COLUMNS ... AS alias``).

    Attributes:
        alias: Alias the matched column is bound to inside the policy.
        condition: Tag condition columns must satisfy, or None when not reported.
    """

    alias: str
    condition: str | None = None


class ColumnMaskInfo(ApiModel):
    """Column-mask action of an ABAC policy.

    Attributes:
        function_name: Fully qualified name of the masking UDF.
        on_column: Match-column alias the mask is applied to; empty when not reported.
    """

    function_name: str
    on_column: str


class RowFilterInfo(ApiModel):
    """Row-filter action of an ABAC policy.

    Attributes:
        function_name: Fully qualified name of the row-filter UDF.
        using: Match-column aliases passed to the UDF as arguments, in order.
    """

    function_name: str
    using: list[str] = []


class GrantDenyInfo(ApiModel):
    """Privileges granted or denied by a privilege-type ABAC policy.

    Attributes:
        privileges: Privilege names, e.g. ``SELECT``.
    """

    privileges: list[str] = []


class AffectedAsset(ApiModel):
    """Table or column whose governed tags match a policy.

    Attributes:
        asset_type: ``TABLE`` or ``COLUMN`` (``UNKNOWN`` if the source row lacked a type).
        full_name: Dotted name of the asset (``catalog.schema.table[.column]``).
        catalog_name: Catalog containing the asset.
        schema_name: Schema containing the asset.
        table_name: Table name, or the table containing the column.
        column_name: Column name; None for table-level assets.
        matched_tag_key: Governed tag key that caused the match.
        matched_tag_value: Value of the matched tag, or None when the tag has no value.
    """

    asset_type: str
    full_name: str
    catalog_name: str | None = None
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    matched_tag_key: str | None = None
    matched_tag_value: str | None = None


class PolicySummary(ApiModel):
    """List-view summary of an ABAC policy from ``information_schema``.

    Attributes:
        id: Unity Catalog policy ID.
        name: Policy name, unique on its securable.
        policy_type: Normalized policy type, e.g. column mask, row filter, grant or deny.
        scope_kind: ``metastore`` when attached to the metastore, otherwise ``catalog``.
        on_securable_type: Upper-case type of the securable the policy is attached to
            (``METASTORE``, ``CATALOG``, ``SCHEMA``, ``TABLE``).
        on_securable_fullname: Dotted name of that securable; the metastore name (or
            ``metastore``) for metastore-level policies.
        catalog_name: Catalog of the securable; None for metastore-level policies.
        for_securable_type: Upper-case type of securables the policy applies to, or None.
        to_principals: Principals the policy applies to.
        except_principals: Principals exempted from the policy.
        when_condition: Optional ``WHEN`` tag condition limiting where the policy applies.
        governed_tags: Governed tags referenced by the policy.
        privileges: Privileges granted or denied, for privilege-type policies.
        created_by: Principal that created the policy, or None when unknown.
    """

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
    """Full policy definition enriched from the policies API, plus affected assets.

    Detail fields default to empty / None when the policies API call fails; the summary
    fields inherited from ``PolicySummary`` are always present.

    Attributes:
        comment: Free-text policy comment.
        match_columns: Column-matching clauses.
        column_mask: Column-mask action, for column-mask policies.
        row_filter: Row-filter action, for row-filter policies.
        grant: Granted privileges, for grant policies.
        deny: Denied privileges, for deny policies.
        created_at: Creation time as an epoch timestamp from the policies API.
        updated_at: Last update time as an epoch timestamp from the policies API.
        updated_by: Principal that last updated the policy.
        affected_assets: Tagged tables / columns that match the policy's governed tags.
        affected_assets_truncated: True when more assets matched than were returned.
        affected_assets_note: Explanation when asset discovery was skipped or limited.
    """

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
    """Policies are returned in two buckets: metastore-level vs catalog-specific.

    Attributes:
        metastore_name: Name of the current metastore, or None when it could not be resolved.
        metastore: Policies attached to the metastore.
        catalog: Policies attached to a catalog, schema or table.
        total: Number of policies across both buckets after filtering.
    """

    metastore_name: str | None = None
    metastore: list[PolicySummary]
    catalog: list[PolicySummary]
    total: int

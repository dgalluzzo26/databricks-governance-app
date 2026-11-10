from __future__ import annotations

import json
import logging
from typing import Any

from databricks.sdk.errors import NotFound

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import NotFoundError, UpstreamError
from backend.app.models.policy import (
    AffectedAsset,
    ColumnMaskInfo,
    GrantDenyInfo,
    MatchColumnInfo,
    PolicyDetail,
    PolicyListResponse,
    PolicySummary,
    RowFilterInfo,
    TagRef,
)

logger = logging.getLogger(__name__)

_LIST_SQL = """
SELECT
  policy_id,
  policy_name,
  policy_type,
  catalog_name,
  schema_name,
  securable_name,
  on_securable_type,
  to_principals,
  except_principals,
  for_securable_type,
  privileges,
  when_condition,
  match_columns,
  governed_tags,
  created_by
FROM system.information_schema.abac_policy_definitions
ORDER BY
  CASE upper(on_securable_type)
    WHEN 'METASTORE' THEN 0
    WHEN 'CATALOG' THEN 1
    WHEN 'SCHEMA' THEN 2
    ELSE 3
  END,
  coalesce(catalog_name, ''),
  coalesce(schema_name, ''),
  policy_name
"""

_ASSET_LIMIT = 2000


def _parse_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    text = str(raw).strip()
    if not text or text == "null":
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _parse_string_list(raw: Any) -> list[str]:
    return [str(item) for item in _parse_json_list(raw) if item is not None and str(item).strip()]


def _parse_tags(raw: Any) -> list[TagRef]:
    tags: list[TagRef] = []
    for item in _parse_json_list(raw):
        if isinstance(item, dict):
            key = item.get("tag_key") or item.get("key")
            if key:
                value = item.get("tag_value") if "tag_value" in item else item.get("value")
                tags.append(TagRef(key=str(key), value=None if value is None else str(value)))
        elif isinstance(item, str) and item.strip():
            tags.append(TagRef(key=item.strip()))
    return tags


def _normalize_policy_type(raw: str | None) -> str:
    text = (raw or "").strip().upper()
    if text.startswith("POLICY_TYPE_"):
        text = text[len("POLICY_TYPE_") :]
    return text or "UNKNOWN"


def _normalize_securable_type(raw: str | None) -> str:
    return (raw or "").strip().upper() or "UNKNOWN"


def _scope_kind(on_securable_type: str) -> str:
    return "metastore" if on_securable_type == "METASTORE" else "catalog"


class PolicyService:
    """Read-only explorer for Unity Catalog ABAC policies (metastore and catalog scopes)."""

    def __init__(
        self,
        factory: WorkspaceClientFactory,
        sql: SqlExecutor,
        cache: TtlCache,
    ) -> None:
        self._factory = factory
        self._sql = sql
        self._cache = cache

    def list_policies(
        self,
        *,
        scope: str | None = None,
        policy_type: str | None = None,
        catalog: str | None = None,
        q: str | None = None,
    ) -> PolicyListResponse:
        rows = self._cached_rows()
        metastore_name = self._metastore_name()
        summaries = [self._to_summary(row, metastore_name) for row in rows]

        scope_filter = (scope or "").strip().lower()
        type_filter = _normalize_policy_type(policy_type) if policy_type else ""
        catalog_filter = (catalog or "").strip()
        query = (q or "").strip().lower()

        def matches(item: PolicySummary) -> bool:
            if scope_filter in {"metastore", "catalog"} and item.scope_kind != scope_filter:
                return False
            if type_filter and item.policy_type != type_filter:
                return False
            if catalog_filter:
                if item.scope_kind == "metastore":
                    return False
                if (item.catalog_name or "").lower() != catalog_filter.lower():
                    return False
            if query:
                haystack = " ".join(
                    [
                        item.name,
                        item.on_securable_fullname,
                        item.when_condition or "",
                        " ".join(item.to_principals),
                        " ".join(f"{t.key}:{t.value or ''}" for t in item.governed_tags),
                    ]
                ).lower()
                if query not in haystack:
                    return False
            return True

        filtered = [item for item in summaries if matches(item)]
        metastore = [item for item in filtered if item.scope_kind == "metastore"]
        catalog_policies = [item for item in filtered if item.scope_kind == "catalog"]
        return PolicyListResponse(
            metastore_name=metastore_name,
            metastore=metastore,
            catalog=catalog_policies,
            total=len(filtered),
        )

    def get_policy(self, policy_id: str) -> PolicyDetail:
        row = next((r for r in self._cached_rows() if str(r.get("policy_id")) == policy_id), None)
        if row is None:
            raise NotFoundError(f"Policy {policy_id} was not found")

        metastore_name = self._metastore_name()
        summary = self._to_summary(row, metastore_name)
        detail = PolicyDetail(**summary.model_dump())

        try:
            info = self._factory.admin().policies.get_policy(
                summary.on_securable_type.lower(),
                summary.on_securable_fullname,
                summary.name,
            )
        except NotFound as exc:
            raise NotFoundError(
                f"Policy {summary.name} was not found on {summary.on_securable_fullname}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_policy failed for %s: %s", policy_id, exc)
            info = None

        if info is not None:
            detail.comment = getattr(info, "comment", None)
            detail.created_at = getattr(info, "created_at", None)
            detail.updated_at = getattr(info, "updated_at", None)
            detail.updated_by = getattr(info, "updated_by", None)
            detail.to_principals = list(
                getattr(info, "to_principals", None) or detail.to_principals
            )
            detail.except_principals = list(
                getattr(info, "except_principals", None) or detail.except_principals
            )
            detail.when_condition = getattr(info, "when_condition", None) or detail.when_condition
            if getattr(info, "governed_tags", None):
                detail.governed_tags = [
                    TagRef(key=t.tag_key, value=getattr(t, "tag_value", None))
                    for t in info.governed_tags
                    if getattr(t, "tag_key", None)
                ]
            detail.match_columns = [
                MatchColumnInfo(alias=m.alias, condition=getattr(m, "condition", None))
                for m in (getattr(info, "match_columns", None) or [])
                if getattr(m, "alias", None)
            ]
            mask = getattr(info, "column_mask", None)
            if mask and getattr(mask, "function_name", None):
                detail.column_mask = ColumnMaskInfo(
                    function_name=mask.function_name,
                    on_column=getattr(mask, "on_column", "") or "",
                )
            row_filter = getattr(info, "row_filter", None)
            if row_filter and getattr(row_filter, "function_name", None):
                using = []
                for arg in getattr(row_filter, "using", None) or []:
                    alias = getattr(arg, "alias", None)
                    if alias:
                        using.append(alias)
                detail.row_filter = RowFilterInfo(function_name=row_filter.function_name, using=using)
            grant = getattr(info, "grant", None)
            if grant and getattr(grant, "privileges", None):
                detail.grant = GrantDenyInfo(privileges=[str(p) for p in grant.privileges])
                detail.privileges = detail.grant.privileges
            deny = getattr(info, "deny", None)
            if deny and getattr(deny, "privileges", None):
                detail.deny = GrantDenyInfo(privileges=[str(p) for p in deny.privileges])
                detail.privileges = detail.deny.privileges

        assets, truncated, note = self._affected_assets(detail)
        detail.affected_assets = assets
        detail.affected_assets_truncated = truncated
        detail.affected_assets_note = note
        return detail

    def _cached_rows(self) -> list[dict[str, Any]]:
        return self._cache.get_or_load(("abac-policy-definitions",), self._load_rows)

    def _load_rows(self) -> list[dict[str, Any]]:
        try:
            return self._sql.query(_LIST_SQL)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not list ABAC policies: {exc}") from exc

    def _metastore_name(self) -> str | None:
        def load() -> str | None:
            try:
                assignment = self._factory.admin().metastores.current()
                metastore = self._factory.admin().metastores.get(assignment.metastore_id)
                return getattr(metastore, "name", None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not resolve metastore name: %s", exc)
                return None

        return self._cache.get_or_load(("metastore-name",), load)

    def _to_summary(self, row: dict[str, Any], metastore_name: str | None) -> PolicySummary:
        on_type = _normalize_securable_type(row.get("on_securable_type"))
        on_name = self._on_securable_fullname(row, on_type, metastore_name)
        catalog_name = row.get("catalog_name")
        if on_type == "METASTORE":
            catalog_name = None
        return PolicySummary(
            id=str(row.get("policy_id") or ""),
            name=str(row.get("policy_name") or ""),
            policy_type=_normalize_policy_type(row.get("policy_type")),
            scope_kind=_scope_kind(on_type),
            on_securable_type=on_type,
            on_securable_fullname=on_name,
            catalog_name=None if catalog_name is None else str(catalog_name),
            for_securable_type=_normalize_securable_type(row.get("for_securable_type"))
            if row.get("for_securable_type")
            else None,
            to_principals=_parse_string_list(row.get("to_principals")),
            except_principals=_parse_string_list(row.get("except_principals")),
            when_condition=row.get("when_condition"),
            governed_tags=_parse_tags(row.get("governed_tags")),
            privileges=_parse_string_list(row.get("privileges")),
            created_by=row.get("created_by"),
        )

    @staticmethod
    def _on_securable_fullname(row: dict[str, Any], on_type: str, metastore_name: str | None) -> str:
        if on_type == "METASTORE":
            return metastore_name or "metastore"
        catalog = row.get("catalog_name")
        schema = row.get("schema_name")
        securable = row.get("securable_name")
        if on_type == "CATALOG":
            return str(catalog or "")
        if on_type == "SCHEMA":
            return f"{catalog}.{schema}"
        if on_type == "TABLE":
            return f"{catalog}.{schema}.{securable}"
        parts = [p for p in (catalog, schema, securable) if p]
        return ".".join(str(p) for p in parts)

    def _affected_assets(self, policy: PolicyDetail) -> tuple[list[AffectedAsset], bool, str | None]:
        if policy.for_securable_type and policy.for_securable_type not in {"TABLE", "SCHEMA", "CATALOG"}:
            return (
                [],
                False,
                f"Affected-asset discovery for {policy.for_securable_type} is not available yet; "
                "showing principals, tags, and conditions only.",
            )
        if not policy.governed_tags:
            return (
                [],
                False,
                "This policy does not reference governed tags, so tagged-asset discovery was skipped.",
            )

        predicates: list[str] = []
        for tag in policy.governed_tags:
            key = string_literal(tag.key)
            if tag.value is None:
                predicates.append(f"tag_name = {key}")
            else:
                predicates.append(f"(tag_name = {key} AND tag_value = {string_literal(tag.value)})")
        tag_sql = " OR ".join(predicates)
        scope_sql = self._scope_filter_sql(policy)

        limit = _ASSET_LIMIT + 1
        column_sql = f"""
            SELECT
              'COLUMN' AS asset_type,
              catalog_name, schema_name, table_name, column_name, tag_name, tag_value
            FROM system.information_schema.column_tags
            WHERE ({tag_sql}) {scope_sql}
            LIMIT {limit}
        """
        table_sql = f"""
            SELECT
              'TABLE' AS asset_type,
              catalog_name, schema_name, table_name,
              CAST(NULL AS STRING) AS column_name,
              tag_name, tag_value
            FROM system.information_schema.table_tags
            WHERE ({tag_sql}) {scope_sql}
            LIMIT {limit}
        """

        assets: list[AffectedAsset] = []
        try:
            for row in self._sql.query(column_sql) + self._sql.query(table_sql):
                catalog = row.get("catalog_name")
                schema = row.get("schema_name")
                table = row.get("table_name")
                column = row.get("column_name")
                parts = [p for p in (catalog, schema, table, column) if p]
                assets.append(
                    AffectedAsset(
                        asset_type=str(row.get("asset_type") or "UNKNOWN"),
                        full_name=".".join(str(p) for p in parts),
                        catalog_name=None if catalog is None else str(catalog),
                        schema_name=None if schema is None else str(schema),
                        table_name=None if table is None else str(table),
                        column_name=None if column is None else str(column),
                        matched_tag_key=None if row.get("tag_name") is None else str(row.get("tag_name")),
                        matched_tag_value=None if row.get("tag_value") is None else str(row.get("tag_value")),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Affected asset lookup failed for %s: %s", policy.id, exc)
            return [], False, f"Could not resolve tagged assets: {exc}"

        # De-dupe while preserving order.
        seen: set[str] = set()
        unique: list[AffectedAsset] = []
        for asset in assets:
            key = f"{asset.asset_type}:{asset.full_name}:{asset.matched_tag_key}:{asset.matched_tag_value}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(asset)

        truncated = len(unique) > _ASSET_LIMIT
        unique = unique[:_ASSET_LIMIT]
        note = (
            "Tagged assets in scope that match any governed tag referenced by the policy "
            "(approximate; full WHEN/MATCH condition evaluation is not applied)."
        )
        if truncated:
            note += f" Showing the first {_ASSET_LIMIT} matches; refine tags or scope to see the rest."
        return unique, truncated, note

    @staticmethod
    def _scope_filter_sql(policy: PolicyDetail) -> str:
        if policy.on_securable_type == "METASTORE":
            return ""
        if policy.on_securable_type == "CATALOG" and policy.catalog_name:
            return f" AND catalog_name = {string_literal(policy.catalog_name)}"
        if policy.on_securable_type == "SCHEMA":
            parts = policy.on_securable_fullname.split(".", 1)
            if len(parts) == 2:
                return (
                    f" AND catalog_name = {string_literal(parts[0])}"
                    f" AND schema_name = {string_literal(parts[1])}"
                )
        if policy.on_securable_type == "TABLE":
            parts = policy.on_securable_fullname.split(".", 2)
            if len(parts) == 3:
                return (
                    f" AND catalog_name = {string_literal(parts[0])}"
                    f" AND schema_name = {string_literal(parts[1])}"
                    f" AND table_name = {string_literal(parts[2])}"
                )
        if policy.catalog_name:
            return f" AND catalog_name = {string_literal(policy.catalog_name)}"
        return ""

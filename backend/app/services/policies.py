"""Read-only explorer for Unity Catalog ABAC policies.

Lists metastore- and catalog-scoped ABAC policies from
``system.information_schema.abac_policy_definitions`` (cached), enriches a single policy with
details from the Policies API, and finds tables and columns the policy actually targets by
evaluating its ``WHEN`` and ``MATCH COLUMNS`` tag conditions against information_schema tags
(including catalog/schema tag inheritance for ``WHEN``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from databricks.sdk.errors import NotFound

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, NotFoundError, UpstreamError
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
from backend.app.services.tag_conditions import (
    having_sql,
    parse_tag_condition,
    referenced_tag_keys,
    strip_match_alias,
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
    """Parse a list or JSON-array string from information_schema; anything else is empty."""
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
    """Parse a JSON list into non-blank strings."""
    return [str(item) for item in _parse_json_list(raw) if item is not None and str(item).strip()]


def _parse_tags(raw: Any) -> list[TagRef]:
    """Parse governed tag references given as objects (key/value) or bare key strings."""
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


def _parse_match_columns(raw: Any) -> list[MatchColumnInfo]:
    """Parse ``MATCH COLUMNS`` from information_schema (JSON array of ``expr AS alias`` strings)."""
    items = _parse_json_list(raw)
    if not items and isinstance(raw, str) and raw.strip() and not raw.strip().startswith("["):
        items = [raw]
    matches: list[MatchColumnInfo] = []
    for item in items:
        if isinstance(item, dict):
            alias = item.get("alias")
            condition = item.get("condition")
            if condition:
                matches.append(
                    MatchColumnInfo(
                        alias=str(alias or "c"),
                        condition=str(condition).strip() or None,
                    )
                )
            continue
        if not isinstance(item, str) or not item.strip():
            continue
        condition, alias = strip_match_alias(item)
        if condition:
            matches.append(MatchColumnInfo(alias=alias or "c", condition=condition))
    return matches


def _normalize_policy_type(raw: str | None) -> str:
    """Upper-case a policy type and strip the ``POLICY_TYPE_`` prefix (``UNKNOWN`` if blank)."""
    text = (raw or "").strip().upper()
    if text.startswith("POLICY_TYPE_"):
        text = text[len("POLICY_TYPE_") :]
    return text or "UNKNOWN"


def _normalize_securable_type(raw: str | None) -> str:
    """Upper-case a securable type (``UNKNOWN`` if blank)."""
    return (raw or "").strip().upper() or "UNKNOWN"


def _scope_kind(on_securable_type: str) -> str:
    """Map a securable type to the UI scope: ``metastore`` or ``catalog`` (everything else)."""
    return "metastore" if on_securable_type == "METASTORE" else "catalog"


class PolicyService:
    """Read-only explorer for Unity Catalog ABAC policies (metastore and catalog scopes).

    Args:
        factory: Workspace client factory; the admin client reads policies and metastores.
        sql: SQL executor for information_schema queries.
        cache: TTL cache for policy definitions and the metastore name.
    """

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
        """List ABAC policies grouped into metastore and catalog scopes.

        Args:
            scope: ``metastore`` or ``catalog`` to restrict scope; other values are ignored.
            policy_type: Policy type filter (e.g. ``COLUMN_MASK``), with or without the
                ``POLICY_TYPE_`` prefix.
            catalog: Catalog name filter (case-insensitive); excludes metastore policies.
            q: Case-insensitive text search over name, securable, condition, principals, and
                governed tags.

        Returns:
            Filtered policies split by scope, with the metastore name and total count.

        Raises:
            UpstreamError: If the policy definitions cannot be queried.
        """
        rows = self._cached_rows()
        metastore_name = self._metastore_name()
        summaries = [self._to_summary(row, metastore_name) for row in rows]

        scope_filter = (scope or "").strip().lower()
        type_filter = _normalize_policy_type(policy_type) if policy_type else ""
        catalog_filter = (catalog or "").strip()
        query = (q or "").strip().lower()

        def matches(item: PolicySummary) -> bool:
            """Whether a policy passes every active filter."""
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
        """Return one policy with API details and affected assets from WHEN/MATCH evaluation.

        Starts from the cached information_schema row and overlays comment, timestamps,
        principals, match columns, mask/row-filter functions, and grant/deny privileges from
        the Policies API. If the API call fails for reasons other than not-found, the
        information_schema data is returned alone.

        Args:
            policy_id: Policy identifier from the list view.

        Returns:
            The policy detail including tables/columns that satisfy its WHEN and MATCH
            COLUMNS tag conditions (capped).

        Raises:
            NotFoundError: If the policy is not in information_schema or the Policies API
                reports it missing.
            UpstreamError: If the policy definitions cannot be queried.
        """
        row = next((r for r in self._cached_rows() if str(r.get("policy_id")) == policy_id), None)
        if row is None:
            raise NotFoundError(f"Policy {policy_id} was not found")

        metastore_name = self._metastore_name()
        summary = self._to_summary(row, metastore_name)
        detail = PolicyDetail(
            **summary.model_dump(),
            match_columns=_parse_match_columns(row.get("match_columns")),
        )

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
            api_matches = [
                MatchColumnInfo(alias=m.alias, condition=getattr(m, "condition", None))
                for m in (getattr(info, "match_columns", None) or [])
                if getattr(m, "alias", None)
            ]
            # Keep information_schema MATCH COLUMNS when the API omits them.
            if api_matches:
                detail.match_columns = api_matches
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
        """Return policy definition rows from the cache, loading them on a miss."""
        return self._cache.get_or_load(("abac-policy-definitions",), self._load_rows)

    def _load_rows(self) -> list[dict[str, Any]]:
        """Query all ABAC policy definitions.

        Raises:
            UpstreamError: If the query fails.
        """
        try:
            return self._sql.query(_LIST_SQL)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not list ABAC policies: {exc}") from exc

    def _metastore_name(self) -> str | None:
        """Return the current metastore's name (cached), or None if it cannot be resolved."""

        def load() -> str | None:
            """Resolve the metastore name via the admin client; None on failure."""
            try:
                assignment = self._factory.admin().metastores.current()
                metastore = self._factory.admin().metastores.get(assignment.metastore_id)
                return getattr(metastore, "name", None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not resolve metastore name: %s", exc)
                return None

        return self._cache.get_or_load(("metastore-name",), load)

    def _to_summary(self, row: dict[str, Any], metastore_name: str | None) -> PolicySummary:
        """Convert an information_schema row into a normalized policy summary."""
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
        """Build the dotted full name of the securable the policy is attached to."""
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
        """Find tables/columns in scope that satisfy the policy's WHEN and MATCH COLUMNS.

        ``WHEN`` is evaluated against table tags plus inherited catalog/schema tags.
        ``MATCH COLUMNS`` is evaluated against direct column tags only. Identity/context
        attribute functions in conditions are treated as true for discovery. Lookup and
        parse failures are logged and reported in the note rather than raised.

        Args:
            policy: Policy detail with scope, WHEN, and MATCH COLUMNS.

        Returns:
            De-duplicated assets (capped at the asset limit), a truncated flag, and an
            explanatory note.
        """
        if policy.for_securable_type and policy.for_securable_type not in {"TABLE", "SCHEMA", "CATALOG"}:
            return (
                [],
                False,
                f"Affected-asset discovery for {policy.for_securable_type} is not available yet; "
                "showing principals, tags, and conditions only.",
            )

        when_text = (policy.when_condition or "").strip() or None
        match_texts = [
            condition
            for item in policy.match_columns
            if (condition := (item.condition or "").strip())
        ]
        if not when_text and not match_texts and not policy.governed_tags:
            return (
                [],
                False,
                "This policy has no WHEN or MATCH COLUMNS tag condition, so affected-asset "
                "discovery was skipped.",
            )

        try:
            when_node = parse_tag_condition(when_text) if when_text else None
            match_nodes = [parse_tag_condition(text) for text in match_texts]
        except InvalidRequestError as exc:
            return [], False, f"Could not evaluate WHEN/MATCH condition: {exc}"

        limit = _ASSET_LIMIT + 1
        try:
            if match_nodes:
                rows = self._matching_columns(policy, when_node, match_nodes, limit)
                asset_type = "COLUMN"
            else:
                rows = self._matching_tables(policy, when_node, limit)
                asset_type = "TABLE"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Affected asset lookup failed for %s: %s", policy.id, exc)
            return [], False, f"Could not resolve tagged assets: {exc}"

        assets: list[AffectedAsset] = []
        seen: set[str] = set()
        for row in rows:
            catalog = row.get("catalog_name")
            schema = row.get("schema_name")
            table = row.get("table_name")
            column = row.get("column_name")
            parts = [p for p in (catalog, schema, table, column) if p]
            asset = AffectedAsset(
                asset_type=asset_type,
                full_name=".".join(str(p) for p in parts),
                catalog_name=None if catalog is None else str(catalog),
                schema_name=None if schema is None else str(schema),
                table_name=None if table is None else str(table),
                column_name=None if column is None else str(column),
                matched_tag_key=None if row.get("tag_name") is None else str(row.get("tag_name")),
                matched_tag_value=None if row.get("tag_value") is None else str(row.get("tag_value")),
            )
            key = (
                f"{asset.asset_type}:{asset.full_name}:"
                f"{asset.matched_tag_key}:{asset.matched_tag_value}"
            )
            if key in seen:
                continue
            seen.add(key)
            assets.append(asset)

        truncated = len(assets) > _ASSET_LIMIT
        assets = assets[:_ASSET_LIMIT]
        if match_nodes:
            note = (
                "Columns in scope whose tags satisfy the policy MATCH COLUMNS condition"
                + (" on tables that also satisfy WHEN." if when_node is not None else ".")
            )
        elif when_node is not None:
            note = (
                "Tables in scope whose tags (including inherited catalog/schema tags) "
                "satisfy the policy WHEN condition."
            )
        else:
            note = "Assets in the policy scope."
        if truncated:
            note += (
                f" Showing the first {_ASSET_LIMIT} matches; refine the policy condition "
                "or scope to see the rest."
            )
        return assets, truncated, note

    def _matching_tables(
        self,
        policy: PolicyDetail,
        when_node: object | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return tables in scope that satisfy ``when_node`` (all scoped tables when None)."""
        scope_sql = self._scope_filter_sql(
            policy,
            catalog_col="table_catalog",
            schema_col="table_schema",
            table_col="table_name",
        )
        if when_node is None:
            return self._sql.query(
                f"""
                SELECT
                  table_catalog AS catalog_name,
                  table_schema AS schema_name,
                  table_name,
                  CAST(NULL AS STRING) AS column_name,
                  CAST(NULL AS STRING) AS tag_name,
                  CAST(NULL AS STRING) AS tag_value
                FROM system.information_schema.tables
                WHERE table_schema <> 'information_schema'
                  {scope_sql}
                ORDER BY table_catalog, table_schema, table_name
                LIMIT {limit}
                """
            )

        when_having = having_sql(when_node)  # type: ignore[arg-type]
        keys = referenced_tag_keys(when_node)  # type: ignore[arg-type]
        tag_pick = self._tag_pick_sql(keys)
        return self._sql.query(
            f"""
            WITH scoped_tables AS (
              SELECT table_catalog AS catalog_name, table_schema AS schema_name, table_name
              FROM system.information_schema.tables
              WHERE table_schema <> 'information_schema'
                {scope_sql}
            ),
            effective_tags AS (
              SELECT t.catalog_name, t.schema_name, t.table_name, tg.tag_name, tg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.table_tags tg
                ON tg.catalog_name = t.catalog_name
               AND tg.schema_name = t.schema_name
               AND tg.table_name = t.table_name
              UNION ALL
              SELECT t.catalog_name, t.schema_name, t.table_name, sg.tag_name, sg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.schema_tags sg
                ON sg.catalog_name = t.catalog_name
               AND sg.schema_name = t.schema_name
              UNION ALL
              SELECT t.catalog_name, t.schema_name, t.table_name, cg.tag_name, cg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.catalog_tags cg
                ON cg.catalog_name = t.catalog_name
            )
            SELECT
              catalog_name,
              schema_name,
              table_name,
              CAST(NULL AS STRING) AS column_name,
              {tag_pick}
            FROM effective_tags
            GROUP BY catalog_name, schema_name, table_name
            HAVING {when_having}
            ORDER BY catalog_name, schema_name, table_name
            LIMIT {limit}
            """
        )

    def _matching_columns(
        self,
        policy: PolicyDetail,
        when_node: object | None,
        match_nodes: list[object],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return columns matching every MATCH COLUMNS expression on WHEN-matching tables.

        A table is kept only when each MATCH expression finds at least one column. Returned
        rows are the union of those matching columns.
        """
        per_expr = [self._columns_for_match(policy, when_node, node, limit) for node in match_nodes]
        if not per_expr:
            return []
        if len(per_expr) == 1:
            return per_expr[0]

        def table_key(row: dict[str, Any]) -> tuple[str, str, str]:
            return (
                str(row.get("catalog_name") or ""),
                str(row.get("schema_name") or ""),
                str(row.get("table_name") or ""),
            )

        eligible = {table_key(row) for row in per_expr[0]}
        for rows in per_expr[1:]:
            eligible &= {table_key(row) for row in rows}
        combined: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for rows in per_expr:
            for row in rows:
                if table_key(row) not in eligible:
                    continue
                key = (*table_key(row), str(row.get("column_name") or ""))
                if key in seen:
                    continue
                seen.add(key)
                combined.append(row)
                if len(combined) >= limit:
                    return combined
        return combined

    def _columns_for_match(
        self,
        policy: PolicyDetail,
        when_node: object | None,
        match_node: object,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return columns matching one MATCH COLUMNS expression on WHEN-matching tables."""
        scope_sql = self._scope_filter_sql(policy)
        match_having = having_sql(match_node)  # type: ignore[arg-type]
        keys = referenced_tag_keys(match_node)  # type: ignore[arg-type]
        tag_pick = self._tag_pick_sql(keys)

        if when_node is None:
            return self._sql.query(
                f"""
                SELECT
                  catalog_name,
                  schema_name,
                  table_name,
                  column_name,
                  {tag_pick}
                FROM system.information_schema.column_tags
                WHERE TRUE
                  {scope_sql}
                GROUP BY catalog_name, schema_name, table_name, column_name
                HAVING {match_having}
                ORDER BY catalog_name, schema_name, table_name, column_name
                LIMIT {limit}
                """
            )

        when_having = having_sql(when_node)  # type: ignore[arg-type]
        tables_scope = self._scope_filter_sql(
            policy,
            catalog_col="table_catalog",
            schema_col="table_schema",
            table_col="table_name",
        )
        return self._sql.query(
            f"""
            WITH scoped_tables AS (
              SELECT table_catalog AS catalog_name, table_schema AS schema_name, table_name
              FROM system.information_schema.tables
              WHERE table_schema <> 'information_schema'
                {tables_scope}
            ),
            effective_tags AS (
              SELECT t.catalog_name, t.schema_name, t.table_name, tg.tag_name, tg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.table_tags tg
                ON tg.catalog_name = t.catalog_name
               AND tg.schema_name = t.schema_name
               AND tg.table_name = t.table_name
              UNION ALL
              SELECT t.catalog_name, t.schema_name, t.table_name, sg.tag_name, sg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.schema_tags sg
                ON sg.catalog_name = t.catalog_name
               AND sg.schema_name = t.schema_name
              UNION ALL
              SELECT t.catalog_name, t.schema_name, t.table_name, cg.tag_name, cg.tag_value
              FROM scoped_tables t
              JOIN system.information_schema.catalog_tags cg
                ON cg.catalog_name = t.catalog_name
            ),
            when_tables AS (
              SELECT catalog_name, schema_name, table_name
              FROM effective_tags
              GROUP BY catalog_name, schema_name, table_name
              HAVING {when_having}
            ),
            scoped_column_tags AS (
              SELECT
                ct.catalog_name,
                ct.schema_name,
                ct.table_name,
                ct.column_name,
                ct.tag_name,
                ct.tag_value
              FROM system.information_schema.column_tags ct
              JOIN when_tables wt
                ON wt.catalog_name = ct.catalog_name
               AND wt.schema_name = ct.schema_name
               AND wt.table_name = ct.table_name
              WHERE TRUE
                {scope_sql}
            )
            SELECT
              catalog_name,
              schema_name,
              table_name,
              column_name,
              {tag_pick}
            FROM scoped_column_tags
            GROUP BY catalog_name, schema_name, table_name, column_name
            HAVING {match_having}
            ORDER BY catalog_name, schema_name, table_name, column_name
            LIMIT {limit}
            """
        )

    @staticmethod
    def _tag_pick_sql(keys: list[str]) -> str:
        """SQL selecting a display tag_name/tag_value from keys referenced by the condition."""
        if not keys:
            return "CAST(NULL AS STRING) AS tag_name, CAST(NULL AS STRING) AS tag_value"
        in_list = ", ".join(string_literal(key) for key in keys)
        return (
            f"any_value(tag_name) FILTER (WHERE tag_name IN ({in_list})) AS tag_name, "
            f"any_value(tag_value) FILTER (WHERE tag_name IN ({in_list})) AS tag_value"
        )

    @staticmethod
    def _scope_filter_sql(
        policy: PolicyDetail,
        *,
        catalog_col: str = "catalog_name",
        schema_col: str = "schema_name",
        table_col: str = "table_name",
    ) -> str:
        """Return an ``AND ...`` SQL fragment restricting lookups to the policy's scope."""
        if policy.on_securable_type == "METASTORE":
            return ""
        if policy.on_securable_type == "CATALOG" and policy.catalog_name:
            return f" AND {catalog_col} = {string_literal(policy.catalog_name)}"
        if policy.on_securable_type == "SCHEMA":
            parts = policy.on_securable_fullname.split(".", 1)
            if len(parts) == 2:
                return (
                    f" AND {catalog_col} = {string_literal(parts[0])}"
                    f" AND {schema_col} = {string_literal(parts[1])}"
                )
        if policy.on_securable_type == "TABLE":
            parts = policy.on_securable_fullname.split(".", 2)
            if len(parts) == 3:
                return (
                    f" AND {catalog_col} = {string_literal(parts[0])}"
                    f" AND {schema_col} = {string_literal(parts[1])}"
                    f" AND {table_col} = {string_literal(parts[2])}"
                )
        if policy.catalog_name:
            return f" AND {catalog_col} = {string_literal(policy.catalog_name)}"
        return ""

"""Per-table access insights: column masks, row filters, and attribute suggestions."""

from __future__ import annotations

import logging
from collections import defaultdict

from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, NotFoundError
from backend.app.models.access_explorer import (
    AnalyzeTableRequest,
    ColumnAccessInsight,
    ColumnAccessState,
    ColumnTagMap,
    RowFilterInsight,
    SuggestedAttribute,
    SuggestedAttributeKind,
    TableAccessInsight,
    TableSearchHit,
    TableSearchResult,
    TreeCatalog,
    TreeCatalogList,
    TreeSchema,
    TreeSchemaList,
    TreeTable,
    TreeTableList,
)
from backend.app.models.mask_rule import MaskRuleKind
from backend.app.models.user import CurrentUser
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.mask_rules import MaskRuleService
from backend.app.services.row_filters import RowFilterService, split_table_name

logger = logging.getLogger(__name__)

_GEO_TYPES = frozenset({"geometry", "geography"})
_TREE_LIMIT = 500
_IDENT_MAX = 255


def _require_identifier(value: str, label: str) -> str:
    name = (value or "").strip()
    if not name or len(name) > _IDENT_MAX or any(ch in name for ch in "\x00\n\r"):
        raise InvalidRequestError(f"Invalid {label} name")
    return name
_TAG_KEYS = (
    "sensitivity_category",
    "sensitivity",
    "mask_rule",
    "data_type",
    "row_filter",
)


def has_sensitivity_access(held_paths: set[str], required_path: str) -> bool:
    """Mirror has_sensitivity(): exact match or held path is a prefix of the required path."""
    required = required_path.strip().lower()
    if not required:
        return False
    for held in held_paths:
        value = held.strip().lower()
        if not value:
            continue
        if required == value or required.startswith(f"{value}."):
            return True
    return False


class AccessExplorerService:
    def __init__(
        self,
        config: AppConfig,
        sql: SqlExecutor,
        identity_attributes: IdentityAttributeService,
        mask_rules: MaskRuleService,
        row_filters: RowFilterService,
    ) -> None:
        self._config = config
        self._sql = sql
        self._identity = identity_attributes
        self._mask_rules = mask_rules
        self._row_filters = row_filters

    def search_tables(self, query: str, *, limit: int = 40) -> TableSearchResult:
        q = (query or "").strip()
        if len(q) < 2:
            raise InvalidRequestError("Search query must be at least 2 characters")
        limit = max(1, min(limit, 100))
        # instr() is a literal substring match, so _ and % in names are not wildcards.
        needle = string_literal(q.lower())
        rows = self._sql.query(
            f"""
            SELECT table_catalog, table_schema, table_name
            FROM system.information_schema.tables
            WHERE instr(lower(concat_ws('.', table_catalog, table_schema, table_name)), {needle}) > 0
            ORDER BY table_catalog, table_schema, table_name
            LIMIT {limit}
            """
        )
        tables = [
            TableSearchHit(
                catalog_name=str(row["table_catalog"]),
                schema_name=str(row["table_schema"]),
                table_name=str(row["table_name"]),
            )
            for row in rows
        ]
        return TableSearchResult(tables=tables, total=len(tables))

    # --- tree browser (lazy: one level per request) ---------------------------------

    def list_catalogs(self) -> TreeCatalogList:
        rows = self._sql.query(
            f"""
            SELECT catalog_name
            FROM system.information_schema.catalogs
            ORDER BY catalog_name
            LIMIT {_TREE_LIMIT + 1}
            """
        )
        truncated = len(rows) > _TREE_LIMIT
        items = [TreeCatalog(catalog_name=str(r["catalog_name"])) for r in rows[:_TREE_LIMIT]]
        return TreeCatalogList(catalogs=items, total=len(items), truncated=truncated)

    def list_schemas(self, catalog: str) -> TreeSchemaList:
        name = _require_identifier(catalog, "catalog")
        rows = self._sql.query(
            f"""
            SELECT schema_name
            FROM system.information_schema.schemata
            WHERE catalog_name = {string_literal(name)}
              AND schema_name <> 'information_schema'
            ORDER BY schema_name
            LIMIT {_TREE_LIMIT + 1}
            """
        )
        truncated = len(rows) > _TREE_LIMIT
        items = [
            TreeSchema(catalog_name=name, schema_name=str(r["schema_name"]))
            for r in rows[:_TREE_LIMIT]
        ]
        return TreeSchemaList(schemas=items, total=len(items), truncated=truncated)

    def list_tables(self, catalog: str, schema: str) -> TreeTableList:
        cat = _require_identifier(catalog, "catalog")
        sch = _require_identifier(schema, "schema")
        rows = self._sql.query(
            f"""
            SELECT table_name, table_type
            FROM system.information_schema.tables
            WHERE table_catalog = {string_literal(cat)}
              AND table_schema = {string_literal(sch)}
            ORDER BY table_name
            LIMIT {_TREE_LIMIT + 1}
            """
        )
        truncated = len(rows) > _TREE_LIMIT
        items = [
            TreeTable(
                catalog_name=cat,
                schema_name=sch,
                table_name=str(r["table_name"]),
                table_type=str(r.get("table_type") or ""),
            )
            for r in rows[:_TREE_LIMIT]
        ]
        return TreeTableList(tables=items, total=len(items), truncated=truncated)

    def analyze_table(self, request: AnalyzeTableRequest, user: CurrentUser) -> TableAccessInsight:
        catalog, schema, table = split_table_name(request.table_full_name)
        exists = self._sql.query(
            f"""
            SELECT 1 AS ok
            FROM system.information_schema.tables
            WHERE table_catalog = {string_literal(catalog)}
              AND table_schema = {string_literal(schema)}
              AND table_name = {string_literal(table)}
            LIMIT 1
            """
        )
        if not exists:
            raise NotFoundError(f"Table '{catalog}.{schema}.{table}' was not found")

        columns = self._sql.query(
            f"""
            SELECT column_name, full_data_type
            FROM system.information_schema.columns
            WHERE table_catalog = {string_literal(catalog)}
              AND table_schema = {string_literal(schema)}
              AND table_name = {string_literal(table)}
            ORDER BY ordinal_position
            """
        )
        tag_rows = self._sql.query(
            f"""
            SELECT column_name, tag_name, tag_value
            FROM system.information_schema.column_tags
            WHERE catalog_name = {string_literal(catalog)}
              AND schema_name = {string_literal(schema)}
              AND table_name = {string_literal(table)}
              AND lower(tag_name) IN ({", ".join(string_literal(k) for k in _TAG_KEYS)})
            """
        )
        tags_by_col: dict[str, dict[str, str]] = defaultdict(dict)
        for row in tag_rows:
            name = str(row["column_name"])
            key = str(row["tag_name"]).strip().lower()
            value = None if row.get("tag_value") is None else str(row["tag_value"])
            if key and value is not None:
                tags_by_col[name][key] = value

        held = self._held_attribute_values(user)
        sensitivity_held = held.get("sensitivity", set())
        rules_by_key = {r.rule_key.lower(): r for r in self._mask_rules.list_rules().rules}

        insights: list[ColumnAccessInsight] = []
        for col in columns:
            col_name = str(col["column_name"])
            raw_tags = tags_by_col.get(col_name, {})
            tags = ColumnTagMap(
                sensitivity_category=raw_tags.get("sensitivity_category"),
                sensitivity=raw_tags.get("sensitivity"),
                mask_rule=raw_tags.get("mask_rule"),
                data_type=raw_tags.get("data_type"),
                row_filter=raw_tags.get("row_filter"),
            )
            insights.append(
                self._column_insight(
                    column_name=col_name,
                    sql_type=None if col.get("full_data_type") is None else str(col["full_data_type"]),
                    tags=tags,
                    sensitivity_held=sensitivity_held,
                    rules_by_key=rules_by_key,
                )
            )

        row_filter = self._row_filter_insight(catalog, schema, table, held)
        suggestions = self._suggestions(insights, row_filter)
        masked = sum(
            1
            for c in insights
            if c.access_state in {ColumnAccessState.MASKED, ColumnAccessState.GEO_MASKED}
            and not c.user_has_access
        )
        unmasked = sum(1 for c in insights if c.user_has_access)
        summary_parts = [
            f"{len(insights)} columns",
            f"{unmasked} unmasked for you" if unmasked else "no unmasked governed columns",
            f"{masked} still masked" if masked else "no masked columns pending",
        ]
        if row_filter.attached:
            if row_filter.user_can_see_rows:
                summary_parts.append(f"row filter {row_filter.dimension_key} allows some/all rows")
            else:
                summary_parts.append(f"row filter {row_filter.dimension_key} hides all rows")
        else:
            summary_parts.append("no row filter")

        return TableAccessInsight(
            table_full_name=f"{catalog}.{schema}.{table}",
            catalog_name=catalog,
            schema_name=schema,
            table_name=table,
            columns=insights,
            row_filter=row_filter,
            suggestions=suggestions,
            summary="; ".join(summary_parts),
        )

    def _held_attribute_values(self, user: CurrentUser) -> dict[str, set[str]]:
        held: dict[str, set[str]] = defaultdict(set)
        for item in self._identity.list_for_user(user).assignments:
            held[item.attribute_key.lower()].add(item.attribute_value)
        return held

    def _column_insight(
        self,
        *,
        column_name: str,
        sql_type: str | None,
        tags: ColumnTagMap,
        sensitivity_held: set[str],
        rules_by_key: dict,
    ) -> ColumnAccessInsight:
        category = (tags.sensitivity_category or "").strip()
        sensitivity = (tags.sensitivity or "").strip()
        data_type = (tags.data_type or "").strip().lower()
        has_auth_tags = bool(category and sensitivity)
        required = f"{category}.{sensitivity}" if has_auth_tags else None
        user_has = has_sensitivity_access(sensitivity_held, required) if required else False

        if not has_auth_tags or not data_type:
            return ColumnAccessInsight(
                column_name=column_name,
                data_type=sql_type,
                tags=tags,
                access_state=ColumnAccessState.NOT_GOVERNED,
                required_sensitivity_path=required,
                user_has_access=True if not has_auth_tags else user_has,
                notes=(
                    "Missing sensitivity_category, sensitivity, or data_type tag — "
                    "not matched by the metastore mask policy."
                    if not (has_auth_tags and data_type)
                    else None
                ),
            )

        if data_type in _GEO_TYPES:
            return ColumnAccessInsight(
                column_name=column_name,
                data_type=sql_type,
                tags=tags,
                access_state=ColumnAccessState.GEO_MASKED
                if not user_has
                else ColumnAccessState.UNMASKED,
                required_sensitivity_path=required,
                user_has_access=user_has,
                mask_rule_key=None,
                mask_handler=None,
                mask_kind=None,
                mask_description="Geometry/geography columns return NULL when denied",
                policy_name=(
                    "mask_sensitivity_geometry"
                    if data_type == "geometry"
                    else "mask_sensitivity_geography"
                ),
                notes="Authorized via has_sensitivity; denied values become NULL (geo policy).",
            )

        rule_key = (tags.mask_rule or "full").strip() or "full"
        rule = rules_by_key.get(rule_key.lower())
        if rule is None:
            handler = "full"
            kind = MaskRuleKind.BUILTIN.value
            description = "Unknown mask_rule — falls back to full redact"
        else:
            handler = rule.handler
            kind = rule.kind.value
            description = rule.description

        return ColumnAccessInsight(
            column_name=column_name,
            data_type=sql_type,
            tags=tags,
            access_state=ColumnAccessState.UNMASKED if user_has else ColumnAccessState.MASKED,
            required_sensitivity_path=required,
            user_has_access=user_has,
            mask_rule_key=rule_key,
            mask_handler=handler,
            mask_kind=kind,
            mask_description=description,
            policy_name=self._config.mask_variant_policy_name,
            notes=(
                None
                if user_has
                else f"Denied presentation via apply_mask_rule handler '{handler}'."
            ),
        )

    def _row_filter_insight(
        self, catalog: str, schema: str, table: str, held: dict[str, set[str]]
    ) -> RowFilterInsight:
        policy_name = self._config.row_filter_policy_name
        function_name = (
            f"{self._config.app_catalog}.{self._config.abac_function_schema}.has_row_access"
        )
        full = f"{catalog}.{schema}.{table}".lower()
        try:
            attachments = self._row_filters.list_attachments().attachments
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load row filter attachments: %s", exc)
            return RowFilterInsight(
                attached=False,
                policy_name=policy_name,
                function_name=function_name,
                notes=f"Could not inspect row_filter tags: {exc}",
            )
        match = next((a for a in attachments if a.table_full_name.lower() == full), None)
        if match is None:
            return RowFilterInsight(
                attached=False,
                policy_name=policy_name,
                function_name=function_name,
                notes="No row_filter tag on this table.",
            )
        dim = match.dimension
        col = match.column_name
        values = sorted(held.get(dim.lower(), set()), key=str.lower)
        has_star = any(v == "*" for v in values)
        can_see = bool(values)  # any value (including *) means some rows; empty = none
        return RowFilterInsight(
            attached=True,
            dimension_key=dim,
            column_name=col,
            policy_name=policy_name,
            function_name=function_name,
            user_has_wildcard=has_star,
            user_values=values,
            user_can_see_rows=can_see,
            notes=(
                "You hold * — all rows pass the filter."
                if has_star
                else (
                    f"You hold {', '.join(values)} — only matching rows are visible."
                    if values
                    else f"No '{dim}' attribute — has_row_access denies every row."
                )
            ),
        )

    def _suggestions(
        self, columns: list[ColumnAccessInsight], row_filter: RowFilterInsight
    ) -> list[SuggestedAttribute]:
        by_path: dict[str, SuggestedAttribute] = {}
        for col in columns:
            if col.user_has_access or not col.required_sensitivity_path:
                continue
            if col.access_state == ColumnAccessState.NOT_GOVERNED:
                continue
            path = col.required_sensitivity_path
            existing = by_path.get(path.lower())
            if existing is None:
                by_path[path.lower()] = SuggestedAttribute(
                    kind=SuggestedAttributeKind.SENSITIVITY,
                    attribute_key="sensitivity",
                    attribute_value=path,
                    reason=f"Unmask columns that require sensitivity path {path}",
                    requestable=True,
                    request_path=f"/identity?tab=request&value={path}",
                    column_names=[col.column_name],
                )
            else:
                existing.column_names.append(col.column_name)

        suggestions = list(by_path.values())
        if row_filter.attached and not row_filter.user_can_see_rows and row_filter.dimension_key:
            suggestions.append(
                SuggestedAttribute(
                    kind=SuggestedAttributeKind.ROW_FILTER,
                    attribute_key=row_filter.dimension_key,
                    attribute_value="*",
                    reason=(
                        f"Row filter on column {row_filter.column_name} requires a "
                        f"'{row_filter.dimension_key}' attribute (specific value or *). "
                        "Ask an admin to assign it — row-filter attributes are not "
                        "requestable in the access-request flow yet."
                    ),
                    requestable=False,
                    request_path=None,
                    column_names=[row_filter.column_name] if row_filter.column_name else [],
                )
            )
        return suggestions

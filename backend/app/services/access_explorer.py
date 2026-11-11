"""Per-table access insights: column masks, row filters, and attribute suggestions.

Lets a signed-in user browse Unity Catalog (search or a lazy catalog/schema/table tree) and
explain, for one table, which columns the metastore ABAC mask policies will mask for them,
whether the app-managed row filter hides rows, and which identity attributes they could
request to gain access. The analysis is read-only: it mirrors the ``has_sensitivity`` and
``has_row_access`` UDF semantics in Python over information_schema metadata and the caller's
app-managed attribute assignments, and previews who would review each suggested request.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, NotFoundError
from backend.app.models.access_explorer import (
    AnalyzeTableRequest,
    ApproverPreview,
    ColumnAccessInsight,
    ColumnAccessState,
    ColumnTagMap,
    RequestRouteKind,
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
from backend.app.models.access_request import SENSITIVITY_ATTRIBUTE_KEY
from backend.app.models.mask_rule import MaskRuleKind
from backend.app.models.user import CurrentUser
from backend.app.services.access_requests import AccessRequestService, path_prefixes
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.mask_rules import MaskRuleService
from backend.app.services.row_filters import RowFilterService, split_table_name

logger = logging.getLogger(__name__)

_GEO_TYPES = frozenset({"geometry", "geography"})
_TREE_LIMIT = 500
_IDENT_MAX = 255


def _require_identifier(value: str, label: str) -> str:
    """Validate a raw catalog/schema name used as a SQL string literal filter.

    Args:
        value: Name supplied by the caller.
        label: Kind of securable, used in the error message.

    Returns:
        The stripped name.

    Raises:
        InvalidRequestError: If the name is empty, too long, or contains control characters.
    """
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
    """Mirror has_sensitivity(): exact match or held path is a prefix of the required path.

    Comparison is case-insensitive and prefix matching respects dotted segments, so holding
    ``pii`` grants ``pii.high`` but holding ``pi`` does not.

    Args:
        held_paths: Sensitivity paths the user holds (e.g. ``pii`` or ``pii.high``).
        required_path: The column's ``sensitivity_category.sensitivity`` path.

    Returns:
        True if any held path grants the required path; False for an empty required path.
    """
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
    """Read-only explorer that explains a user's effective access to Unity Catalog tables.

    Args:
        config: App configuration (policy names, ABAC function schema).
        sql: SQL executor used for information_schema lookups.
        identity_attributes: Source of the caller's attribute assignments and definitions.
        mask_rules: Mask rule registry used to describe denied-column presentation.
        row_filters: Row-filter service used as a fallback to find table attachments.
        access_requests: Optional request service used to preview approver routing; when
            omitted, every suggestion is shown as routed to app admins.
    """

    def __init__(
        self,
        config: AppConfig,
        sql: SqlExecutor,
        identity_attributes: IdentityAttributeService,
        mask_rules: MaskRuleService,
        row_filters: RowFilterService,
        access_requests: AccessRequestService | None = None,
    ) -> None:
        self._config = config
        self._sql = sql
        self._identity = identity_attributes
        self._mask_rules = mask_rules
        self._row_filters = row_filters
        self._access_requests = access_requests

    def search_tables(self, query: str, *, limit: int = 40) -> TableSearchResult:
        """Find tables whose ``catalog.schema.table`` name contains a substring.

        Matching is a case-insensitive literal substring (``_`` and ``%`` are not wildcards)
        over tables visible to the SQL executor's identity.

        Args:
            query: Search text; must be at least 2 characters after trimming.
            limit: Maximum hits to return, clamped to 1-100.

        Returns:
            Matching tables ordered by full name.

        Raises:
            InvalidRequestError: If the query is shorter than 2 characters.
        """
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
        """List catalogs for the top level of the tree browser.

        Returns:
            Up to the tree limit of catalogs, with ``truncated`` set when more exist.
        """
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
        """List schemas in a catalog for the tree browser, excluding ``information_schema``.

        Args:
            catalog: Catalog name.

        Returns:
            Up to the tree limit of schemas, with ``truncated`` set when more exist.

        Raises:
            InvalidRequestError: If the catalog name is invalid.
        """
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
        """List tables and views in a schema for the tree browser.

        Args:
            catalog: Catalog name.
            schema: Schema name.

        Returns:
            Up to the tree limit of tables, with ``truncated`` set when more exist.

        Raises:
            InvalidRequestError: If the catalog or schema name is invalid.
        """
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
        """Explain how column masks and the row filter apply to ``user`` on one table.

        Reads the table's columns and governance tags (``sensitivity_category``,
        ``sensitivity``, ``mask_rule``, ``data_type``, ``row_filter``), evaluates them against
        the user's active attribute assignments (direct and via groups), and builds
        request suggestions that would unmask columns or reveal filtered rows. This is an
        approximation of policy evaluation from app metadata, not a query as the user.

        Args:
            request: Holds the three-part table name to analyze.
            user: The signed-in user whose attributes are evaluated.

        Returns:
            Per-column insights, row-filter insight, suggested attribute requests, and a
            one-line summary.

        Raises:
            InvalidRequestError: If the table name is not a valid three-part name.
            NotFoundError: If the table does not exist or is not visible.
        """
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

        row_filter = self._row_filter_insight(catalog, schema, table, held, tags_by_col)
        suggestions = self._suggestions(insights, row_filter, sensitivity_held, held)
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
        """Group the user's active attribute values by lower-cased attribute key."""
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
        """Classify one column against the metastore mask policies.

        A column is governed only when it carries ``sensitivity_category``, ``sensitivity``,
        and ``data_type`` tags. Geometry/geography columns fall under the geo policies (denied
        values become NULL); all other governed columns use the variant mask policy, whose
        denied presentation comes from the ``mask_rule`` tag (unknown or missing rules fall
        back to ``full``).

        Args:
            column_name: Column name.
            sql_type: Full SQL data type from information_schema, if known.
            tags: Governance tags on the column.
            sensitivity_held: Sensitivity paths the user holds.
            rules_by_key: Mask rule definitions keyed by lower-cased rule key.

        Returns:
            The column's access state, required sensitivity path, and mask details.
        """
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
        self,
        catalog: str,
        schema: str,
        table: str,
        held: dict[str, set[str]],
        tags_by_col: dict[str, dict[str, str]],
    ) -> RowFilterInsight:
        """Describe whether the app row filter applies to the table and what the user sees.

        The dimension is taken from a ``row_filter`` column tag on the table, falling back to
        the row-filter service's attachment index. Mirrors ``has_row_access``: holding ``*``
        passes every row, holding specific values passes matching rows, and holding nothing
        for the dimension denies every row.

        Args:
            catalog: Catalog name.
            schema: Schema name.
            table: Table name.
            held: The user's held attribute values keyed by lower-cased attribute key.
            tags_by_col: Governance tags already loaded for the table's columns.

        Returns:
            Row-filter insight; ``attached`` is False when no dimension applies or the
            attachment lookup failed.
        """
        policy_name = self._config.row_filter_policy_name
        function_name = (
            f"{self._config.app_catalog}.{self._config.abac_function_schema}.has_row_access"
        )
        # Prefer the column tags already loaded for this table (works even if the
        # dimension is not yet registered in the app pack).
        tag_match: tuple[str, str] | None = None
        for column_name, tags in tags_by_col.items():
            dim = (tags.get("row_filter") or "").strip()
            if dim:
                tag_match = (column_name, dim)
                break
        if tag_match is None:
            # Fall back to the pack attachment index.
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
            col, dim = match.column_name, match.dimension
        else:
            col, dim = tag_match

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
        self,
        columns: list[ColumnAccessInsight],
        row_filter: RowFilterInsight,
        sensitivity_held: set[str],
        held: dict[str, set[str]],
    ) -> list[SuggestedAttribute]:
        """Build attribute requests that would widen the user's access to the table.

        Produces one sensitivity suggestion per distinct missing leaf path, then broader
        hierarchy parents that cover two or more missing leaves (skipped if already held or
        not in the sensitivity vocabulary). For a row-filtered table without ``*``, also
        suggests ``*`` and each concrete dimension value the user lacks; row-filter requests
        always route to app admins and are only requestable once the dimension's attribute
        definition exists.

        Args:
            columns: Column insights for the table.
            row_filter: Row-filter insight for the table.
            sensitivity_held: Sensitivity paths the user holds.
            held: All held attribute values keyed by lower-cased attribute key.

        Returns:
            Suggestions ordered leaf paths first, then parents, then row-filter values.
        """
        path_to_cols: dict[str, list[str]] = defaultdict(list)
        path_canonical: dict[str, str] = {}
        for col in columns:
            if col.user_has_access or not col.required_sensitivity_path:
                continue
            if col.access_state == ColumnAccessState.NOT_GOVERNED:
                continue
            path = col.required_sensitivity_path
            key = path.lower()
            path_canonical[key] = path
            path_to_cols[key].append(col.column_name)

        allowed = {v.lower(): v for v in self._sensitivity_allowed_values()}
        suggestions: list[SuggestedAttribute] = []

        # One suggestion per distinct required leaf path (deduped across columns).
        for key, cols in sorted(path_to_cols.items(), key=lambda item: item[0]):
            path = path_canonical[key]
            suggestions.append(
                self._sensitivity_suggestion(
                    path=path,
                    column_names=cols,
                    covers_paths=[path],
                    is_parent=False,
                    reason=(
                        f"Unmask {len(cols)} column(s) that require sensitivity path {path}"
                    ),
                    requestable=key in allowed or not allowed,
                )
            )

        # Broader hierarchy parents that cover 2+ missing leaves (and user does not already hold).
        for parent, covered in self._hierarchy_parents(list(path_canonical.values())):
            if has_sensitivity_access(sensitivity_held, parent):
                continue
            parent_key = parent.lower()
            if parent_key in path_to_cols:
                continue
            if allowed and parent_key not in allowed:
                continue
            covered_cols: list[str] = []
            for leaf in covered:
                covered_cols.extend(path_to_cols.get(leaf.lower(), []))
            # Preserve order, unique
            seen_cols: set[str] = set()
            unique_cols: list[str] = []
            for name in covered_cols:
                if name not in seen_cols:
                    seen_cols.add(name)
                    unique_cols.append(name)
            suggestions.append(
                self._sensitivity_suggestion(
                    path=allowed.get(parent_key, parent),
                    column_names=unique_cols,
                    covers_paths=covered,
                    is_parent=True,
                    reason=(
                        f"Broader path covering {len(covered)} required tags "
                        f"({', '.join(covered)}) across {len(unique_cols)} column(s)"
                    ),
                    requestable=True,
                )
            )

        if (
            row_filter.attached
            and row_filter.dimension_key
            and not row_filter.user_has_wildcard
        ):
            dim = row_filter.dimension_key
            held_dim = held.get(dim.lower(), set())
            dim_def = None
            allowed: list[str] = []
            try:
                dim_def = self._identity.get_definition(dim)
                allowed = list(dim_def.allowed_values)
            except Exception:  # noqa: BLE001 - missing definition is a valid explorer state
                dim_def = None
            # Always offer * (full row access) when the user does not hold it.
            star_allowed = not allowed or any(v == "*" for v in allowed)
            if "*" not in {v.lower() for v in held_dim} and star_allowed:
                suggestions.append(
                    self._row_filter_suggestion(
                        dim=dim,
                        value="*",
                        column_name=row_filter.column_name,
                        requestable=dim_def is not None,
                        reason=(
                            (
                                f"Row filter dimension '{dim}' is tagged on this table but has no "
                                "identity attribute definition yet — an admin must create it "
                                "(Row filters page) before requests can be submitted."
                            )
                            if dim_def is None
                            else (
                                f"Row filter on column {row_filter.column_name} uses dimension "
                                f"'{dim}'. Request * to see all rows"
                            )
                        ),
                    )
                )
            # Also offer concrete dimension values the user does not already hold.
            for value in allowed:
                if value == "*" or value.lower() in {v.lower() for v in held_dim}:
                    continue
                suggestions.append(
                    self._row_filter_suggestion(
                        dim=dim,
                        value=value,
                        column_name=row_filter.column_name,
                        requestable=True,
                        reason=(
                            f"Row filter on column {row_filter.column_name}: request "
                            f"{dim}={value} to see matching rows"
                        ),
                    )
                )
        return suggestions

    def _row_filter_suggestion(
        self,
        *,
        dim: str,
        value: str,
        column_name: str | None,
        reason: str,
        requestable: bool,
    ) -> SuggestedAttribute:
        """Build a row-filter suggestion annotated with its approval routing preview.

        Designated dimension approvers win when configured; otherwise app admins review.
        """
        routing = (
            self._access_requests.resolve_attribute_routing(dim, value)
            if self._access_requests is not None
            else {
                "resolved_category": None,
                "routed_to": RequestRouteKind.APP_ADMINS,
                "approvers": [],
            }
        )
        routed = routing["routed_to"]
        route_kind = routed if isinstance(routed, RequestRouteKind) else RequestRouteKind(str(routed))
        approvers = [
            ApproverPreview(
                principal_name=a.principal_name,
                principal_type=a.principal_type.value
                if hasattr(a.principal_type, "value")
                else str(a.principal_type),
            )
            for a in routing["approvers"]
        ]
        route_note = (
            "App admins will review (no dimension approvers configured)."
            if route_kind == RequestRouteKind.APP_ADMINS
            else (
                f"Routed to {dim} approvers: "
                + ", ".join(f"{a.principal_name} ({a.principal_type})" for a in approvers)
            )
        )
        return SuggestedAttribute(
            kind=SuggestedAttributeKind.ROW_FILTER,
            attribute_key=dim,
            attribute_value=value,
            reason=f"{reason}. {route_note}",
            requestable=requestable,
            request_path=(
                f"/identity?tab=request&key={dim}&value={value}" if requestable else None
            ),
            column_names=[column_name] if column_name else [],
            covers_paths=[value],
            is_hierarchy_parent=False,
            routed_to=route_kind,
            resolved_category=routing["resolved_category"],
            approvers=approvers,
        )

    def _sensitivity_suggestion(
        self,
        *,
        path: str,
        column_names: list[str],
        covers_paths: list[str],
        is_parent: bool,
        reason: str,
        requestable: bool,
    ) -> SuggestedAttribute:
        """Build a sensitivity suggestion annotated with its approval routing preview.

        Routing goes to the approvers of the most-specific configured category covering
        ``path``, else to app admins (always app admins when no request service is wired).

        Args:
            path: Sensitivity path to request.
            column_names: Columns the grant would unmask.
            covers_paths: Required leaf paths the grant satisfies.
            is_parent: Whether ``path`` is a broader hierarchy parent of the leaves.
            reason: Human-readable reason; the routing note is appended.
            requestable: Whether the UI should allow submitting the request.

        Returns:
            The suggestion with routing kind, resolved category, and approver preview.
        """
        routing = (
            self._access_requests.resolve_routing(path)
            if self._access_requests is not None
            else {
                "resolved_category": None,
                "routed_to": RequestRouteKind.APP_ADMINS,
                "approvers": [],
            }
        )
        routed = routing["routed_to"]
        route_kind = routed if isinstance(routed, RequestRouteKind) else RequestRouteKind(str(routed))
        approvers = [
            ApproverPreview(
                principal_name=a.principal_name,
                principal_type=a.principal_type.value
                if hasattr(a.principal_type, "value")
                else str(a.principal_type),
            )
            for a in routing["approvers"]
        ]
        route_note = (
            "App admins will review (no category approvers configured)."
            if route_kind == RequestRouteKind.APP_ADMINS
            else (
                f"Routed to {routing['resolved_category']} approvers: "
                + ", ".join(f"{a.principal_name} ({a.principal_type})" for a in approvers)
            )
        )
        return SuggestedAttribute(
            kind=SuggestedAttributeKind.SENSITIVITY,
            attribute_key=SENSITIVITY_ATTRIBUTE_KEY,
            attribute_value=path,
            reason=f"{reason}. {route_note}",
            requestable=requestable,
            request_path=f"/identity?tab=request&value={path}",
            column_names=column_names,
            covers_paths=covers_paths,
            is_hierarchy_parent=is_parent,
            routed_to=route_kind,
            resolved_category=routing["resolved_category"],
            approvers=approvers,
        )

    def _sensitivity_allowed_values(self) -> list[str]:
        """Return the sensitivity attribute vocabulary, or an empty list if unavailable."""
        try:
            definition = self._identity.get_definition(SENSITIVITY_ATTRIBUTE_KEY)
        except Exception:  # noqa: BLE001
            return []
        return list(definition.allowed_values)

    @staticmethod
    def _hierarchy_parents(paths: list[str]) -> list[tuple[str, list[str]]]:
        """Return (parent_path, covered_leaf_paths) for parents that cover 2+ leaves.

        Most-specific parents first.

        Args:
            paths: Distinct required leaf sensitivity paths.

        Returns:
            Parent paths with the sorted leaves they cover, deepest parents first and then
            alphabetical.
        """
        if len(paths) < 2:
            return []
        coverage: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            for prefix in path_prefixes(path)[1:]:  # skip the leaf itself
                coverage[prefix].add(path)
        ranked = sorted(
            (
                (parent, sorted(covered))
                for parent, covered in coverage.items()
                if len(covered) >= 2
            ),
            key=lambda item: (-len(item[0].split(".")), item[0].lower()),
        )
        return ranked

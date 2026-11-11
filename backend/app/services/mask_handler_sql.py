"""Static checks and SQL generation for apply_mask_rule presentation handlers.

When a caller lacks the sensitivity grant for a column, the metastore variant mask policy
delegates presentation to ``apply_mask_rule``, which dispatches on the column's ``mask_rule``
tag. This module allow-lists admin-authored custom expressions (a single scalar expression
over ``v`` using a fixed function set) and generates the ``CREATE OR REPLACE FUNCTION`` DDL
for ``apply_mask_rule`` and its ``_resolve_mask_handler`` helper from the rule registry.
"""

from __future__ import annotations

import json
import re

from backend.app.core.errors import InvalidRequestError
from backend.app.models.mask_rule import BUILTIN_HANDLERS, MaskRuleDefinition, MaskRuleKind

_ALLOWED_IDENTS = frozenset(
    {
        "v",
        "true",
        "false",
        "null",
        "and",
        "or",
        "not",
        "case",
        "when",
        "then",
        "else",
        "end",
        "as",
        "in",
        "between",
        "like",
        "rlike",
        "is",
        "cast",
        "string",
        "int",
        "bigint",
        "double",
        "boolean",
        "variant",
    }
)

_ALLOWED_FUNCS = frozenset(
    {
        "concat",
        "concat_ws",
        "sha2",
        "md5",
        "crc32",
        "hash",
        "lower",
        "upper",
        "trim",
        "ltrim",
        "rtrim",
        "regexp_replace",
        "regexp_extract",
        "split_part",
        "split",
        "right",
        "left",
        "length",
        "len",
        "repeat",
        "instr",
        "substring",
        "substr",
        "replace",
        "coalesce",
        "nullif",
        "nvl",
        "greatest",
        "least",
        "if",
        "iff",
        "char_length",
        "character_length",
        "ascii",
        "chr",
        "base64",
        "unbase64",
        "hex",
        "unhex",
        "abs",
        "mod",
        "floor",
        "ceil",
        "round",
        "cast",
        "try_cast",
        "get_json_object",
        "to_json",
        "from_json",
        "parse_url",
        "url_encode",
        "url_decode",
        "translate",
        "overlay",
        "position",
        "locate",
        "format_string",
        "printf",
        "lpad",
        "rpad",
        "initcap",
        "reverse",
        "space",
        "mask",
    }
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:''|[^'])*'")


def sanitize_expression(expression: str) -> str:
    """Return a cleaned single SQL expression or raise InvalidRequestError.

    Strips comments and a trailing semicolon, rejects multiple statements and DML/DDL
    keywords, and allows only the column identifier ``v``, a small keyword/type set, and
    allow-listed functions (string literals are ignored for these checks).

    Args:
        expression: Admin-authored mask expression.

    Returns:
        The cleaned expression text.

    Raises:
        InvalidRequestError: If the expression is empty, has multiple statements, contains a
            banned keyword, or uses a non-allow-listed function or identifier.
    """
    text = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", expression or "")).strip()
    text = text.rstrip(";").strip()
    if not text:
        raise InvalidRequestError("Expression is empty")
    if ";" in text:
        raise InvalidRequestError("Expression must be a single statement")
    stripped = _STRING.sub("''", text)
    lowered = stripped.lower()
    for banned in (
        " drop ",
        " delete ",
        " insert ",
        " update ",
        " merge ",
        " create ",
        " alter ",
        " grant ",
        " revoke ",
        " truncate ",
        " copy ",
        " call ",
    ):
        if banned in f" {lowered} ":
            raise InvalidRequestError(f"Expression must not contain '{banned.strip()}'")
    for match in _IDENT.finditer(stripped):
        name = match.group(0).lower()
        after = stripped[match.end() :].lstrip()
        if after.startswith("("):
            if name not in _ALLOWED_FUNCS and name not in _ALLOWED_IDENTS:
                raise InvalidRequestError(f"Function '{name}' is not allowed in mask expressions")
        elif name not in _ALLOWED_IDENTS and name not in _ALLOWED_FUNCS:
            raise InvalidRequestError(
                f"Identifier '{name}' is not allowed; use 'v' for the column value"
            )
    return text


def substitute_v(expression: str, replacement: str) -> str:
    """Replace standalone identifier v with replacement (not inside strings).

    Args:
        expression: Sanitized mask expression.
        replacement: SQL to substitute for ``v`` (e.g. a cast of the column value or a
            sample literal).

    Returns:
        The expression with every whole-word ``v`` outside string literals replaced.
    """
    parts: list[str] = []
    last = 0
    for match in _STRING.finditer(expression):
        parts.append(_replace_idents(expression[last : match.start()], replacement))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_replace_idents(expression[last:], replacement))
    return "".join(parts)


def _replace_idents(chunk: str, replacement: str) -> str:
    """Replace whole-word ``v`` in a chunk that contains no string literals."""
    return re.sub(r"\bv\b", replacement, chunk)


def builtin_branch_sql(handler: str, *, params_json_expr: str, salt_expr: str) -> str:
    """Return the VARIANT-valued SQL for a built-in handler's denied presentation.

    ``joinable`` is a salted SHA-256 of the normalized value (stable for joins), ``email``
    keeps the domain, ``phone`` keeps the last four digits, ``keep_last_n`` keeps the last
    ``n`` characters (default 4), and anything else (including ``full``) is ``'***'``.

    Args:
        handler: Built-in handler name.
        params_json_expr: SQL expression yielding the rule's params JSON string.
        salt_expr: SQL expression yielding the joinable salt.

    Returns:
        A SQL expression over ``value`` for use in a CASE branch.
    """
    cast_v = "cast(value AS STRING)"
    if handler == "joinable":
        return f"sha2(concat({salt_expr}, lower(trim({cast_v}))), 256)::VARIANT"
    if handler == "email":
        return f"""CASE
      WHEN instr({cast_v}, '@') > 1 AND instr({cast_v}, '@') < length({cast_v})
      THEN concat('***@', split_part({cast_v}, '@', 2))::VARIANT
      ELSE '***'::VARIANT
    END"""
    if handler == "phone":
        return f"""CASE
      WHEN length(regexp_replace({cast_v}, '[^0-9]', '')) >= 4 THEN
        concat('***-***-', right(regexp_replace({cast_v}, '[^0-9]', ''), 4))::VARIANT
      ELSE '***'::VARIANT
    END"""
    if handler == "keep_last_n":
        n = f"cast(coalesce(get_json_object({params_json_expr}, '$.n'), '4') AS INT)"
        return f"""CASE
      WHEN length({cast_v}) <= {n} THEN '***'::VARIANT
      ELSE concat(
        repeat('*', greatest(length({cast_v}) - {n}, 1)),
        right({cast_v}, {n})
      )::VARIANT
    END"""
    return "'***'::VARIANT"


def build_resolve_handler_sql(*, function_fqn: str, definitions_table: str) -> str:
    """Return DDL for the UDF that maps a ``mask_rule`` tag value to a handler key.

    The generated function looks the rule up in the UC mirror: custom rules resolve to
    their rule key, built-ins to their handler; blank or unknown rules resolve to ``full``.

    Args:
        function_fqn: Fully qualified, quoted name of the resolver function.
        definitions_table: Fully qualified, quoted UC mirror of the mask rule registry.

    Returns:
        A ``CREATE OR REPLACE FUNCTION`` statement.
    """
    return f"""CREATE OR REPLACE FUNCTION {function_fqn}(mask_rule STRING)
RETURNS STRING
LANGUAGE SQL
READS SQL DATA
COMMENT 'Resolve mask_rule tag value to a handler CASE key (custom keys use rule_key)'
RETURN lower(coalesce(
  (
    SELECT CASE
      WHEN lower(coalesce(kind, 'builtin')) = 'custom' THEN lower(rule_key)
      ELSE lower(handler)
    END
    FROM {definitions_table}
    WHERE lower(rule_key) = lower(coalesce(nullif(trim(mask_rule), ''), 'full'))
    LIMIT 1
  ),
  'full'
))"""


def build_apply_mask_rule_sql(
    *,
    function_fqn: str,
    resolve_function_fqn: str,
    definitions_table: str,
    config_table: str,
    rules: list[MaskRuleDefinition],
) -> str:
    """Emit CREATE OR REPLACE FUNCTION for apply_mask_rule from the current registry.

    Builds one CASE branch per distinct handler: custom rules inline their sanitized
    expression, built-in rules use their params, and any built-in not covered by the
    registry gets a default branch. NULL and non-string VARIANT values return NULL;
    unmatched handlers fall back to ``'***'``.

    Args:
        function_fqn: Fully qualified, quoted name of ``apply_mask_rule``.
        resolve_function_fqn: Fully qualified, quoted name of the handler resolver UDF.
        definitions_table: Fully qualified, quoted UC mirror of the mask rule registry.
        config_table: Fully qualified, quoted UC masking config table (joinable salt).
        rules: Current mask rule definitions.

    Returns:
        A ``CREATE OR REPLACE FUNCTION`` statement.

    Raises:
        InvalidRequestError: If a stored custom expression fails sanitization.
    """
    salt_expr = f"""coalesce(
      (SELECT config_value FROM {config_table} WHERE config_key = 'joinable_salt' LIMIT 1),
      'unset'
    )"""
    resolved = f"{resolve_function_fqn}(mask_rule)"

    branches: list[str] = []
    seen_handlers: set[str] = set()

    for rule in rules:
        if rule.kind == MaskRuleKind.CUSTOM:
            key = rule.handler or rule.rule_key
            if key in seen_handlers:
                continue
            seen_handlers.add(key)
            expr = sanitize_expression(rule.expression or "")
            body = substitute_v(expr, "cast(value AS STRING)")
            branches.append(f"WHEN {resolved} = {_sql_str(key)} THEN ({body})::VARIANT")
        elif rule.handler in BUILTIN_HANDLERS and rule.handler not in seen_handlers:
            seen_handlers.add(rule.handler)
            params_lit = _sql_str(json.dumps(rule.params or {}))
            body = builtin_branch_sql(
                rule.handler, params_json_expr=params_lit, salt_expr=salt_expr
            )
            branches.append(f"WHEN {resolved} = {_sql_str(rule.handler)} THEN {body}")

    for builtin in ("full", "joinable", "email", "phone", "keep_last_n"):
        if builtin in seen_handlers:
            continue
        seen_handlers.add(builtin)
        params_lit = "'{\"n\":4}'" if builtin == "keep_last_n" else "'{}'"
        body = builtin_branch_sql(builtin, params_json_expr=params_lit, salt_expr=salt_expr)
        branches.append(f"WHEN {resolved} = {_sql_str(builtin)} THEN {body}")

    case_body = "\n  ".join(branches)
    return f"""CREATE OR REPLACE FUNCTION {function_fqn}(
  value VARIANT,
  mask_rule STRING
)
RETURNS VARIANT
LANGUAGE SQL
READS SQL DATA
COMMENT 'Apply presentation mask_rule handler (builtin or custom expression) to a denied column value'
RETURN CASE
  WHEN value IS NULL THEN NULL::VARIANT
  WHEN schema_of_variant(value) <> 'STRING' THEN NULL::VARIANT
  {case_body}
  ELSE '***'::VARIANT
END"""


def expected_custom_keys(rules: list[MaskRuleDefinition]) -> list[str]:
    """Return the handler keys of custom rules that should appear in ``apply_mask_rule``.

    Used to detect a deployed UDF that is out of sync with the registry.

    Args:
        rules: Current mask rule definitions.

    Returns:
        Handler (or rule) keys of custom rules with a non-empty expression.
    """
    return [
        (r.handler or r.rule_key)
        for r in rules
        if r.kind == MaskRuleKind.CUSTOM and (r.expression or "").strip()
    ]


def _sql_str(value: str) -> str:
    """Quote a value as a SQL string literal, escaping backslashes and single quotes."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

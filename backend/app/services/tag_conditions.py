"""Parse and translate ABAC tag conditions (``WHEN`` / ``MATCH COLUMNS``).

Unity Catalog evaluates ``has_tag`` / ``has_tag_value`` (and camelCase aliases) in policy
conditions. This module turns those boolean expressions into SQL ``HAVING`` predicates over
``information_schema`` tag rows so affected-asset discovery can match the real policy target,
not merely “any governed tag the policy mentions”.

Identity and context attribute functions gate *who* a policy applies to at query time; for
asset discovery they are treated as ``TRUE`` so the tag portion still selects the right
tables and columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.app.clients.sql import string_literal
from backend.app.core.errors import InvalidRequestError

_AS_ALIAS = re.compile(r"\s+AS\s+([A-Za-z_][\w]*)\s*$", re.IGNORECASE)
_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<and>\bAND\b)
        |(?P<or>\bOR\b)
        |(?P<not>\bNOT\b)
        |(?P<true>\bTRUE\b)
        |(?P<false>\bFALSE\b)
        |(?P<lparen>\()
        |(?P<rparen>\))
        |(?P<has_tag_value>\bhas_tag_value\b|\bhasTagValue\b)
        |(?P<has_tag>\bhas_tag\b|\bhasTag\b)
        |(?P<ignored_fn>\bhas_identity_attribute_value\b
            |\bhas_identity_attribute_tag_match\b
            |\bhas_context_attribute_value\b
            |\bhas_context_attribute\b)
        |(?P<string>'(?:''|[^'])*')
        |(?P<comma>,)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _True:
    pass


@dataclass(frozen=True, slots=True)
class _False:
    pass


@dataclass(frozen=True, slots=True)
class _HasTag:
    key: str


@dataclass(frozen=True, slots=True)
class _HasTagValue:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class _Not:
    inner: object


@dataclass(frozen=True, slots=True)
class _And:
    left: object
    right: object


@dataclass(frozen=True, slots=True)
class _Or:
    left: object
    right: object


_Node = _True | _False | _HasTag | _HasTagValue | _Not | _And | _Or


def strip_match_alias(expression: str) -> tuple[str, str | None]:
    """Remove a trailing ``AS alias`` from a MATCH COLUMNS expression.

    Args:
        expression: Raw match-column expression, possibly including ``AS c``.

    Returns:
        The condition text and the alias when present.
    """
    text = expression.strip()
    match = _AS_ALIAS.search(text)
    if not match:
        return text, None
    return text[: match.start()].strip(), match.group(1)


def parse_tag_condition(expression: str) -> _Node:
    """Parse a ``WHEN`` / ``MATCH COLUMNS`` tag condition into an AST.

    Args:
        expression: Boolean expression using ``has_tag`` / ``has_tag_value`` (and aliases).

    Returns:
        Root AST node.

    Raises:
        InvalidRequestError: If the expression is empty or uses unsupported syntax.
    """
    text = expression.strip()
    if not text:
        raise InvalidRequestError("Tag condition is empty")
    tokens = list(_TOKEN.finditer(text))
    consumed = 0
    for match in tokens:
        consumed = match.end()
    if text[consumed:].strip():
        raise InvalidRequestError(
            f"Unsupported tag condition syntax near: {text[consumed:].strip()[:40]!r}"
        )
    parser = _Parser([match for match in tokens])
    node = parser.parse_or()
    if parser.pos != len(parser.tokens):
        raise InvalidRequestError("Unexpected trailing tokens in tag condition")
    return node


def having_sql(node: _Node) -> str:
    """Translate a tag-condition AST into a ``HAVING`` predicate over tag rows.

    Each ``has_tag`` / ``has_tag_value`` becomes ``bool_or(...)`` so the predicate is meant
    for ``GROUP BY`` catalog/schema/table[/column] over ``information_schema`` tag tables.

    Args:
        node: AST from :func:`parse_tag_condition`.

    Returns:
        SQL boolean expression safe for interpolation (literals are escaped).
    """
    if isinstance(node, _True):
        return "TRUE"
    if isinstance(node, _False):
        return "FALSE"
    if isinstance(node, _HasTag):
        return f"bool_or(tag_name = {string_literal(node.key)})"
    if isinstance(node, _HasTagValue):
        return (
            f"bool_or(tag_name = {string_literal(node.key)} "
            f"AND tag_value = {string_literal(node.value)})"
        )
    if isinstance(node, _Not):
        return f"NOT ({having_sql(node.inner)})"  # type: ignore[arg-type]
    if isinstance(node, _And):
        return f"({having_sql(node.left)} AND {having_sql(node.right)})"  # type: ignore[arg-type]
    if isinstance(node, _Or):
        return f"({having_sql(node.left)} OR {having_sql(node.right)})"  # type: ignore[arg-type]
    raise InvalidRequestError(f"Unknown tag condition node: {type(node)!r}")


def referenced_tag_keys(node: _Node) -> list[str]:
    """Return tag keys referenced by ``node``, preferring ``has_tag_value`` keys first."""
    value_keys: list[str] = []
    key_only: list[str] = []

    def walk(current: object) -> None:
        if isinstance(current, _HasTagValue):
            if current.key not in value_keys:
                value_keys.append(current.key)
        elif isinstance(current, _HasTag):
            if current.key not in key_only and current.key not in value_keys:
                key_only.append(current.key)
        elif isinstance(current, _Not):
            walk(current.inner)
        elif isinstance(current, (_And, _Or)):
            walk(current.left)
            walk(current.right)

    walk(node)
    return value_keys + key_only


class _Parser:
    """Recursive-descent parser for ABAC tag conditions."""

    def __init__(self, tokens: list[re.Match[str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def parse_or(self) -> _Node:
        node = self.parse_and()
        while self._matches("or"):
            self.pos += 1
            node = _Or(node, self.parse_and())
        return node

    def parse_and(self) -> _Node:
        node = self.parse_not()
        while self._matches("and"):
            self.pos += 1
            node = _And(node, self.parse_not())
        return node

    def parse_not(self) -> _Node:
        if self._matches("not"):
            self.pos += 1
            return _Not(self.parse_not())
        return self.parse_primary()

    def parse_primary(self) -> _Node:
        if self._matches("true"):
            self.pos += 1
            return _True()
        if self._matches("false"):
            self.pos += 1
            return _False()
        if self._matches("lparen"):
            self.pos += 1
            node = self.parse_or()
            if not self._matches("rparen"):
                raise InvalidRequestError("Expected ')' in tag condition")
            self.pos += 1
            return node
        if self._matches("has_tag_value"):
            self.pos += 1
            key, value = self._call_args(2)
            return _HasTagValue(key, value)
        if self._matches("has_tag"):
            self.pos += 1
            (key,) = self._call_args(1)
            return _HasTag(key)
        if self._matches("ignored_fn"):
            self.pos += 1
            self._skip_call()
            return _True()
        raise InvalidRequestError("Expected has_tag / has_tag_value in tag condition")

    def _call_args(self, count: int) -> tuple[str, ...]:
        if not self._matches("lparen"):
            raise InvalidRequestError("Expected '(' after tag function")
        self.pos += 1
        args: list[str] = []
        for index in range(count):
            if index and not self._matches("comma"):
                raise InvalidRequestError("Expected ',' between tag function arguments")
            if index:
                self.pos += 1
            args.append(self._string())
        if not self._matches("rparen"):
            raise InvalidRequestError("Expected ')' after tag function arguments")
        self.pos += 1
        return tuple(args)

    def _skip_call(self) -> None:
        if not self._matches("lparen"):
            raise InvalidRequestError("Expected '(' after function name")
        self.pos += 1
        depth = 1
        while self.pos < len(self.tokens) and depth:
            if self._matches("lparen"):
                depth += 1
            elif self._matches("rparen"):
                depth -= 1
            self.pos += 1
        if depth:
            raise InvalidRequestError("Unclosed '(' in tag condition")

    def _string(self) -> str:
        if not self._matches("string"):
            raise InvalidRequestError("Expected string literal in tag condition")
        raw = self.tokens[self.pos].group("string")
        self.pos += 1
        return raw[1:-1].replace("''", "'")

    def _matches(self, name: str) -> bool:
        if self.pos >= len(self.tokens):
            return False
        return self.tokens[self.pos].group(name) is not None

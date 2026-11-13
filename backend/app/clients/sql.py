"""SQL warehouse access via the Statement Execution API, plus SQL quoting helpers."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    Format,
    StatementParameterListItem,
    StatementState,
)

from backend.app.core.errors import InvalidRequestError, NotConfiguredError, UpstreamError
from backend.app.core.perf import track

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_\-]+$")
_PENDING = {StatementState.PENDING, StatementState.RUNNING}


def quote_identifier(name: str) -> str:
    """Backtick-quote a Unity Catalog name segment after validating it.

    Args:
        name: Single name segment (no dots); letters, digits, ``_`` and ``-`` only.

    Returns:
        The backtick-quoted identifier.

    Raises:
        InvalidRequestError: If ``name`` contains any other character or is empty.
    """
    if not _IDENTIFIER.match(name):
        raise InvalidRequestError(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def string_literal(value: str) -> str:
    """Render ``value`` as a single-quoted SQL string literal with backslash escaping.

    Intended for SQL that cannot use named parameters, such as DDL or dynamically built
    predicates.

    Args:
        value: Raw string.

    Returns:
        The quoted, escaped literal.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class SqlExecutor:
    """Runs SQL on a warehouse through the Statement Execution API and returns dict rows.

    Statements run as whichever identity ``client_provider`` returns (the container wires
    one executor to the Account Admin SP and one to the app SP). Results are not cached.

    Args:
        client_provider: Callable returning the workspace client to execute with; called
            once per statement.
        warehouse_id: SQL warehouse ID; None makes every statement raise
            ``NotConfiguredError``.
        wait_timeout_seconds: Server-side synchronous wait before the API returns a
            still-running statement.
        max_wait_seconds: Total time to poll a running statement before cancelling it.
    """

    def __init__(
        self,
        client_provider: Callable[[], WorkspaceClient],
        warehouse_id: str | None,
        *,
        wait_timeout_seconds: int = 30,
        max_wait_seconds: int = 90,
    ) -> None:
        self._client_provider = client_provider
        self._warehouse_id = warehouse_id
        self._wait_timeout = f"{wait_timeout_seconds}s"
        self._max_wait = max_wait_seconds

    @property
    def configured(self) -> bool:
        """Whether a warehouse ID is set."""
        return bool(self._warehouse_id)

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a statement and return all result rows.

        Waits for completion (polling once per second up to ``max_wait_seconds``) and
        fetches every result chunk. Values are returned as the API's JSON strings.

        Args:
            statement: SQL text; reference parameters as ``:name``.
            parameters: Named parameters; values are sent as strings, None as SQL NULL.

        Returns:
            One dict per row, keyed by column name; empty for statements without results.

        Raises:
            NotConfiguredError: If no warehouse ID is configured.
            UpstreamError: If the statement fails, is cancelled, or times out (a timed-out
                statement is cancelled first).
        """
        if not self._warehouse_id:
            raise NotConfiguredError("DATABRICKS_WAREHOUSE_ID is not configured")
        with track("warehouse"):
            return self._run_statement(statement, parameters)

    def _run_statement(self, statement: str, parameters: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Submit, poll, and page one statement (see :meth:`query`)."""
        client = self._client_provider()
        params = [
            StatementParameterListItem(name=k, value=None if v is None else str(v))
            for k, v in (parameters or {}).items()
        ]
        response = client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._warehouse_id,
            parameters=params or None,
            wait_timeout=self._wait_timeout,
            disposition=Disposition.INLINE,
            format=Format.JSON_ARRAY,
        )
        deadline = time.monotonic() + self._max_wait
        while response.status and response.status.state in _PENDING:
            if time.monotonic() > deadline:
                client.statement_execution.cancel_execution(response.statement_id)
                raise UpstreamError("SQL statement timed out")
            time.sleep(1)
            response = client.statement_execution.get_statement(response.statement_id)

        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            message = response.status.error.message if response.status and response.status.error else state
            raise UpstreamError(f"SQL statement failed: {message}")

        columns = [c.name for c in (response.manifest.schema.columns or [])] if response.manifest else []
        rows: list[list[Any]] = list(response.result.data_array or []) if response.result else []
        next_chunk = response.result.next_chunk_index if response.result else None
        while next_chunk is not None:
            chunk = client.statement_execution.get_statement_result_chunk_n(response.statement_id, next_chunk)
            rows.extend(chunk.data_array or [])
            next_chunk = chunk.next_chunk_index
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        """Execute a statement for its side effects, discarding any rows.

        Args:
            statement: SQL text; reference parameters as ``:name``.
            parameters: Named parameters; values are sent as strings, None as SQL NULL.

        Raises:
            NotConfiguredError: If no warehouse ID is configured.
            UpstreamError: If the statement fails or times out.
        """
        self.query(statement, parameters)

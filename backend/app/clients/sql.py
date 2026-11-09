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

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_\-]+$")
_PENDING = {StatementState.PENDING, StatementState.RUNNING}


def quote_identifier(name: str) -> str:
    """Backtick-quote a Unity Catalog name segment after validating it."""
    if not _IDENTIFIER.match(name):
        raise InvalidRequestError(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class SqlExecutor:
    """Runs SQL on a warehouse through the Statement Execution API and returns dict rows."""

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
        return bool(self._warehouse_id)

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not self._warehouse_id:
            raise NotConfiguredError("DATABRICKS_WAREHOUSE_ID is not configured")
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
        self.query(statement, parameters)

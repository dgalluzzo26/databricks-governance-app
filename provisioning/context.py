from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

logger = logging.getLogger("provisioning")


class SqlRunner:
    """Runs SQL on a SQL warehouse through the Statement Execution API."""

    def __init__(self, client: WorkspaceClient, warehouse_id: str, poll_seconds: float = 1.0) -> None:
        self._client = client
        self._warehouse_id = warehouse_id
        self._poll_seconds = poll_seconds

    def execute(self, statement: str) -> list[list[str | None]]:
        response = self._client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._warehouse_id,
            wait_timeout="30s",
            disposition=Disposition.INLINE,
            format=Format.JSON_ARRAY,
        )
        while response.status and response.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(self._poll_seconds)
            response = self._client.statement_execution.get_statement(response.statement_id)

        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            message = response.status.error.message if response.status and response.status.error else state
            raise RuntimeError(f"SQL failed ({state}): {message}\n{statement}")
        return (response.result.data_array or []) if response.result else []


@dataclass
class ProvisioningContext:
    client: WorkspaceClient
    sql: SqlRunner
    app_name: str
    app_catalog: str
    app_schema: str
    catalog_managed_location: str | None = None

    @property
    def app_schema_fqn(self) -> str:
        return f"`{self.app_catalog}`.`{self.app_schema}`"

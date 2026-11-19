"""Setup job: the app catalog grants ALL PRIVILEGES to the app SP and the Account Admin SP."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound

from provisioning.context import ProvisioningContext
from provisioning.steps.catalogs import CatalogsStep

ADMIN_SP = "c300a3e9-7bea-4035-9022-b0b1346ec68f"
APP_SP = "18514e8e-168c-4097-b71e-f95d9957ef1a"


class _Sql:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> list:
        self.statements.append(statement)
        return []


class _Apps:
    def __init__(self, sp: str | None) -> None:
        self._sp = sp

    def get(self, name: str) -> Any:
        if self._sp is None:
            raise NotFound("no app")
        return SimpleNamespace(service_principal_client_id=self._sp)


def _ctx(sp: str | None, sql: _Sql) -> ProvisioningContext:
    client = SimpleNamespace(apps=_Apps(sp))
    return ProvisioningContext(
        client=client,  # type: ignore[arg-type]
        sql=sql,  # type: ignore[arg-type]
        app_name="data-governance-app",
        app_catalog="data_governance_app",
        app_schema="app",
        admin_sp_client_id=ADMIN_SP,
    )


def test_catalog_step_grants_all_privileges_to_app_and_admin_sps() -> None:
    sql = _Sql()
    CatalogsStep().run(_ctx(APP_SP, sql))
    grants = [s for s in sql.statements if s.startswith("GRANT")]
    assert grants == [
        f"GRANT ALL PRIVILEGES ON CATALOG `data_governance_app` TO `{ADMIN_SP}`",
        f"GRANT ALL PRIVILEGES ON CATALOG `data_governance_app` TO `{APP_SP}`",
    ]


def test_catalog_step_still_grants_admin_sp_when_app_is_missing() -> None:
    sql = _Sql()
    CatalogsStep().run(_ctx(None, sql))
    grants = [s for s in sql.statements if s.startswith("GRANT")]
    assert grants == [f"GRANT ALL PRIVILEGES ON CATALOG `data_governance_app` TO `{ADMIN_SP}`"]

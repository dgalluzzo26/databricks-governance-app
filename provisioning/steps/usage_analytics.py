"""Provision the usage-analytics schema and the grants the daily rollup Job needs."""

from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")

USAGE_SCHEMA = "usage"

# System schemas the rollup reads. Granting them requires a metastore admin (or MANAGE on
# the system catalog), so failures are logged with the statement to run by hand.
SYSTEM_SCHEMAS = ("system.access", "system.query")


def system_grant_statements(principal: str) -> list[str]:
    """Return the GRANTs a metastore admin runs so ``principal`` can read lineage and queries."""
    statements = [f"GRANT USE CATALOG ON CATALOG system TO `{principal}`"]
    for schema in SYSTEM_SCHEMAS:
        statements.append(f"GRANT USE SCHEMA, SELECT ON SCHEMA {schema} TO `{principal}`")
    return statements


class UsageAnalyticsStep(ProvisioningStep):
    """Create ``{app_catalog}.usage`` and grant the rollup Job and the app access to it.

    - The Account Admin SP (rollup Job run-as) owns the schema so it can create and
      rewrite the summary tables.
    - The app SP gets USE SCHEMA and SELECT so the dashboard API and Genie can read them.
    - The Account Admin SP gets SELECT on ``system.access`` and ``system.query``
      (best effort: needs a metastore admin).
    """

    name = "usage_analytics"

    def run(self, ctx: ProvisioningContext) -> None:
        schema = f"`{ctx.app_catalog}`.`{USAGE_SCHEMA}`"
        ctx.sql.execute(
            f"CREATE SCHEMA IF NOT EXISTS {schema} "
            "COMMENT 'Daily usage rollups: table, user, and sensitive-column reads'"
        )
        admin_sp = (ctx.admin_sp_client_id or "").strip()
        if admin_sp:
            ctx.sql.execute(f"GRANT USE CATALOG ON CATALOG `{ctx.app_catalog}` TO `{admin_sp}`")
            ctx.sql.execute(f"ALTER SCHEMA {schema} OWNER TO `{admin_sp}`")
            self._grant_system_tables(ctx, admin_sp)
        else:
            logger.warning("No --admin-sp-client-id; usage schema stays owned by the deployer")

        app_sp = self._app_principal(ctx)
        if app_sp:
            ctx.sql.execute(f"GRANT USE SCHEMA, SELECT ON SCHEMA {schema} TO `{app_sp}`")
        else:
            logger.warning("App %s not found yet; skipping usage schema grant", ctx.app_name)
        logger.info("Usage analytics schema %s ready", schema)

    @staticmethod
    def _grant_system_tables(ctx: ProvisioningContext, principal: str) -> None:
        failed: list[str] = []
        for statement in system_grant_statements(principal):
            try:
                ctx.sql.execute(statement)
            except RuntimeError as exc:
                failed.append(statement)
                logger.warning("System table grant failed (needs a metastore admin): %s", exc)
        if failed:
            logger.warning(
                "Ask a metastore admin to run, so the usage rollup can read lineage:\n  %s",
                ";\n  ".join(failed),
            )
        else:
            logger.info("Admin SP can read %s", ", ".join(SYSTEM_SCHEMAS))

    @staticmethod
    def _app_principal(ctx: ProvisioningContext) -> str | None:
        try:
            app = ctx.client.apps.get(ctx.app_name)
        except NotFound:
            return None
        return app.service_principal_client_id

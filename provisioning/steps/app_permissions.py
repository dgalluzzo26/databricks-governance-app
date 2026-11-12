from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")


class GrantAppPermissionsStep(ProvisioningStep):
    """Grants the app's service principal what it needs in Unity Catalog.

    - App schema: read/write for the Delta storage backend.
    - Masking functions schema: USE SCHEMA so ABAC policies and Access Explorer can resolve UDFs.
    """

    name = "app_permissions"

    def run(self, ctx: ProvisioningContext) -> None:
        principal = self._app_principal(ctx)
        if principal is None:
            logger.warning(
                "App %s not found yet; skipping grants. Re-run after the app exists.", ctx.app_name
            )
            return

        grants = [
            f"GRANT USE CATALOG ON CATALOG `{ctx.app_catalog}` TO `{principal}`",
            f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {ctx.app_schema_fqn} TO `{principal}`",
            # Pack sync recreates geo mask UDFs via the Account Admin SP; the app SP still
            # needs USE SCHEMA so runtime checks and Access Explorer can resolve them.
            (f"GRANT USE SCHEMA ON SCHEMA `{ctx.app_catalog}`.`masking_functions` TO `{principal}`"),
        ]
        for statement in grants:
            ctx.sql.execute(statement)
        logger.info("Granted app service principal %s access", principal)

    @staticmethod
    def _app_principal(ctx: ProvisioningContext) -> str | None:
        try:
            app = ctx.client.apps.get(ctx.app_name)
        except NotFound:
            return None
        return app.service_principal_client_id

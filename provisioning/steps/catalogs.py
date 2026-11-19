from __future__ import annotations

import logging

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")


class CatalogsStep(ProvisioningStep):
    """Creates the app catalog and schema.

    Catalogs are created through SQL rather than as bundle resources: SQL CREATE CATALOG picks
    up the account's Default Storage, while the catalogs API needs an explicit managed location
    on metastores without a storage root.
    """

    name = "catalogs"

    def run(self, ctx: ProvisioningContext) -> None:
        self._create_catalog(ctx, ctx.app_catalog, "Data Governance App state")
        self._grant_all_privileges(ctx)
        ctx.sql.execute(
            f"CREATE SCHEMA IF NOT EXISTS {ctx.app_schema_fqn} COMMENT 'Data Governance App Delta tables'"
        )
        logger.info("Catalogs ready")

    @staticmethod
    def _grant_all_privileges(ctx: ProvisioningContext) -> None:
        """Give the app SP and the Account Admin SP ALL PRIVILEGES on the app catalog.

        Catalog privileges carry down to its schemas, so this also covers the masking functions
        schema the mask and row-filter packs write to.
        """
        app_sp = ctx.app_principal()
        if app_sp is None:
            logger.warning("App %s not found yet; its service principal gets no catalog grant", ctx.app_name)
        principals = [p for p in (ctx.admin_sp_client_id, app_sp) if p]
        for principal in dict.fromkeys(principals):
            ctx.sql.execute(f"GRANT ALL PRIVILEGES ON CATALOG `{ctx.app_catalog}` TO `{principal}`")

    @staticmethod
    def _create_catalog(ctx: ProvisioningContext, name: str, comment: str) -> None:
        location = (
            f" MANAGED LOCATION '{ctx.catalog_managed_location}'" if ctx.catalog_managed_location else ""
        )
        ctx.sql.execute(f"CREATE CATALOG IF NOT EXISTS `{name}`{location} COMMENT '{comment}'")

from __future__ import annotations

import logging

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")


class DeltaStorageStep(ProvisioningStep):
    """Creates the Delta tables used when STORAGE_BACKEND=delta.

    The Lakebase backend creates its own Postgres schema at app startup, because the app's
    service principal must own that schema.
    """

    name = "delta_storage"

    def run(self, ctx: ProvisioningContext) -> None:
        schema = ctx.app_schema_fqn
        ctx.sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.app_settings (
              setting_key STRING NOT NULL,
              setting_value STRING NOT NULL,
              updated_by STRING,
              updated_at TIMESTAMP
            )
            COMMENT 'Governance app runtime settings (JSON values)'
            """
        )
        ctx.sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.audit_events (
              event_id STRING NOT NULL,
              event_type STRING NOT NULL,
              actor STRING,
              payload STRING,
              created_at TIMESTAMP
            )
            COMMENT 'Governance app audit trail'
            """
        )
        logger.info("Delta storage tables ready in %s", schema)

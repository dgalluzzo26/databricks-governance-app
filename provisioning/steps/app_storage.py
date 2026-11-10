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
        ctx.sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.identity_attribute_definitions (
              attribute_key STRING NOT NULL,
              description STRING,
              governed_tag_keys STRING NOT NULL,
              allowed_values STRING NOT NULL,
              created_by STRING,
              created_at TIMESTAMP,
              updated_by STRING,
              updated_at TIMESTAMP
            )
            COMMENT 'App-managed identity attribute definitions linked to governed tags'
            """
        )
        ctx.sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.identity_attributes (
              assignment_id STRING NOT NULL,
              principal_name STRING NOT NULL,
              principal_type STRING NOT NULL,
              attribute_key STRING NOT NULL,
              attribute_value STRING NOT NULL,
              source STRING NOT NULL,
              granted_by STRING,
              justification STRING,
              expires_at TIMESTAMP,
              created_at TIMESTAMP,
              revoked_at TIMESTAMP,
              revoked_by STRING
            )
            COMMENT 'App-managed identity attributes (until native identity attributes are available)'
            """
        )
        logger.info("Delta storage tables ready in %s", schema)

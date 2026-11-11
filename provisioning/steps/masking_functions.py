"""Ensure the app-owned authorization and mask UDFs exist (same DDL as pack sync)."""

from __future__ import annotations

import logging

from backend.app.services import abac_udfs
from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")

MASKING_SCHEMA = "masking_functions"


class MaskingFunctionsStep(ProvisioningStep):
    """Creates ``{app_catalog}.masking_functions`` UDFs that the app's ABAC policies need.

    Builds the same DDL as ``MaskRuleService.ensure_pack`` (``backend/app/services/abac_udfs``),
    so a fresh deploy has working authorization and masks before the first UI sync:

    - ``has_sensitivity`` (needs the identity mirror table ``{app_catalog}.{app_schema}.identity_attributes``)
    - ``mask_variant`` (needs ``apply_mask_rule``, which the app regenerates on sync)
    - ``mask_geometry_4326`` / ``mask_geography_4326`` (need ``has_sensitivity``)

    Anything whose dependency is missing is skipped with a warning; pack sync creates it later.
    """

    name = "masking_functions"

    def run(self, ctx: ProvisioningContext) -> None:
        schema = abac_udfs.fqn(ctx.app_catalog, MASKING_SCHEMA)
        ctx.sql.execute(
            f"CREATE SCHEMA IF NOT EXISTS {schema} "
            "COMMENT 'ABAC mask and row-filter functions for the Data Governance App'"
        )

        has_sensitivity = abac_udfs.fqn(ctx.app_catalog, MASKING_SCHEMA, "has_sensitivity")
        identity = abac_udfs.fqn(ctx.app_catalog, ctx.app_schema, "identity_attributes")
        if self._table_exists(ctx, ctx.app_catalog, ctx.app_schema, "identity_attributes"):
            ctx.sql.execute(abac_udfs.build_has_sensitivity_sql(has_sensitivity, identity))
        else:
            logger.warning(
                "Skipping has_sensitivity: %s.%s.identity_attributes not found yet "
                "(pack sync creates it later).",
                ctx.app_catalog,
                ctx.app_schema,
            )
            return

        if self._routine_exists(ctx, ctx.app_catalog, MASKING_SCHEMA, "apply_mask_rule"):
            ctx.sql.execute(
                abac_udfs.build_mask_variant_sql(
                    abac_udfs.fqn(ctx.app_catalog, MASKING_SCHEMA, "mask_variant"),
                    has_sensitivity,
                    abac_udfs.fqn(ctx.app_catalog, MASKING_SCHEMA, "apply_mask_rule"),
                )
            )
        else:
            logger.warning("Skipping mask_variant: apply_mask_rule not found yet (pack sync).")

        for kind in abac_udfs.GEO_KINDS:
            ctx.sql.execute(
                abac_udfs.build_geo_mask_sql(
                    kind,
                    abac_udfs.fqn(ctx.app_catalog, MASKING_SCHEMA, f"mask_{kind}_4326"),
                    has_sensitivity,
                )
            )
        logger.info("Authorization and geo mask UDFs ready in %s", schema)

    @staticmethod
    def _table_exists(ctx: ProvisioningContext, catalog: str, schema: str, table: str) -> bool:
        rows = ctx.sql.execute(
            f"""
            SELECT 1 FROM system.information_schema.tables
            WHERE table_catalog = '{catalog}' AND table_schema = '{schema}'
              AND table_name = '{table}'
            LIMIT 1
            """
        )
        return bool(rows)

    @staticmethod
    def _routine_exists(ctx: ProvisioningContext, catalog: str, schema: str, routine: str) -> bool:
        rows = ctx.sql.execute(
            f"""
            SELECT 1 FROM system.information_schema.routines
            WHERE routine_catalog = '{catalog}' AND routine_schema = '{schema}'
              AND routine_name = '{routine}'
            LIMIT 1
            """
        )
        return bool(rows)

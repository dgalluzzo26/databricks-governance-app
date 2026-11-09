from __future__ import annotations

import logging
from dataclasses import dataclass

from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import (
    ColumnMaskOptions,
    FunctionArgument,
    MatchColumn,
    PolicyInfo,
    PolicyType,
    RowFilterOptions,
    SecurableType,
)

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")

GOVERNANCE_SCHEMA = "governance"


@dataclass(frozen=True)
class DemoPolicy:
    name: str
    on_schema: str | None
    info: PolicyInfo


class DemoPoliciesStep(ProvisioningStep):
    """Creates mask/filter functions and native UC ABAC policies (column masks and row filters).

    ABAC GRANT/DENY policy types are not used: they do not yet support catalog, schema, or table
    privileges. Table access is handled later by app-managed grant policies.
    """

    name = "demo_policies"
    demo_only = True

    def run(self, ctx: ProvisioningContext) -> None:
        self._create_functions(ctx)
        for policy in self._policies(ctx):
            self._upsert(ctx, policy)

    def _fn(self, ctx: ProvisioningContext, name: str) -> str:
        return f"{ctx.demo_catalog}.{GOVERNANCE_SCHEMA}.{name}"

    def _create_functions(self, ctx: ProvisioningContext) -> None:
        schema = f"`{ctx.demo_catalog}`.`{GOVERNANCE_SCHEMA}`"
        ctx.sql.execute(f"CREATE SCHEMA IF NOT EXISTS {schema} COMMENT 'Mask and row filter functions'")
        ctx.sql.execute(
            f"""
            CREATE OR REPLACE FUNCTION {schema}.mask_string(val STRING)
            RETURNS STRING
            COMMENT 'Redacts a sensitive string value'
            RETURN CASE WHEN val IS NULL THEN NULL ELSE '****' END
            """
        )
        ctx.sql.execute(
            f"""
            CREATE OR REPLACE FUNCTION {schema}.mask_last4(val STRING)
            RETURNS STRING
            COMMENT 'Shows only the last four characters'
            RETURN CASE WHEN val IS NULL THEN NULL ELSE concat('****', right(val, 4)) END
            """
        )
        ctx.sql.execute(
            f"""
            CREATE OR REPLACE FUNCTION {schema}.rf_deny_all(dimension_value STRING)
            RETURNS BOOLEAN
            COMMENT 'Default-deny row filter: no rows unless the caller is excepted by the policy'
            RETURN FALSE
            """
        )

    def _policies(self, ctx: ProvisioningContext) -> list[DemoPolicy]:
        excepted = ctx.demo_except_principals
        catalog = ctx.demo_catalog
        return [
            DemoPolicy(
                name="dga_mask_pii_finance",
                on_schema=None,
                info=PolicyInfo(
                    name="dga_mask_pii_finance",
                    comment="Default mask for PII columns in the Finance domain",
                    policy_type=PolicyType.POLICY_TYPE_COLUMN_MASK,
                    on_securable_type=SecurableType.CATALOG,
                    on_securable_fullname=catalog,
                    for_securable_type=SecurableType.TABLE,
                    to_principals=["account users"],
                    except_principals=excepted,
                    when_condition="has_tag_value('dga_domain', 'finance')",
                    match_columns=[
                        MatchColumn(
                            alias="c",
                            condition="has_tag_value('dga_sensitivity', 'PII.FirstName') OR has_tag_value('dga_sensitivity', 'PII.LastName') OR has_tag_value('dga_sensitivity', 'PII.Address') OR has_tag_value('dga_sensitivity', 'PII.SSN')",
                        )
                    ],
                    column_mask=ColumnMaskOptions(function_name=self._fn(ctx, "mask_string"), on_column="c"),
                ),
            ),
            DemoPolicy(
                name="dga_mask_bank_account",
                on_schema=None,
                info=PolicyInfo(
                    name="dga_mask_bank_account",
                    comment="Partial mask for bank account numbers everywhere",
                    policy_type=PolicyType.POLICY_TYPE_COLUMN_MASK,
                    on_securable_type=SecurableType.CATALOG,
                    on_securable_fullname=catalog,
                    for_securable_type=SecurableType.TABLE,
                    to_principals=["account users"],
                    except_principals=excepted,
                    match_columns=[
                        MatchColumn(alias="c", condition="has_tag_value('dga_sensitivity', 'FIN.BankAcct')")
                    ],
                    column_mask=ColumnMaskOptions(function_name=self._fn(ctx, "mask_last4"), on_column="c"),
                ),
            ),
            DemoPolicy(
                name="dga_mask_pii_hr_payroll",
                on_schema="hr_payroll",
                info=PolicyInfo(
                    name="dga_mask_pii_hr_payroll",
                    comment="Default mask for all sensitive columns in HR payroll",
                    policy_type=PolicyType.POLICY_TYPE_COLUMN_MASK,
                    on_securable_type=SecurableType.SCHEMA,
                    on_securable_fullname=f"{catalog}.hr_payroll",
                    for_securable_type=SecurableType.TABLE,
                    to_principals=["account users"],
                    except_principals=excepted,
                    match_columns=[MatchColumn(alias="c", condition="has_tag('dga_sensitivity')")],
                    column_mask=ColumnMaskOptions(function_name=self._fn(ctx, "mask_string"), on_column="c"),
                ),
            ),
            DemoPolicy(
                name="dga_rows_dr_default_deny",
                on_schema=None,
                info=PolicyInfo(
                    name="dga_rows_dr_default_deny",
                    comment="Default-deny DR row filter: no rows unless excepted",
                    policy_type=PolicyType.POLICY_TYPE_ROW_FILTER,
                    on_securable_type=SecurableType.CATALOG,
                    on_securable_fullname=catalog,
                    for_securable_type=SecurableType.TABLE,
                    to_principals=["account users"],
                    except_principals=excepted,
                    match_columns=[
                        MatchColumn(alias="dr", condition="has_tag_value('dga_row_filter', 'DR')")
                    ],
                    row_filter=RowFilterOptions(
                        function_name=self._fn(ctx, "rf_deny_all"),
                        using=[FunctionArgument(alias="dr")],
                    ),
                ),
            ),
        ]

    @staticmethod
    def _upsert(ctx: ProvisioningContext, policy: DemoPolicy) -> None:
        api = ctx.client.policies
        info = policy.info
        securable_type = info.on_securable_type.value
        try:
            api.get_policy(securable_type, info.on_securable_fullname, info.name)
            api.delete_policy(securable_type, info.on_securable_fullname, info.name)
            logger.info("Replacing policy %s", info.name)
        except NotFound:
            pass
        api.create_policy(policy_info=info)
        logger.info("Policy %s ready on %s %s", info.name, securable_type, info.on_securable_fullname)

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")


@dataclass(frozen=True)
class DemoTable:
    schema: str
    name: str
    columns: str
    rows: str
    column_tags: dict[str, dict[str, str]] = field(default_factory=dict)
    table_tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DemoSchema:
    name: str
    tags: dict[str, str]
    tables: list[DemoTable]


DEMO_SCHEMAS: list[DemoSchema] = [
    DemoSchema(
        name="finance_budget",
        tags={"dga_domain": "finance", "dga_subdomain": "finance.budget"},
        tables=[
            DemoTable(
                schema="finance_budget",
                name="vendor_payments",
                columns=(
                    "payment_id BIGINT, dr_number STRING, vendor_first_name STRING, vendor_last_name STRING, "
                    "vendor_address STRING, vendor_ssn STRING, bank_account STRING, amount DECIMAL(18,2), "
                    "fiscal_period STRING"
                ),
                rows=(
                    "(1, 'DR-4562', 'Ana', 'Lopez', '12 Elm St', '123-45-6789', '000123456789', 1250.00, 'FY27-Q1'),"
                    "(2, 'DR-4571', 'Ben', 'Okafor', '9 Oak Ave', '987-65-4321', '000987654321', 980.50, 'FY27-Q1'),"
                    "(3, 'DR-4580', 'Chen', 'Wu', '400 Pine Rd', '555-11-2222', '000555111222', 4300.00, 'FY27-Q2')"
                ),
                column_tags={
                    "dr_number": {"dga_row_filter": "DR"},
                    "vendor_first_name": {"dga_sensitivity": "PII.FirstName"},
                    "vendor_last_name": {"dga_sensitivity": "PII.LastName"},
                    "vendor_address": {"dga_sensitivity": "PII.Address"},
                    "vendor_ssn": {"dga_sensitivity": "PII.SSN"},
                    "bank_account": {"dga_sensitivity": "FIN.BankAcct"},
                },
            ),
            DemoTable(
                schema="finance_budget",
                name="budget_lines",
                columns="line_id BIGINT, cost_center STRING, fiscal_period STRING, planned DECIMAL(18,2)",
                rows="(1, 'CC-100', 'FY27-Q1', 50000.00), (2, 'CC-200', 'FY27-Q1', 72000.00)",
            ),
        ],
    ),
    DemoSchema(
        name="finance_contracts",
        tags={"dga_domain": "finance", "dga_subdomain": "finance.contracts"},
        tables=[
            DemoTable(
                schema="finance_contracts",
                name="contracts",
                columns=(
                    "contract_id BIGINT, vendor_first_name STRING, vendor_last_name STRING, "
                    "contract_value DECIMAL(18,2), dr_number STRING"
                ),
                rows="(10, 'Ana', 'Lopez', 250000.00, 'DR-4562'), (11, 'Dee', 'Patel', 90000.00, 'DR-4590')",
                column_tags={
                    "vendor_first_name": {"dga_sensitivity": "PII.FirstName"},
                    "vendor_last_name": {"dga_sensitivity": "PII.LastName"},
                    "dr_number": {"dga_row_filter": "DR"},
                },
            ),
        ],
    ),
    DemoSchema(
        name="hr_payroll",
        tags={"dga_domain": "hr", "dga_subdomain": "hr.payroll"},
        tables=[
            DemoTable(
                schema="hr_payroll",
                name="employees",
                columns=(
                    "employee_id BIGINT, first_name STRING, last_name STRING, home_address STRING, "
                    "ssn STRING, region STRING, salary DECIMAL(18,2)"
                ),
                rows=(
                    "(100, 'Eve', 'Stone', '1 River Way', '111-22-3333', 'EAST', 98000.00),"
                    "(101, 'Raj', 'Kumar', '77 Hill St', '444-55-6666', 'WEST', 105000.00)"
                ),
                column_tags={
                    "first_name": {"dga_sensitivity": "PII.FirstName"},
                    "last_name": {"dga_sensitivity": "PII.LastName"},
                    "home_address": {"dga_sensitivity": "PII.Address"},
                    "ssn": {"dga_sensitivity": "PII.SSN"},
                    "region": {"dga_row_filter": "REGION"},
                },
            ),
        ],
    ),
]


def _tag_clause(tags: dict[str, str]) -> str:
    return ", ".join(f"'{k}' = '{v}'" for k, v in tags.items())


class DemoDomainStep(ProvisioningStep):
    """Creates tagged demo schemas and tables in the demo catalog."""

    name = "demo_domain"
    demo_only = True

    def run(self, ctx: ProvisioningContext) -> None:
        catalog = f"`{ctx.demo_catalog}`"
        for schema in DEMO_SCHEMAS:
            schema_fqn = f"{catalog}.`{schema.name}`"
            ctx.sql.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
            ctx.sql.execute(f"ALTER SCHEMA {schema_fqn} SET TAGS ({_tag_clause(schema.tags)})")
            for table in schema.tables:
                self._create_table(ctx, schema_fqn, table)
            logger.info("Demo schema %s.%s ready", ctx.demo_catalog, schema.name)

    @staticmethod
    def _create_table(ctx: ProvisioningContext, schema_fqn: str, table: DemoTable) -> None:
        table_fqn = f"{schema_fqn}.`{table.name}`"
        ctx.sql.execute(f"CREATE TABLE IF NOT EXISTS {table_fqn} ({table.columns})")
        count = ctx.sql.execute(f"SELECT COUNT(*) FROM {table_fqn}")
        if count and int(count[0][0] or 0) == 0:
            ctx.sql.execute(f"INSERT INTO {table_fqn} VALUES {table.rows}")
        if table.table_tags:
            ctx.sql.execute(f"ALTER TABLE {table_fqn} SET TAGS ({_tag_clause(table.table_tags)})")
        for column, tags in table.column_tags.items():
            ctx.sql.execute(f"ALTER TABLE {table_fqn} ALTER COLUMN `{column}` SET TAGS ({_tag_clause(tags)})")

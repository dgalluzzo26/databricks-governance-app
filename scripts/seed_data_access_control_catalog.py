#!/usr/bin/env python3
"""Seed catalog ``data_access_control`` with FEMA-domain schemas, dummy tables, and tags.

Creates:
  * Catalog ``data_access_control``
  * One schema per FEMA domain
  * Tables named ``<subdomain>_<table>`` with ``data_access_control`` + ``clr_review_status``
  * Column tags for masking (sensitivity_category / sensitivity / data_type / mask_rule)
  * Column tags for row filters (``row_filter=state``) on selected tables
  * Extends the governed ``data_access_control`` tag policy with new path values

Usage:
  python scripts/seed_data_access_control_catalog.py --profile dbc-16303671-e52d
  python scripts/seed_data_access_control_catalog.py --profile dbc-16303671-e52d --warehouse-id dd0a656c3ddc883e
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any

CATALOG = "data_access_control"
ACCESS_TAG = "data_access_control"
REVIEW_TAG = "clr_review_status"
STATES = ("CA", "NY", "TX", "FL", "WA", "CO", "VA", "IL")


@dataclass(frozen=True)
class TableSpec:
    schema: str
    name: str
    access_value: str
    reviewed: bool
    columns_sql: str
    insert_sql: str
    column_tags: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ViewSpec:
    """A view that carries object-level access tags only.

    Column tags are never set on views: Unity Catalog rejects a view with its own row filter or
    column mask when it reads a table that already has one (``UNSUPPORTED_NESTED_ROW_OR_COLUMN_ACCESS_POLICY``).
    The base table's masks and row filters still apply when the view is queried, and Access
    Explorer inherits them through column lineage.
    """

    schema: str
    name: str
    access_value: str
    reviewed: bool
    select_sql: str


# Placeholder replaced with the owning table's data_access_control path in build_tables().
TABLE_CATEGORY = "@table"


def _sens(leaf: str, mask: str, category: str = TABLE_CATEGORY) -> dict[str, str]:
    """Column tags for the app mask pack.

    Required path checked by auth is ``sensitivity_category.sensitivity``, e.g.
    ``people.employees.PII.Name`` or ``finance.payroll.SPII.BankAcct``.
    """
    return {
        "sensitivity_category": category,
        "sensitivity": leaf,
        "data_type": "string",
        "mask_rule": mask,
    }


def _pii(column: str, leaf: str, mask: str, category: str = TABLE_CATEGORY) -> dict[str, str]:
    """Personally identifiable (PII.<leaf>); category defaults to the table's access path."""
    del column
    return _sens(f"PII.{leaf}", mask, category)


def _spii(column: str, leaf: str, mask: str, category: str = TABLE_CATEGORY) -> dict[str, str]:
    """Sensitive PII (SPII.<leaf>) such as SSN, tax id, bank account."""
    del column
    return _sens(f"SPII.{leaf}", mask, category)


def _rf_state() -> dict[str, str]:
    return {"row_filter": "state"}


CROSS_SCHEMA = "cross_domain"
CROSS_TABLE = "people_employees_workforce_profile"

# Columns sourced from different domains: category = the domain that owns that data.
CROSS_COLUMN_TAGS: dict[str, dict[str, str]] = {
    "first_name": _pii("first_name", "Name", "full"),  # people.employees
    "last_name": _pii("last_name", "Name", "full"),  # people.employees
    "email": _pii("email", "Email", "email"),  # people.employees
    "ssn": _spii("ssn", "SSN", "keep_last_4"),  # people.employees
    "payroll_deposit_account": _spii(
        "payroll_deposit_account", "BankAcct", "keep_last_4", "finance.payroll"
    ),
    "deployment_contact_phone": _pii(
        "deployment_contact_phone", "Phone", "phone", "incidents.tactical"
    ),
    "training_contact_email": _pii(
        "training_contact_email", "Email", "email", "training_and_exercises.courses"
    ),
    "state": _rf_state(),
}


def _people_rows() -> str:
    rows = []
    names = [
        ("Ana", "Lopez", "ana.lopez@example.com", "555-100-0001", "123-45-1001"),
        ("Ben", "Okafor", "ben.okafor@example.com", "555-100-0002", "123-45-1002"),
        ("Chen", "Wu", "chen.wu@example.com", "555-100-0003", "123-45-1003"),
        ("Dee", "Patel", "dee.patel@example.com", "555-100-0004", "123-45-1004"),
        ("Eve", "Stone", "eve.stone@example.com", "555-100-0005", "123-45-1005"),
        ("Raj", "Kumar", "raj.kumar@example.com", "555-100-0006", "123-45-1006"),
        ("Sam", "Nguyen", "sam.nguyen@example.com", "555-100-0007", "123-45-1007"),
        ("Tina", "Brooks", "tina.brooks@example.com", "555-100-0008", "123-45-1008"),
    ]
    for i, (fn, ln, email, phone, ssn) in enumerate(names, start=1):
        state = STATES[(i - 1) % len(STATES)]
        rows.append(
            f"({i}, '{fn}', '{ln}', '{email}', '{phone}', '{ssn}', '{state}', {70000 + i * 1250})"
        )
    return ", ".join(rows)


def _money_rows(prefix: str) -> str:
    rows = []
    for i in range(1, 13):
        state = STATES[(i - 1) % len(STATES)]
        rows.append(
            f"({i}, '{prefix}-{1000 + i}', '{state}', {10000 + i * 337.25:.2f}, 'FY27-Q{(i % 4) + 1}')"
        )
    return ", ".join(rows)


def _incident_rows() -> str:
    rows = []
    for i in range(1, 13):
        state = STATES[(i - 1) % len(STATES)]
        rows.append(
            f"({i}, 'DR-{4500 + i}', '{state}', 'Type-{(i % 3) + 1}', {i * 1.5:.1f})"
        )
    return ", ".join(rows)


def _asset_rows(kind: str) -> str:
    rows = []
    for i in range(1, 11):
        state = STATES[(i - 1) % len(STATES)]
        rows.append(
            f"({i}, '{kind}-{200 + i}', '{state}', 'Active', {5000 + i * 250})"
        )
    return ", ".join(rows)


def build_tables() -> list[TableSpec]:
    people_cols = (
        "id BIGINT, first_name STRING, last_name STRING, email STRING, phone STRING, "
        "ssn STRING, state STRING, salary DECIMAL(18,2)"
    )
    people_tags = {
        "first_name": _pii("first_name", "Name", "full"),
        "last_name": _pii("last_name", "Name", "full"),
        "email": _pii("email", "Email", "email"),
        "phone": _pii("phone", "Phone", "phone"),
        "ssn": _spii("ssn", "SSN", "keep_last_4"),
        "state": _rf_state(),
    }
    money_cols = (
        "id BIGINT, record_id STRING, state STRING, amount DECIMAL(18,2), fiscal_period STRING"
    )
    asset_cols = (
        "id BIGINT, asset_code STRING, state STRING, status STRING, book_value DECIMAL(18,2)"
    )
    people_rows = _people_rows()
    money_rows = _money_rows("REC")
    incident_rows = _incident_rows()
    asset_rows = _asset_rows("AST")

    tables: list[TableSpec] = [
        # People
        TableSpec("people", "employees_roster", "people.employees", True, people_cols, people_rows, people_tags),
        TableSpec(
            "people",
            "employees_assignments",
            "people.employees",
            True,
            "id BIGINT, employee_id BIGINT, org_unit STRING, state STRING, role STRING",
            ", ".join(
                f"({i}, {i}, 'OU-{(i % 4) + 1}', '{STATES[(i - 1) % len(STATES)]}', 'Role-{(i % 3) + 1}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "people",
            "employees_pending_hires",
            "people.employees",
            False,  # unreviewed on purpose
            people_cols,
            people_rows,
            {k: v for k, v in people_tags.items() if k != "state"},
        ),
        TableSpec("people", "survivors_registrations", "people.survivors", True, people_cols, people_rows, people_tags),
        TableSpec(
            "people",
            "survivors_assistance",
            "people.survivors",
            True,
            "id BIGINT, survivor_id BIGINT, program STRING, state STRING, amount DECIMAL(18,2)",
            ", ".join(
                f"({i}, {i}, 'IHP', '{STATES[(i - 1) % len(STATES)]}', {1200 + i * 50}.00)"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "people",
            "first_responders_roster",
            "people.first_responders",
            True,
            people_cols,
            people_rows,
            people_tags,
        ),
        TableSpec(
            "people",
            "first_responders_certifications",
            "people.first_responders",
            True,
            "id BIGINT, responder_id BIGINT, cert_type STRING, expires_on DATE, state STRING",
            ", ".join(
                f"({i}, {i}, 'CERT-{(i % 3) + 1}', DATE '2027-0{(i % 9) + 1}-15', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        # Finance
        TableSpec(
            "finance",
            "budget_lines",
            "finance.budget",
            True,
            money_cols,
            money_rows,
            {"state": _rf_state()},
        ),
        TableSpec(
            "finance",
            "budget_appropriations",
            "finance.budget",
            True,
            "id BIGINT, account_code STRING, fiscal_year STRING, amount DECIMAL(18,2)",
            ", ".join(f"({i}, 'ACCT-{i}', 'FY27', {50000 + i * 1000}.00)" for i in range(1, 9)),
        ),
        TableSpec(
            "finance",
            "budget_draft_lines",
            "finance.budget",
            False,
            money_cols,
            money_rows,
        ),
        TableSpec(
            "finance",
            "grants_awards",
            "finance.grants",
            True,
            money_cols,
            _money_rows("GR"),
            {"state": _rf_state()},
        ),
        TableSpec(
            "finance",
            "grants_disbursements",
            "finance.grants",
            True,
            "id BIGINT, grant_id STRING, payee STRING, amount DECIMAL(18,2), state STRING",
            ", ".join(
                f"({i}, 'GR-{1000 + i}', 'Payee {i}', {2500 + i * 100}.00, '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "finance",
            "contracts_awards",
            "finance.contracts",
            True,
            "id BIGINT, contract_id STRING, vendor_name STRING, amount DECIMAL(18,2), state STRING",
            ", ".join(
                f"({i}, 'C-{2000 + i}', 'Vendor {i}', {15000 + i * 500}.00, '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "finance",
            "contracts_vendors",
            "finance.contracts",
            True,
            "id BIGINT, vendor_name STRING, contact_email STRING, bank_account STRING, tax_id STRING, state STRING",
            ", ".join(
                f"({i}, 'Vendor {i}', 'vendor{i}@example.com', '000111222{i:03d}', '98-7654{i:03d}', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {
                "contact_email": _pii("contact_email", "Email", "email"),
                "bank_account": _spii("bank_account", "BankAcct", "keep_last_4"),
                "tax_id": _spii("tax_id", "TaxId", "full"),
                "state": _rf_state(),
            },
        ),
        TableSpec(
            "finance",
            "payroll_runs",
            "finance.payroll",
            True,
            people_cols,
            people_rows,
            people_tags,
        ),
        TableSpec(
            "finance",
            "payroll_deductions",
            "finance.payroll",
            True,
            "id BIGINT, employee_id BIGINT, deduction_type STRING, amount DECIMAL(18,2)",
            ", ".join(f"({i}, {i}, 'FICA', {200 + i * 10}.00)" for i in range(1, 9)),
        ),
        # Assets
        TableSpec("assets", "real_property_sites", "assets.real_property", True, asset_cols, asset_rows, {"state": _rf_state()}),
        TableSpec(
            "assets",
            "real_property_leases",
            "assets.real_property",
            True,
            "id BIGINT, site_code STRING, lessor STRING, annual_rent DECIMAL(18,2), state STRING",
            ", ".join(
                f"({i}, 'SITE-{i}', 'Lessor {i}', {12000 + i * 100}.00, '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec("assets", "fleet_vehicles", "assets.fleet", True, asset_cols, _asset_rows("VEH"), {"state": _rf_state()}),
        TableSpec(
            "assets",
            "fleet_maintenance",
            "assets.fleet",
            True,
            "id BIGINT, vehicle_code STRING, work_order STRING, cost DECIMAL(18,2), state STRING",
            ", ".join(
                f"({i}, 'VEH-{200 + i}', 'WO-{i}', {300 + i * 25}.00, '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec("assets", "equipment_inventory", "assets.equipment", True, asset_cols, _asset_rows("EQP"), {"state": _rf_state()}),
        TableSpec(
            "assets",
            "equipment_assignments",
            "assets.equipment",
            True,
            "id BIGINT, equipment_code STRING, assignee STRING, state STRING",
            ", ".join(
                f"({i}, 'EQP-{200 + i}', 'User {i}', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        # Incidents
        TableSpec(
            "incidents",
            "declarations_master",
            "incidents.declarations",
            True,
            "id BIGINT, disaster_number STRING, state STRING, incident_type STRING, severity DOUBLE",
            incident_rows,
            {"state": _rf_state()},
        ),
        TableSpec(
            "incidents",
            "declarations_funding",
            "incidents.declarations",
            True,
            money_cols,
            _money_rows("DRF"),
            {"state": _rf_state()},
        ),
        TableSpec(
            "incidents",
            "tactical_activities",
            "incidents.tactical",
            True,
            "id BIGINT, activity_code STRING, state STRING, status STRING, cost DECIMAL(18,2)",
            ", ".join(
                f"({i}, 'ACT-{i}', '{STATES[(i - 1) % len(STATES)]}', 'Open', {1000 + i * 40}.00)"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "incidents",
            "tactical_resources",
            "incidents.tactical",
            True,
            asset_cols,
            _asset_rows("RES"),
            {"state": _rf_state()},
        ),
        # Assessments
        TableSpec(
            "assessments",
            "audits_engagements",
            "assessments.audits",
            True,
            "id BIGINT, engagement_id STRING, subject STRING, status STRING, state STRING",
            ", ".join(
                f"({i}, 'AUD-{i}', 'Subject {i}', 'Open', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "assessments",
            "audits_findings",
            "assessments.audits",
            True,
            "id BIGINT, engagement_id STRING, finding STRING, severity STRING, owner_email STRING",
            ", ".join(
                f"({i}, 'AUD-{i}', 'Finding {i}', 'Medium', 'auditor{i}@example.com')"
                for i in range(1, 9)
            ),
            {"owner_email": _pii("owner_email", "Email", "email")},
        ),
        TableSpec(
            "assessments",
            "performance_metrics",
            "assessments.performance",
            True,
            "id BIGINT, metric_name STRING, period STRING, value DOUBLE, state STRING",
            ", ".join(
                f"({i}, 'KPI-{(i % 4) + 1}', 'FY27-Q{(i % 4) + 1}', {50 + i * 1.5}, '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "assessments",
            "performance_reviews",
            "assessments.performance",
            True,
            "id BIGINT, program STRING, score DOUBLE, reviewer_email STRING",
            ", ".join(
                f"({i}, 'Program {i}', {70 + i * 0.8}, 'reviewer{i}@example.com')"
                for i in range(1, 9)
            ),
            {"reviewer_email": _pii("reviewer_email", "Email", "email")},
        ),
        # Authorities
        TableSpec(
            "authorities",
            "policy_documents",
            "authorities.policy",
            True,
            "id BIGINT, doc_id STRING, title STRING, version STRING, owner_email STRING",
            ", ".join(
                f"({i}, 'POL-{i}', 'Policy {i}', '1.{i}', 'policy{i}@example.com')"
                for i in range(1, 9)
            ),
            {"owner_email": _pii("owner_email", "Email", "email")},
        ),
        TableSpec(
            "authorities",
            "policy_directives",
            "authorities.policy",
            True,
            "id BIGINT, directive_id STRING, effective_date DATE, status STRING",
            ", ".join(
                f"({i}, 'DIR-{i}', DATE '2026-0{(i % 9) + 1}-01', 'Active')"
                for i in range(1, 9)
            ),
        ),
        # Communications
        TableSpec(
            "communications",
            "alerts_messages",
            "communications.alerts",
            True,
            "id BIGINT, alert_id STRING, state STRING, channel STRING, body STRING",
            ", ".join(
                f"({i}, 'ALT-{i}', '{STATES[(i - 1) % len(STATES)]}', 'SMS', 'Alert body {i}')"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "communications",
            "alerts_channels",
            "communications.alerts",
            True,
            "id BIGINT, channel STRING, endpoint STRING, owner_email STRING",
            ", ".join(
                f"({i}, 'CH-{(i % 3) + 1}', 'endpoint-{i}', 'comms{i}@example.com')"
                for i in range(1, 8)
            ),
            {"owner_email": _pii("owner_email", "Email", "email")},
        ),
        # External coordination
        TableSpec(
            "external_coordination",
            "mous_agreements",
            "external_coordination.mous",
            True,
            "id BIGINT, mou_id STRING, partner STRING, state STRING, amount DECIMAL(18,2)",
            ", ".join(
                f"({i}, 'MOU-{i}', 'Partner {i}', '{STATES[(i - 1) % len(STATES)]}', {8000 + i * 100}.00)"
                for i in range(1, 9)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "external_coordination",
            "mous_partners",
            "external_coordination.mous",
            True,
            "id BIGINT, partner STRING, contact_email STRING, contact_phone STRING, state STRING",
            ", ".join(
                f"({i}, 'Partner {i}', 'partner{i}@example.com', '555-200-{i:04d}', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 9)
            ),
            {
                "contact_email": _pii("contact_email", "Email", "email"),
                "contact_phone": _pii("contact_phone", "Phone", "phone"),
                "state": _rf_state(),
            },
        ),
        # IT
        TableSpec(
            "information_technology",
            "systems_inventory",
            "information_technology.systems",
            True,
            "id BIGINT, system_name STRING, owner_email STRING, status STRING, criticality STRING",
            ", ".join(
                f"({i}, 'SYS-{i}', 'owner{i}@example.com', 'Online', '{'High' if i % 3 == 0 else 'Medium'}')"
                for i in range(1, 10)
            ),
            {"owner_email": _pii("owner_email", "Email", "email")},
        ),
        TableSpec(
            "information_technology",
            "systems_access_logs",
            "information_technology.systems",
            True,
            "id BIGINT, system_name STRING, user_email STRING, action STRING, event_ts TIMESTAMP",
            ", ".join(
                f"({i}, 'SYS-{(i % 5) + 1}', 'user{i}@example.com', 'LOGIN', TIMESTAMP '2026-11-0{(i % 9) + 1} 10:00:00')"
                for i in range(1, 16)
            ),
            {"user_email": _pii("user_email", "Email", "email")},
        ),
        # Organizations
        TableSpec(
            "organizations",
            "partners_directory",
            "organizations.partners",
            True,
            "id BIGINT, org_name STRING, org_type STRING, state STRING",
            ", ".join(
                f"({i}, 'Org {i}', '{'NGO' if i % 2 else 'SLTT'}', '{STATES[(i - 1) % len(STATES)]}')"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "organizations",
            "partners_contacts",
            "organizations.partners",
            True,
            "id BIGINT, org_name STRING, contact_name STRING, contact_email STRING, contact_phone STRING",
            ", ".join(
                f"({i}, 'Org {i}', 'Contact {i}', 'contact{i}@example.com', '555-300-{i:04d}')"
                for i in range(1, 10)
            ),
            {
                "contact_email": _pii("contact_email", "Email", "email"),
                "contact_phone": _pii("contact_phone", "Phone", "phone"),
            },
        ),
        # Threats and hazards
        TableSpec(
            "threats_and_hazards",
            "planning_scenarios",
            "threats_and_hazards.planning",
            True,
            "id BIGINT, scenario_id STRING, hazard_type STRING, state STRING, risk_score DOUBLE",
            ", ".join(
                f"({i}, 'SCN-{i}', '{'Flood' if i % 2 else 'Wildfire'}', '{STATES[(i - 1) % len(STATES)]}', {1.0 + i * 0.3})"
                for i in range(1, 10)
            ),
            {"state": _rf_state()},
        ),
        TableSpec(
            "threats_and_hazards",
            "planning_risk_scores",
            "threats_and_hazards.planning",
            True,
            "id BIGINT, scenario_id STRING, dimension STRING, score DOUBLE",
            ", ".join(
                f"({i}, 'SCN-{(i % 5) + 1}', 'DIM-{(i % 3) + 1}', {2.0 + i * 0.2})"
                for i in range(1, 12)
            ),
        ),
        # Training
        TableSpec(
            "training_and_exercises",
            "courses_catalog",
            "training_and_exercises.courses",
            True,
            "id BIGINT, course_id STRING, title STRING, audience STRING, hours INT",
            ", ".join(
                f"({i}, 'CRS-{i}', 'Course {i}', '{'Public' if i % 2 else 'Staff'}', {4 + i})"
                for i in range(1, 10)
            ),
        ),
        TableSpec(
            "training_and_exercises",
            "courses_enrollments",
            "training_and_exercises.courses",
            True,
            "id BIGINT, course_id STRING, trainee_email STRING, state STRING, completed BOOLEAN",
            ", ".join(
                f"({i}, 'CRS-{(i % 5) + 1}', 'trainee{i}@example.com', '{STATES[(i - 1) % len(STATES)]}', {str(i % 2 == 0).lower()})"
                for i in range(1, 16)
            ),
            {
                "trainee_email": _pii("trainee_email", "Email", "email"),
                "state": _rf_state(),
            },
        ),
        # One more unreviewed edge case in IT
        TableSpec(
            "information_technology",
            "systems_draft_inventory",
            "information_technology.systems",
            False,
            "id BIGINT, system_name STRING, owner_email STRING, status STRING",
            ", ".join(
                f"({i}, 'DRAFT-SYS-{i}', 'draft{i}@example.com', 'Planned')"
                for i in range(1, 6)
            ),
            {"owner_email": _pii("owner_email", "Email", "email")},
        ),
        # Cross-domain: primary access path is people.employees, but some columns carry
        # sensitivity_category values from other domains (payroll, incidents, training).
        TableSpec(
            CROSS_SCHEMA,
            CROSS_TABLE,
            "people.employees",
            True,
            "employee_id BIGINT, first_name STRING, last_name STRING, email STRING, ssn STRING, "
            "payroll_deposit_account STRING, deduction_type STRING, "
            "deployment_contact_phone STRING, disaster_number STRING, "
            "training_contact_email STRING, state STRING",
            ", ".join(
                f"({i}, '{fn}', '{ln}', '{fn.lower()}.{ln.lower()}@example.com', '123-45-{1000 + i}', "
                f"'00099{i:05d}', 'FICA', '555-400-{i:04d}', 'DR-{4500 + i}', "
                f"'{fn.lower()}.training@example.com', '{STATES[(i - 1) % len(STATES)]}')"
                for i, (fn, ln) in enumerate(
                    [
                        ("Ana", "Lopez"),
                        ("Ben", "Okafor"),
                        ("Chen", "Wu"),
                        ("Dee", "Patel"),
                        ("Eve", "Stone"),
                        ("Raj", "Kumar"),
                        ("Sam", "Nguyen"),
                        ("Tina", "Brooks"),
                    ],
                    start=1,
                )
            ),
            CROSS_COLUMN_TAGS,
        ),
    ]
    return [_resolve_category(t) for t in tables]


def _resolve_category(table: TableSpec) -> TableSpec:
    """Replace the ``@table`` category placeholder with the table's access path."""
    resolved: dict[str, dict[str, str]] = {}
    for column, tags in table.column_tags.items():
        resolved[column] = {
            k: (table.access_value if v == TABLE_CATEGORY else v) for k, v in tags.items()
        }
    return replace(table, column_tags=resolved)


def build_views() -> list[ViewSpec]:
    """Views in ``cross_domain`` used to verify that masks and row filters flow through views."""
    base = f"`{CATALOG}`.`{CROSS_SCHEMA}`.`{CROSS_TABLE}`"
    return [
        # Subset view over the cross-domain table. Masks and row filters come from the base table.
        ViewSpec(
            CROSS_SCHEMA,
            "people_employees_workforce_view",
            "people.employees",
            True,
            f"""SELECT employee_id, first_name, last_name, email, ssn,
                       payroll_deposit_account, deployment_contact_phone,
                       training_contact_email, disaster_number, state
                FROM {base}""",
        ),
        # Join view across real base tables. Each column inherits from its source table.
        ViewSpec(
            CROSS_SCHEMA,
            "people_employees_payroll_join_view",
            "people.employees",
            True,
            f"""SELECT r.id AS employee_id, r.first_name, r.email, r.state,
                       d.deduction_type, d.amount AS deduction_amount
                FROM `{CATALOG}`.`people`.`employees_roster` r
                JOIN `{CATALOG}`.`finance`.`payroll_deductions` d ON d.employee_id = r.id""",
        ),
    ]


# Governance column tags that must never sit on a view column (see ViewSpec).
VIEW_COLUMN_GOVERNANCE_TAGS = (
    "sensitivity_category",
    "sensitivity",
    "data_type",
    "mask_rule",
    "row_filter",
)


def create_views(sql: Any, views: list[ViewSpec]) -> None:
    """Create or replace each view, tag the view object, and clear any governance column tags.

    Args:
        sql: Executor with ``execute(statement)`` and rows from ``SELECT`` statements.
        views: Views to create.
    """
    print("Creating cross-domain views (object tags only; columns inherit from base tables)…")
    for view in views:
        vfqn = f"`{CATALOG}`.`{view.schema}`.`{view.name}`"
        print(f"  [view] {CATALOG}.{view.schema}.{view.name} → {view.access_value}")
        sql.execute(f"CREATE OR REPLACE VIEW {vfqn} AS {view.select_sql}")
        review_value = "reviewed" if view.reviewed else "pending"
        sql.execute(
            f"ALTER VIEW {vfqn} SET TAGS ("
            f"'{ACCESS_TAG}' = '{view.access_value}', '{REVIEW_TAG}' = '{review_value}')"
        )
        clear_view_column_tags(sql, view)


def clear_view_column_tags(sql: Any, view: ViewSpec) -> None:
    """Remove governance column tags left on a view by earlier seed runs.

    Args:
        sql: Executor with ``execute(statement)`` and rows from ``SELECT`` statements.
        view: The view whose columns should carry no governance tags.
    """
    keys = ", ".join(f"'{k}'" for k in VIEW_COLUMN_GOVERNANCE_TAGS)
    rows = sql.execute(
        f"SELECT column_name, tag_name FROM system.information_schema.column_tags "
        f"WHERE catalog_name = '{CATALOG}' AND schema_name = '{view.schema}' "
        f"AND table_name = '{view.name}' AND lower(tag_name) IN ({keys})"
    )
    by_column: dict[str, list[str]] = {}
    for column_name, tag_name in rows:
        by_column.setdefault(str(column_name), []).append(str(tag_name))
    for column, tag_names in sorted(by_column.items()):
        names = ", ".join(f"'{t}'" for t in sorted(tag_names))
        print(f"    [unset] {view.name}.{column}: {', '.join(sorted(tag_names))}")
        sql.execute(
            f"ALTER TABLE `{CATALOG}`.`{view.schema}`.`{view.name}` "
            f"ALTER COLUMN `{column}` UNSET TAGS ({names})"
        )


ACCESS_PATHS = sorted(
    {
        "assessments",
        "assessments.audits",
        "assessments.performance",
        "assets",
        "assets.equipment",
        "assets.fleet",
        "assets.real_property",
        "authorities",
        "authorities.policy",
        "communications",
        "communications.alerts",
        "external_coordination",
        "external_coordination.mous",
        "finance",
        "finance.budget",
        "finance.contracts",
        "finance.grants",
        "finance.payroll",
        "incidents",
        "incidents.declarations",
        "incidents.tactical",
        "information_technology",
        "information_technology.systems",
        "organizations",
        "organizations.partners",
        "people",
        "people.employees",
        "people.first_responders",
        "people.survivors",
        "threats_and_hazards",
        "threats_and_hazards.planning",
        "training_and_exercises",
        "training_and_exercises.courses",
        # keep existing demo values
        "public",
        "non-public",
    }
)

# sensitivity_category mirrors the data_access_control path vocabulary (people.employees, ...).
SENSITIVITY_CATEGORY_VALUES = sorted(set(ACCESS_PATHS))

# sensitivity is the PII / SPII hierarchy; required path = category + "." + sensitivity,
# e.g. people.employees.PII.Name or finance.payroll.SPII.BankAcct.
SENSITIVITY_VALUES = sorted(
    {
        "PII",
        "PII.Name",
        "PII.Email",
        "PII.Phone",
        "PII.Address",
        "SPII",
        "SPII.SSN",
        "SPII.TaxId",
        "SPII.BankAcct",
    }
)


class DatabricksSql:
    def __init__(self, profile: str, warehouse_id: str) -> None:
        self.profile = profile
        self.warehouse_id = warehouse_id

    def execute(self, statement: str, *, wait_timeout: str = "50s") -> list[list[Any]]:
        payload = {
            "warehouse_id": self.warehouse_id,
            "statement": statement,
            "wait_timeout": wait_timeout,
        }
        proc = subprocess.run(
            [
                "databricks",
                "api",
                "post",
                "/api/2.0/sql/statements",
                "-p",
                self.profile,
                "--json",
                json.dumps(payload),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"CLI failed ({proc.returncode}): {proc.stderr or proc.stdout}")
        body = json.loads(proc.stdout)
        status = body.get("status") or {}
        state = status.get("state")
        if state == "PENDING" or state == "RUNNING":
            statement_id = body.get("statement_id")
            body = self._wait(statement_id)
            status = body.get("status") or {}
            state = status.get("state")
        if state != "SUCCEEDED":
            err = (status.get("error") or {}).get("message") or body
            raise RuntimeError(f"SQL failed: {err}\n---\n{statement[:500]}")
        return list((body.get("result") or {}).get("data_array") or [])

    def _wait(self, statement_id: str, timeout_s: int = 180) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            proc = subprocess.run(
                [
                    "databricks",
                    "api",
                    "get",
                    f"/api/2.0/sql/statements/{statement_id}",
                    "-p",
                    self.profile,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            body = json.loads(proc.stdout)
            state = ((body.get("status") or {}).get("state"))
            if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
                return body
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for statement {statement_id}")


def cli_json(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(args, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout or "{}")


def ensure_tag_policy(profile: str, tag_key: str, description: str, values: list[str]) -> None:
    """Create or extend a governed tag policy with the given values."""
    get_args = [
        "databricks",
        "tag-policies",
        "get-tag-policy",
        tag_key,
        "-p",
        profile,
        "-o",
        "json",
    ]
    proc = subprocess.run(get_args, check=False, capture_output=True, text=True)
    value_objs = [{"name": v} for v in sorted(set(values))]
    if proc.returncode != 0:
        create = {
            "tag_key": tag_key,
            "description": description,
            "values": value_objs,
        }
        subprocess.run(
            [
                "databricks",
                "tag-policies",
                "create-tag-policy",
                "--json",
                json.dumps(create),
                "-p",
                profile,
            ],
            check=True,
        )
        print(f"  created tag policy {tag_key} ({len(value_objs)} values)")
        return

    existing = json.loads(proc.stdout)
    current = {v.get("name") for v in (existing.get("values") or []) if v.get("name")}
    merged = sorted(current | set(values))
    if set(merged) == current:
        print(f"  tag policy {tag_key} already has {len(current)} values")
        return
    update = {
        "tag_key": tag_key,
        "description": existing.get("description") or description,
        "values": [{"name": v} for v in merged],
    }
    subprocess.run(
        [
            "databricks",
            "tag-policies",
            "update-tag-policy",
            tag_key,
            "values,description",
            "--json",
            json.dumps(update),
            "-p",
            profile,
        ],
        check=True,
    )
    print(f"  updated tag policy {tag_key}: {len(current)} → {len(merged)} values")


def tag_clause(tags: dict[str, str]) -> str:
    return ", ".join(f"'{k}' = '{v}'" for k, v in tags.items())


def seed(profile: str, warehouse_id: str) -> None:
    tables = build_tables()
    schemas = sorted({t.schema for t in tables})
    print(f"Seeding catalog={CATALOG} schemas={len(schemas)} tables={len(tables)}")
    print("Ensuring governed tag policies…")
    ensure_tag_policy(
        profile,
        ACCESS_TAG,
        "Data-access control path (domain or domain.subdomain)",
        ACCESS_PATHS,
    )
    ensure_tag_policy(
        profile,
        REVIEW_TAG,
        "CLR review status for data-access eligibility",
        ["pending", "reviewed"],
    )
    ensure_tag_policy(
        profile,
        "sensitivity_category",
        "Left side of required sensitivity path",
        SENSITIVITY_CATEGORY_VALUES,
    )
    ensure_tag_policy(
        profile,
        "sensitivity",
        "Right side / leaf of required sensitivity path",
        SENSITIVITY_VALUES,
    )

    sql = DatabricksSql(profile, warehouse_id)
    print("Creating catalog and schemas…")
    sql.execute(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
    sql.execute(
        f"ALTER CATALOG `{CATALOG}` SET TAGS ('{ACCESS_TAG}' = 'public', '{REVIEW_TAG}' = 'reviewed')"
    )
    for schema in schemas:
        sql.execute(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{schema}`")
        # Schema tags seed nesting only; grants still require table-level tags.
        # cross_domain is not a domain path, so it carries no access tag.
        if schema in ACCESS_PATHS:
            sql.execute(
                f"ALTER SCHEMA `{CATALOG}`.`{schema}` SET TAGS ("
                f"'{ACCESS_TAG}' = '{schema}', '{REVIEW_TAG}' = 'reviewed')"
            )

    print("Creating tables, loading rows, applying tags…")
    for i, table in enumerate(tables, start=1):
        fqn = f"`{CATALOG}`.`{table.schema}`.`{table.name}`"
        print(f"  [{i}/{len(tables)}] {CATALOG}.{table.schema}.{table.name} → {table.access_value}"
              f"{'' if table.reviewed else ' (unreviewed)'}")
        sql.execute(f"CREATE TABLE IF NOT EXISTS {fqn} ({table.columns_sql})")
        count_rows = sql.execute(f"SELECT COUNT(*) FROM {fqn}")
        count = int((count_rows[0][0] if count_rows else 0) or 0)
        if count == 0:
            sql.execute(f"INSERT INTO {fqn} VALUES {table.insert_sql}")
        review_value = "reviewed" if table.reviewed else "pending"
        sql.execute(
            f"ALTER TABLE {fqn} SET TAGS ("
            f"'{ACCESS_TAG}' = '{table.access_value}', '{REVIEW_TAG}' = '{review_value}')"
        )
        for column, tags in table.column_tags.items():
            sql.execute(
                f"ALTER TABLE {fqn} ALTER COLUMN `{column}` SET TAGS ({tag_clause(tags)})"
            )

    views = build_views()
    create_views(sql, views)

    reviewed = sum(1 for t in tables if t.reviewed)
    masked = sum(1 for t in tables if any("mask_rule" in tags for tags in t.column_tags.values()))
    filtered = sum(1 for t in tables if any(tags.get("row_filter") for tags in t.column_tags.values()))
    print()
    print("Done.")
    print(f"  tables: {len(tables)} ({reviewed} reviewed, {len(tables) - reviewed} unreviewed)")
    print(f"  views: {len(views)} (cross_domain)")
    print(f"  with column masks: {masked}")
    print(f"  with row filters: {filtered}")
    print(f"  access paths: {len(ACCESS_PATHS)}")
    print()
    print("Next:")
    print("  1. Ensure Identity data_access / sensitivity vocabularies include the new paths")
    print("  2. Assign sensitivity paths like people.employees.PII.Name (and cross-domain ones)")
    print("  3. Run dry-run / apply and measure elapsed time")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Databricks CLI profile")
    parser.add_argument(
        "--warehouse-id",
        default="dd0a656c3ddc883e",
        help="SQL warehouse id (default: Serverless Starter Warehouse on this workspace)",
    )
    args = parser.parse_args()
    try:
        seed(args.profile, args.warehouse_id)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seed the FEMA demo: one catalog per domain, one schema per subdomain, dummy tables.

For every domain in the FEMA access tree (12 domains, 112 subdomains):

  * Catalog ``dga_<domain>`` tagged ``access_path=<domain>``, ``data_access_controlled=true``
  * Schema ``<subdomain>`` tagged ``access_path=<domain>.<subdomain>``
  * Two tables per schema:
      - ``records``: sample rows with sensitive columns (PII / SPII / PHI / FIN / BUS / SEC / LEGAL)
      - ``summary``: aggregate rows with object tags only

Governed tag policies created or extended (values come from the tree and sensitivity model):

  * ``access_path``: every domain and domain.subdomain path (scope for grants and masks)
  * ``sensitivity``: every class (PII) and leaf (PII.Name); required path = access_path.sensitivity
  * ``data_governance_status``: ready (eligibility for grants sync)
  * ``data_access_controlled``: true (catalog enrollment)

Tag keys match the app defaults. ``access_path`` is set on tables, schemas, catalogs, and sensitive
columns; ``sensitivity``, ``data_type`` and ``mask_rule`` on sensitive columns; ``row_filter`` on
``state`` columns.

Re-running is safe: objects are created IF NOT EXISTS, rows are inserted only into empty tables,
and tags are re-applied.

Usage:
  python scripts/seed_data_governance_demo.py --profile <profile>
  python scripts/seed_data_governance_demo.py --profile <profile> --domain people --domain finance
  python scripts/seed_data_governance_demo.py --profile <profile> --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

CATALOG_PREFIX = "dga_"
ACCESS_TAG = "access_path"
SENSITIVITY_TAG = "sensitivity"
REVIEW_TAG = "data_governance_status"
REVIEW_VALUE = "ready"
ENROLLMENT_TAG = "data_access_controlled"
DEFAULT_WAREHOUSE_ID = "792fe1551d747110"
STATES = ("CA", "NY", "TX", "FL", "WA", "CO", "VA", "IL")
ROWS_RECORDS = 10
ROWS_SUMMARY = 4

# FEMA access tree: domain -> subdomains (depth 2). Grants on a domain cover its subdomains.
ACCESS_TREE: dict[str, tuple[str, ...]] = {
    "people": (
        "survivors", "employees", "first_responders", "reservists", "surge_capacity",
        "contractors", "job_applicants", "volunteers", "personnel_security", "workforce_health",
    ),
    "incidents": (
        "declarations", "tactical", "situational_awareness", "preliminary_damage",
        "mission_assignments", "resource_requests", "mass_care", "search_and_rescue",
        "recovery_centers", "operations_centers",
    ),
    "finance": (
        "budget", "payroll", "contracts", "grants", "disaster_relief_fund", "ia_payments",
        "public_assistance", "hazard_mitigation", "nfip_policies", "nfip_claims", "recoupment",
        "charge_cards",
    ),
    "assets": (
        "equipment", "fleet", "real_property", "commodities", "distribution_centers",
        "housing_units", "mobile_response", "facility_security",
    ),
    "authorities": (
        "policy", "statutes_regulations", "directives", "delegations", "legal_counsel",
        "litigation", "foia_privacy", "civil_rights", "ethics", "records_management",
    ),
    "communications": (
        "alerts", "public_affairs", "digital_engagement", "contact_center", "field_outreach",
        "congressional", "internal", "language_access", "emergency_communications",
    ),
    "external_coordination": (
        "mous", "state_territorial", "tribal_nations", "federal_partners",
        "voluntary_organizations", "private_sector", "international", "data_sharing", "mutual_aid",
    ),
    "organizations": (
        "partners", "pa_applicants", "grant_recipients", "nfip_communities", "jurisdictions",
        "regions", "vendors", "fema_offices",
    ),
    "assessments": (
        "audits", "performance", "capability_assessments", "after_action", "program_evaluations",
        "customer_surveys", "improper_payments", "housing_inspections", "internal_controls",
        "enterprise_risk",
    ),
    "information_technology": (
        "systems", "identity_access", "security_operations", "network", "applications",
        "data_platform", "service_desk", "audit_logs",
    ),
    "threats_and_hazards": (
        "planning", "flood_mapping", "risk_index", "hazard_modeling", "thira",
        "threat_information", "critical_infrastructure", "dam_safety", "radiological",
        "resilience_analytics",
    ),
    "training_and_exercises": (
        "courses", "class_schedules", "students", "instructors", "qualifications", "exercises",
        "exercise_evaluations", "learning_content",
    ),
}

# Sensitivity classes and their leaves. Classes are siblings; SPII, PHI and FIN are not under PII.
SENSITIVITY_CLASSES: dict[str, tuple[str, ...]] = {
    "PII": ("Name", "Email", "Phone", "Address", "Geo", "OnlineId", "AgencyId", "Demographic"),
    "SPII": ("SSN", "TaxId", "GovId", "DOB", "Biometric", "Immigration", "Protected", "Criminal"),
    "PHI": ("Medical", "Disability", "InsuranceId", "RecordId"),
    "FIN": ("BankAcct", "Card", "Income", "Credit", "Insurance", "Award"),
    "BUS": ("Procurement", "Proprietary", "Predecisional"),
    "SEC": ("LawEnforcement", "Infrastructure", "ThreatInfo", "Systems", "Secret"),
    "LEGAL": ("Privileged", "Litigation", "Investigation"),
}

# Mask rule per leaf (registry names). Anything not listed uses full redaction.
LEAF_MASK: dict[str, str] = {
    "PII.Email": "email",
    "PII.Phone": "phone",
    "PII.OnlineId": "joinable",
    "PII.AgencyId": "joinable",
    "SPII.SSN": "keep_last_4",
    "SPII.TaxId": "keep_last_4",
    "PHI.RecordId": "joinable",
    "FIN.BankAcct": "keep_last_4",
    "FIN.Card": "keep_last_4",
}

# The sensitive column each domain's records table carries, besides name and email.
DOMAIN_FOCUS: dict[str, tuple[str, str]] = {
    "people": ("ssn", "SPII.SSN"),
    "incidents": ("address", "PII.Address"),
    "finance": ("bank_acct", "FIN.BankAcct"),
    "assets": ("geo_location", "PII.Geo"),
    "authorities": ("privileged_note", "LEGAL.Privileged"),
    "communications": ("contact_phone", "PII.Phone"),
    "external_coordination": ("predecisional_note", "BUS.Predecisional"),
    "organizations": ("agency_id", "PII.AgencyId"),
    "assessments": ("protected_response", "SPII.Protected"),
    "information_technology": ("system_secret", "SEC.Secret"),
    "threats_and_hazards": ("infrastructure_detail", "SEC.Infrastructure"),
    "training_and_exercises": ("disability_need", "PHI.Disability"),
}

# Dummy value formats for leaves that need a realistic shape; others use a generic pattern.
SAMPLE_FORMAT: dict[str, str] = {
    "PII.Name": "Applicant {i:03d}",
    "PII.Email": "applicant{i:03d}@example.com",
    "PII.Phone": "202-555-{i:04d}",
    "PII.Address": "{i} Demo Avenue",
    "SPII.SSN": "900-00-{i:04d}",
    "SPII.DOB": "1980-01-{i:02d}",
    "FIN.BankAcct": "{i:09d}",
    "FIN.Card": "4111-XXXX-XXXX-{i:04d}",
    "PHI.InsuranceId": "MCR{i:09d}",
}


def access_paths() -> list[str]:
    """Every domain and domain.subdomain path in the tree."""
    paths = list(ACCESS_TREE)
    paths += [f"{domain}.{sub}" for domain, subs in ACCESS_TREE.items() for sub in subs]
    return sorted(paths)


def sensitivity_values() -> list[str]:
    """Every sensitivity class and class.leaf."""
    values = list(SENSITIVITY_CLASSES)
    values += [f"{cls}.{leaf}" for cls, leaves in SENSITIVITY_CLASSES.items() for leaf in leaves]
    return sorted(values)


def _sample(leaf: str, i: int) -> str:
    if leaf in SAMPLE_FORMAT:
        return SAMPLE_FORMAT[leaf].format(i=i)
    return f"{leaf.lower().replace('.', '-')}-{i:04d}"


def _sensitive_tags(path: str, leaf: str) -> dict[str, str]:
    return {
        ACCESS_TAG: path,
        SENSITIVITY_TAG: leaf,
        "data_type": "string",
        "mask_rule": LEAF_MASK.get(leaf, "full"),
    }


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns_sql: str
    insert_sql: str
    table_tags: dict[str, str]
    column_tags: dict[str, dict[str, str]] = field(default_factory=dict)


def subdomain_tables(domain: str, sub: str) -> list[TableSpec]:
    """Build the ``records`` and ``summary`` tables for one subdomain."""
    path = f"{domain}.{sub}"
    focus_col, focus_leaf = DOMAIN_FOCUS[domain]
    table_tags = {ACCESS_TAG: path, REVIEW_TAG: REVIEW_VALUE}

    record_rows = [
        f"({i}, '{_sample('PII.Name', i)}', '{_sample('PII.Email', i)}', "
        f"'{_sample(focus_leaf, i)}', '{STATES[(i - 1) % len(STATES)]}', {i * 1250.5})"
        for i in range(1, ROWS_RECORDS + 1)
    ]
    records = TableSpec(
        name="records",
        columns_sql=(
            "record_id BIGINT, full_name STRING, email STRING, "
            f"{focus_col} STRING, state STRING, amount_usd DOUBLE"
        ),
        insert_sql=", ".join(record_rows),
        table_tags=table_tags,
        column_tags={
            "full_name": _sensitive_tags(path, "PII.Name"),
            "email": _sensitive_tags(path, "PII.Email"),
            focus_col: _sensitive_tags(path, focus_leaf),
            "state": {"row_filter": "state"},
        },
    )

    summary_rows = [
        f"('2026-Q{i}', '{STATES[i - 1]}', {i * 12}, {i * 4200.0})"
        for i in range(1, ROWS_SUMMARY + 1)
    ]
    summary = TableSpec(
        name="summary",
        columns_sql="period STRING, state STRING, record_count BIGINT, total_amount DOUBLE",
        insert_sql=", ".join(summary_rows),
        table_tags=table_tags,
        column_tags={"state": {"row_filter": "state"}},
    )
    return [records, summary]


def catalog_name(domain: str) -> str:
    return f"{CATALOG_PREFIX}{domain}"


def tag_clause(tags: dict[str, str]) -> str:
    return ", ".join(f"'{k}' = '{v}'" for k, v in tags.items())


class DatabricksSql:
    """Run SQL through ``databricks api`` so the script needs no SDK install."""

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
        if state in {"PENDING", "RUNNING"}:
            body = self._wait(body.get("statement_id"))
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
            if ((body.get("status") or {}).get("state")) in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
                return body
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for statement {statement_id}")


def ensure_tag_policy(profile: str, tag_key: str, description: str, values: list[str]) -> None:
    """Create or extend a governed tag policy with the given values."""
    proc = subprocess.run(
        ["databricks", "tag-policies", "get-tag-policy", tag_key, "-p", profile, "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    value_objs = [{"name": v} for v in sorted(set(values))]
    if proc.returncode != 0:
        create = {"tag_key": tag_key, "description": description, "values": value_objs}
        subprocess.run(
            ["databricks", "tag-policies", "create-tag-policy", "--json", json.dumps(create), "-p", profile],
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


def ensure_policies(profile: str) -> None:
    print("Ensuring governed tag policies…")
    ensure_tag_policy(
        profile,
        ACCESS_TAG,
        "FEMA access tree: domain or domain.subdomain (scope for grants and masks)",
        access_paths(),
    )
    ensure_tag_policy(
        profile,
        SENSITIVITY_TAG,
        "Sensitivity class (PII) or class.leaf (PII.Name); required path = access_path.sensitivity",
        sensitivity_values(),
    )
    ensure_tag_policy(
        profile,
        REVIEW_TAG,
        "Data governance status for data-access eligibility",
        [REVIEW_VALUE],
    )
    ensure_tag_policy(
        profile,
        ENROLLMENT_TAG,
        "Catalog enrollment for app data-access control",
        ["true"],
    )


def create_catalog(sql: DatabricksSql, domain: str) -> None:
    cat = catalog_name(domain)
    sql.execute(f"CREATE CATALOG IF NOT EXISTS `{cat}`")
    sql.execute(
        f"ALTER CATALOG `{cat}` SET TAGS ("
        f"'{ACCESS_TAG}' = '{domain}', '{REVIEW_TAG}' = '{REVIEW_VALUE}', '{ENROLLMENT_TAG}' = 'true')"
    )


def build_subdomain(sql: DatabricksSql, domain: str, sub: str) -> None:
    """Create the schema and its two tables, then tag them. Safe to re-run."""
    path = f"{domain}.{sub}"
    schema_fqn = f"`{catalog_name(domain)}`.`{sub}`"
    sql.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
    sql.execute(
        f"ALTER SCHEMA {schema_fqn} SET TAGS ("
        f"'{ACCESS_TAG}' = '{path}', '{REVIEW_TAG}' = '{REVIEW_VALUE}')"
    )
    for table in subdomain_tables(domain, sub):
        fqn = f"{schema_fqn}.`{table.name}`"
        sql.execute(f"CREATE TABLE IF NOT EXISTS {fqn} ({table.columns_sql})")
        count_rows = sql.execute(f"SELECT COUNT(*) FROM {fqn}")
        if int((count_rows[0][0] if count_rows else 0) or 0) == 0:
            sql.execute(f"INSERT INTO {fqn} VALUES {table.insert_sql}")
        sql.execute(f"ALTER TABLE {fqn} SET TAGS ({tag_clause(table.table_tags)})")
        for column, tags in table.column_tags.items():
            sql.execute(f"ALTER TABLE {fqn} ALTER COLUMN `{column}` SET TAGS ({tag_clause(tags)})")


def print_plan(domains: list[str]) -> None:
    for domain in domains:
        print(f"{catalog_name(domain)}  (access_path={domain})")
        for sub in ACCESS_TREE[domain]:
            tables = ", ".join(t.name for t in subdomain_tables(domain, sub))
            print(f"  {sub}  (access_path={domain}.{sub}; tables: {tables})")


def seed(profile: str, warehouse_id: str, domains: list[str], workers: int) -> None:
    subs = [(domain, sub) for domain in domains for sub in ACCESS_TREE[domain]]
    print(
        f"Seeding {len(domains)} catalogs, {len(subs)} schemas, "
        f"{len(subs) * 2} tables (prefix {CATALOG_PREFIX})"
    )
    ensure_policies(profile)

    sql = DatabricksSql(profile, warehouse_id)
    print("Creating catalogs…")
    for domain in domains:
        create_catalog(sql, domain)
        print(f"  {catalog_name(domain)}")

    print(f"Creating schemas and tables ({workers} workers)…")
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(build_subdomain, sql, d, s): (d, s) for d, s in subs}
        for done, future in enumerate(as_completed(futures), start=1):
            domain, sub = futures[future]
            label = f"{catalog_name(domain)}.{sub}"
            try:
                future.result()
                print(f"  [{done}/{len(subs)}] {label}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{label}: {exc}")
                print(f"  [{done}/{len(subs)}] {label} FAILED")
    if failures:
        raise RuntimeError(f"{len(failures)} subdomain(s) failed:\n" + "\n".join(failures[:10]))

    print()
    print("Done.")
    print(f"  catalogs: {len(domains)}")
    print(f"  schemas: {len(subs)}")
    print(f"  tables: {len(subs) * 2}")
    print(f"  access paths in policy: {len(access_paths())}")
    print(f"  sensitivity values in policy: {len(sensitivity_values())}")
    print()
    print("Next:")
    print("  1. Identity 'data_access' → governed tag access_path")
    print("  2. Identity 'sensitivity' → governed tags access_path + sensitivity (composed paths)")
    print("  3. Assign grants and sensitivity paths, e.g. people.survivors.PII.Name")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", help="Databricks CLI profile (required unless --dry-run)")
    parser.add_argument(
        "--warehouse-id",
        default=DEFAULT_WAREHOUSE_ID,
        help=f"SQL warehouse id (default: {DEFAULT_WAREHOUSE_ID})",
    )
    parser.add_argument(
        "--domain",
        action="append",
        choices=sorted(ACCESS_TREE),
        help="Limit to one domain (repeat for several). Default: all 12 domains.",
    )
    parser.add_argument("--workers", type=int, default=6, help="Parallel schema builders (default 6)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and exit without changes")
    args = parser.parse_args()

    domains = args.domain or list(ACCESS_TREE)
    if args.dry_run:
        print_plan(domains)
        return 0
    if not args.profile:
        parser.error("--profile is required unless --dry-run is set")
    try:
        seed(args.profile, args.warehouse_id, domains, args.workers)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

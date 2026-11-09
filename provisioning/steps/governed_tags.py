from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound
from databricks.sdk.service.tags import TagPolicy, Value

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")

DEMO_TAG_POLICIES: dict[str, tuple[str, list[str]]] = {
    "dga_domain": ("Governance app demo: business domain", ["finance", "hr"]),
    "dga_subdomain": (
        "Governance app demo: subdomain, path-encoded as domain.subdomain",
        ["finance.budget", "finance.contracts", "hr.payroll"],
    ),
    "dga_sensitivity": (
        "Governance app demo: sensitivity as level.field",
        ["PII.FirstName", "PII.LastName", "PII.Address", "PII.SSN", "FIN.BankAcct"],
    ),
    "dga_row_filter": ("Governance app demo: row filter dimension", ["DR", "REGION"]),
}


class GovernedTagsStep(ProvisioningStep):
    """Creates (or extends) the governed tag policies used by the demo domain."""

    name = "governed_tags"
    demo_only = True

    def run(self, ctx: ProvisioningContext) -> None:
        api = ctx.client.tag_policies
        for key, (description, values) in DEMO_TAG_POLICIES.items():
            try:
                existing = api.get_tag_policy(tag_key=key)
            except NotFound:
                api.create_tag_policy(
                    tag_policy=TagPolicy(
                        tag_key=key, description=description, values=[Value(name=v) for v in values]
                    )
                )
                logger.info("Created governed tag %s", key)
                continue

            current = {v.name for v in existing.values or []}
            missing = [v for v in values if v not in current]
            if missing:
                merged = [Value(name=v) for v in sorted(current | set(values))]
                api.update_tag_policy(
                    tag_key=key,
                    tag_policy=TagPolicy(tag_key=key, description=description, values=merged),
                    update_mask="values",
                )
                logger.info("Added values %s to governed tag %s", missing, key)
            else:
                logger.info("Governed tag %s already up to date", key)

from provisioning.steps.app_permissions import GrantAppPermissionsStep
from provisioning.steps.app_storage import DeltaStorageStep
from provisioning.steps.base import ProvisioningStep
from provisioning.steps.demo_domain import DemoDomainStep
from provisioning.steps.demo_policies import DemoPoliciesStep
from provisioning.steps.governed_tags import GovernedTagsStep

ALL_STEPS: list[type[ProvisioningStep]] = [
    DeltaStorageStep,
    GovernedTagsStep,
    DemoDomainStep,
    DemoPoliciesStep,
    GrantAppPermissionsStep,
]

__all__ = ["ALL_STEPS", "ProvisioningStep"]

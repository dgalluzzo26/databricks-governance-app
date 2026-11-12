from provisioning.steps.admin_sp_permissions import GrantAdminSpPermissionsStep
from provisioning.steps.app_permissions import GrantAppPermissionsStep
from provisioning.steps.app_storage import DeltaStorageStep
from provisioning.steps.base import ProvisioningStep
from provisioning.steps.catalogs import CatalogsStep
from provisioning.steps.masking_functions import MaskingFunctionsStep

ALL_STEPS: list[type[ProvisioningStep]] = [
    CatalogsStep,
    DeltaStorageStep,
    MaskingFunctionsStep,
    GrantAppPermissionsStep,
    GrantAdminSpPermissionsStep,
]

__all__ = ["ALL_STEPS", "ProvisioningStep"]

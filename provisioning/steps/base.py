from __future__ import annotations

from abc import ABC, abstractmethod

from provisioning.context import ProvisioningContext


class ProvisioningStep(ABC):
    """One idempotent unit of setup work. Re-running a step must converge, not duplicate."""

    name: str = "step"

    @abstractmethod
    def run(self, ctx: ProvisioningContext) -> None: ...

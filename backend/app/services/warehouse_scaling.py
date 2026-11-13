"""Raise a SQL warehouse's cluster ceiling for a grants burst, then leave a baseline.

A grants apply runs many small GRANT/REVOKE statements. The warehouse ceiling is raised high
enough that the sync and interactive UI users can share capacity, grant concurrency is sized
from the baseline (not the full burst, so the sync does not fill every cluster), and after the
grants phase the ceiling is set to the baseline so the app always keeps room to scale.
Scaling is best-effort: if the Account Admin SP cannot change the warehouse, the apply still
runs and the reason is recorded on the run profile.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient

from backend.app.services.data_access_profile import note, record_size

logger = logging.getLogger(__name__)

# Concurrent GRANT/REVOKE statements per cluster. Calibrated on a Small serverless warehouse
# (no-op GRANTs, one cluster): throughput ~12/s at 8, ~18/s at 12, ~20/s at 16 with latency
# still near 0.7s. Stage 1 at 16×4=64 raised avg grant latency to ~1.6s, so 12 per cluster is
# the chosen setting. Concurrency uses the baseline cluster count, not the sync burst.
CONCURRENCY_PER_CLUSTER = 12


@dataclass(frozen=True)
class GrantCapacity:
    """Cluster ceiling and statement concurrency for one grants phase."""

    cluster_size: str
    clusters: int
    concurrency: int


def concurrency_for_warehouse(cluster_size: str | None, clusters: int, *, cap: int) -> int:
    """Statements in flight for a warehouse of this size and cluster ceiling.

    Args:
        cluster_size: Warehouse ``cluster_size`` (unused until other sizes are measured).
        clusters: Cluster count used for concurrency (the baseline, not the sync burst).
        cap: Hard upper bound from settings (``grant_concurrency``).
    """
    del cluster_size
    return max(1, min(cap, CONCURRENCY_PER_CLUSTER * max(1, clusters)))


def _normalize_cluster_size(cluster_size: str | None) -> str:
    """Cluster size as a display string (SDK enum values may look like ``ClusterSize.MEDIUM``)."""
    raw = str(cluster_size or "").strip()
    return raw.rsplit(".", 1)[-1].title() if raw else ""


class WarehouseScaler:
    """Raises ``max_num_clusters`` for a grants burst, then leaves the baseline ceiling.

    Args:
        client_provider: Returns the workspace client to change the warehouse with (the
            Account Admin SP in production).
        warehouse_id: Warehouse to scale; scaling is a no-op when unset.
    """

    def __init__(self, client_provider: Callable[[], WorkspaceClient], warehouse_id: str | None) -> None:
        self._client_provider = client_provider
        self._warehouse_id = warehouse_id

    @contextmanager
    def capacity_for(
        self,
        statements: int,
        *,
        enabled: bool,
        sync_max_clusters: int,
        baseline_clusters: int,
        concurrency_cap: int,
    ) -> Iterator[GrantCapacity]:
        """Yield grant concurrency and manage the warehouse ceiling around the grants phase.

        When enabled and there are statements, ``max_num_clusters`` is raised to
        ``sync_max_clusters`` so the sync and UI can share capacity. Grant concurrency is
        ``12 × baseline_clusters`` (capped) so the sync does not fill every cluster. After the
        block, the ceiling is set to ``baseline_clusters`` (not the pre-sync value).

        Args:
            statements: Number of GRANT/REVOKE statements the block will run.
            enabled: When True, change ``max_num_clusters`` for the burst.
            sync_max_clusters: Ceiling during the grants phase (leave headroom for the UI).
            baseline_clusters: Ceiling after the grants phase, and the basis for concurrency.
            concurrency_cap: Hard cap on in-flight statements (settings ``grant_concurrency``).
        """
        restore_to: int | None = None
        if self._warehouse_id:
            capacity, restore_to = self._prepare(
                statements,
                enabled=enabled,
                sync_max_clusters=sync_max_clusters,
                baseline_clusters=baseline_clusters,
                concurrency_cap=concurrency_cap,
            )
        else:
            note("warehouse id unset; grant concurrency falls back to the settings cap")
            capacity = GrantCapacity(
                cluster_size="unknown",
                clusters=max(1, baseline_clusters),
                concurrency=concurrency_for_warehouse(None, baseline_clusters, cap=concurrency_cap),
            )
        record_size("warehouse_clusters_for_grants", capacity.clusters)
        record_size("grant_concurrency_capacity", capacity.concurrency)
        try:
            yield capacity
        finally:
            if restore_to is not None:
                self._set_clusters(restore_to, label="baseline")

    def _prepare(
        self,
        statements: int,
        *,
        enabled: bool,
        sync_max_clusters: int,
        baseline_clusters: int,
        concurrency_cap: int,
    ) -> tuple[GrantCapacity, int | None]:
        """Read the warehouse, optionally raise to the sync ceiling, return (capacity, restore)."""
        try:
            client = self._client_provider()
            current = client.warehouses.get(self._warehouse_id)
        except Exception as exc:  # noqa: BLE001 - scaling must never fail the sync
            logger.warning("could not read warehouse %s: %s", self._warehouse_id, exc)
            note(f"warehouse not resized: {exc}")
            return (
                GrantCapacity(
                    cluster_size="unknown",
                    clusters=max(1, baseline_clusters),
                    concurrency=concurrency_for_warehouse(None, baseline_clusters, cap=concurrency_cap),
                ),
                None,
            )

        size = _normalize_cluster_size(getattr(current, "cluster_size", None))
        before = int(current.max_num_clusters or 1)
        # Sync concurrency uses the baseline so UI users still have clusters to borrow.
        work_clusters = max(1, baseline_clusters)
        sync_ceiling = max(sync_max_clusters, baseline_clusters)
        record_size("warehouse_max_clusters_before", before)
        record_size("warehouse_max_clusters_during", sync_ceiling if enabled and statements > 0 else before)
        record_size("warehouse_baseline_clusters", baseline_clusters)

        restore_to: int | None = None
        during = before
        if enabled and statements > 0:
            if before != sync_ceiling:
                try:
                    self._edit(client, current, sync_ceiling)
                    logger.info(
                        "warehouse %s max_num_clusters %d -> %d for grants (baseline %d, %d statements)",
                        self._warehouse_id,
                        before,
                        sync_ceiling,
                        baseline_clusters,
                        statements,
                    )
                    note(
                        f"warehouse max_num_clusters raised {before} -> {sync_ceiling} "
                        f"for the grants phase (UI headroom; sync uses {work_clusters})"
                    )
                    during = sync_ceiling
                except Exception as exc:  # noqa: BLE001
                    logger.warning("could not raise warehouse %s clusters: %s", self._warehouse_id, exc)
                    note(f"warehouse not resized: {exc}")
                    during = before
            else:
                during = before
                note(f"warehouse max_num_clusters already {before}; not changed")
            # Always leave the warehouse at the app baseline after the grants phase.
            restore_to = baseline_clusters

        concurrency = concurrency_for_warehouse(size, work_clusters, cap=concurrency_cap)
        note(
            f"grant concurrency {concurrency} from {size or 'unknown'} × {work_clusters} "
            f"baseline cluster(s) (cap {concurrency_cap}; ceiling during sync {during})"
        )
        return (
            GrantCapacity(cluster_size=size or "unknown", clusters=work_clusters, concurrency=concurrency),
            restore_to,
        )

    def _set_clusters(self, target: int, *, label: str) -> None:
        """Set ``max_num_clusters`` to ``target``; logs and continues on failure."""
        try:
            client = self._client_provider()
            current = client.warehouses.get(self._warehouse_id)
            before = int(current.max_num_clusters or 1)
            if before == target:
                logger.info(
                    "warehouse %s max_num_clusters already %d (%s)", self._warehouse_id, target, label
                )
                return
            self._edit(client, current, target)
            logger.info(
                "warehouse %s max_num_clusters %d -> %d (%s)",
                self._warehouse_id,
                before,
                target,
                label,
            )
            note(f"warehouse max_num_clusters set to {target} ({label})")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not set warehouse %s clusters to %d (%s): %s",
                self._warehouse_id,
                target,
                label,
                exc,
            )
            note(f"warehouse max_num_clusters not set to {target} ({label}): {exc}")

    def _edit(self, client: WorkspaceClient, current: Any, max_clusters: int) -> None:
        """Send the full warehouse config with only ``max_num_clusters`` changed."""
        client.warehouses.edit(
            self._warehouse_id,
            name=current.name,
            cluster_size=current.cluster_size,
            min_num_clusters=current.min_num_clusters,
            max_num_clusters=max_clusters,
            auto_stop_mins=current.auto_stop_mins,
            enable_serverless_compute=current.enable_serverless_compute,
            warehouse_type=current.warehouse_type,
        )

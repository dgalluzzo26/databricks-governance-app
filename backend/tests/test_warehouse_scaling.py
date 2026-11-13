"""Warehouse cluster scaling and size-based grant concurrency."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import DatabricksError

from backend.app.services.data_access_profile import RunProfiler, profiling
from backend.app.services.warehouse_scaling import WarehouseScaler, concurrency_for_warehouse


class _Warehouses:
    """Warehouse API stand-in that records edits and can fail on demand."""

    def __init__(
        self,
        max_clusters: int | None = 1,
        *,
        cluster_size: str = "Small",
        get_error: Exception | None = None,
    ) -> None:
        self.max_clusters = max_clusters
        self.cluster_size = cluster_size
        self.get_error = get_error
        self.edit_error: Exception | None = None
        self.edits: list[dict[str, Any]] = []

    def get(self, warehouse_id: str):  # noqa: ANN201
        del warehouse_id
        if self.get_error is not None:
            raise self.get_error
        return SimpleNamespace(
            name="governance",
            cluster_size=self.cluster_size,
            min_num_clusters=1,
            max_num_clusters=self.max_clusters,
            auto_stop_mins=10,
            enable_serverless_compute=True,
            warehouse_type="PRO",
        )

    def edit(self, warehouse_id: str, **kwargs: Any) -> None:
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append({"id": warehouse_id, **kwargs})
        self.max_clusters = kwargs["max_num_clusters"]


def _scaler(warehouses: _Warehouses, warehouse_id: str | None = "wh-1") -> WarehouseScaler:
    client = SimpleNamespace(warehouses=warehouses)
    return WarehouseScaler(lambda: client, warehouse_id)


def test_concurrency_is_12_per_baseline_cluster() -> None:
    assert concurrency_for_warehouse("Small", 1, cap=64) == 12
    assert concurrency_for_warehouse("Small", 4, cap=64) == 48
    assert concurrency_for_warehouse("Small", 4, cap=32) == 32


def test_scaler_raises_to_sync_ceiling_and_leaves_baseline() -> None:
    # Sync raises to 8 for UI headroom; concurrency uses baseline 4; after → 4.
    warehouses = _Warehouses(max_clusters=1, cluster_size="Small")
    with _scaler(warehouses).capacity_for(
        2_500,
        enabled=True,
        sync_max_clusters=8,
        baseline_clusters=4,
        concurrency_cap=64,
    ) as capacity:
        assert warehouses.max_clusters == 8
        assert capacity.clusters == 4
        assert capacity.concurrency == 48
    assert warehouses.max_clusters == 4
    assert [e["max_num_clusters"] for e in warehouses.edits] == [8, 4]


def test_scaler_leaves_baseline_even_when_the_block_fails() -> None:
    warehouses = _Warehouses(max_clusters=1, cluster_size="Small")
    try:
        with _scaler(warehouses).capacity_for(
            2_000, enabled=True, sync_max_clusters=8, baseline_clusters=4, concurrency_cap=64
        ):
            raise RuntimeError("apply exploded")
    except RuntimeError:
        pass
    assert warehouses.max_clusters == 4


def test_scaler_still_computes_concurrency_when_autoscale_is_off() -> None:
    warehouses = _Warehouses(max_clusters=1, cluster_size="Small")
    scaler = _scaler(warehouses)
    with scaler.capacity_for(
        5_000, enabled=False, sync_max_clusters=8, baseline_clusters=4, concurrency_cap=64
    ) as capacity:
        assert warehouses.edits == []
        assert capacity.clusters == 4
        assert capacity.concurrency == 48


def test_scaler_sets_baseline_when_ceiling_already_at_sync_max() -> None:
    warehouses = _Warehouses(max_clusters=8, cluster_size="Small")
    with _scaler(warehouses).capacity_for(
        500, enabled=True, sync_max_clusters=8, baseline_clusters=4, concurrency_cap=64
    ) as capacity:
        assert capacity.concurrency == 48
    assert warehouses.max_clusters == 4
    assert [e["max_num_clusters"] for e in warehouses.edits] == [4]


def test_scaler_failures_are_logged_and_the_block_still_runs() -> None:
    ran: list[str] = []
    warehouses = _Warehouses(get_error=DatabricksError("no permission"))
    scaler = _scaler(warehouses)
    with scaler.capacity_for(
        5_000, enabled=True, sync_max_clusters=8, baseline_clusters=4, concurrency_cap=64
    ) as capacity:
        ran.append("read-failure")
        assert capacity.concurrency == 48
    assert ran == ["read-failure"]

    warehouses = _Warehouses(max_clusters=1, cluster_size="Small")
    scaler = _scaler(warehouses)
    profiler = RunProfiler()
    with (
        profiling(profiler),
        scaler.capacity_for(
            2_000, enabled=True, sync_max_clusters=8, baseline_clusters=4, concurrency_cap=64
        ),
    ):
        warehouses.edit_error = DatabricksError("baseline denied")
    assert warehouses.max_clusters == 8
    assert any("not set to 4 (baseline)" in n for n in profiler.notes)
    assert any("raised 1 -> 8" in n for n in profiler.notes)

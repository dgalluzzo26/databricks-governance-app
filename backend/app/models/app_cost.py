"""Models for the app cost report: Databricks Apps compute and per-sync warehouse and job cost."""

from __future__ import annotations

from datetime import date, datetime

from backend.app.models.base import ApiModel


class AppCostDay(ApiModel):
    """List-price cost for one UTC usage day, by billing source (USD).

    Attributes:
        usage_date: UTC day of the usage records.
        app_usd: Always-on Databricks Apps compute for this app.
        warehouse_usd: Dedicated SQL warehouse, all hours of the day.
        job_usd: Data-access sync job compute.
    """

    usage_date: date
    app_usd: float = 0.0
    warehouse_usd: float = 0.0
    job_usd: float = 0.0


class AppCostReport(ApiModel):
    """Cost of running the app, with per-sync averages (list prices, not invoice amounts).

    Attributes:
        available: False when billing could not be read; ``detail`` says why.
        detail: Error text when unavailable.
        app_name: Databricks App name.
        dedicated_warehouse_id: SQL warehouse the app and its sync job use.
        days: Window length in days, ending today (UTC).
        generated_at: When the report was computed.
        today_app_usd: App compute for today so far (partial day).
        app_usd_last_7_days: App compute for the last seven UTC days, today included.
        app_usd_per_active_day: Average app compute per day that has usage.
        sync_count: Finished sync runs (dry-run and apply) in the window.
        sync_usd: Warehouse and job cost that falls inside those runs' start and finish times.
        avg_usd_per_sync: ``sync_usd`` divided by ``sync_count``; None with no syncs.
        unpriced_records: Usage records with no matching list price (counted as $0).
        lag_note: How current billing data is.
        daily: One row per UTC day in the window, oldest first.
    """

    available: bool = True
    detail: str | None = None
    app_name: str
    dedicated_warehouse_id: str | None = None
    days: int
    generated_at: datetime
    today_app_usd: float = 0.0
    app_usd_last_7_days: float = 0.0
    app_usd_per_active_day: float | None = None
    sync_count: int = 0
    sync_usd: float = 0.0
    avg_usd_per_sync: float | None = None
    unpriced_records: int = 0
    lag_note: str
    daily: list[AppCostDay] = []

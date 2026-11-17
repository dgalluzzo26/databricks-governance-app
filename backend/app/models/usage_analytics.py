"""Models for usage analytics: table reads, active users, sensitive columns, and rollups."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.app.models.base import ApiModel


class UsageKpis(ApiModel):
    """Headline usage metrics over a time window."""

    total_table_reads: int = 0
    active_users: int = 0
    tables_accessed: int = 0
    sensitive_column_reads: int = 0
    clear_reads: int = 0
    masked_reads: int = 0
    exempt_reads: int = 0


class UsageLastRun(ApiModel):
    """Metadata from the latest usage rollup run."""

    run_id: str
    status: str
    finished_at: str | None = None
    data_as_of: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    read_events: int = 0
    catalogs: list[str] = Field(default_factory=list)


class UsageDailyPoint(ApiModel):
    """One day of usage metrics for trend charts."""

    date: str
    reads: int = 0
    distinct_users: int = 0
    clear_reads: int = 0
    masked_reads: int = 0


class UsageTableSummary(ApiModel):
    """Aggregated usage for one table in an enrolled catalog."""

    table_full_name: str
    catalog_name: str
    schema_name: str
    table_name: str
    reads: int = 0
    distinct_users: int = 0
    last_read_at: str | None = None


class UsageUserSummary(ApiModel):
    """Aggregated usage for one user across enrolled tables."""

    user_name: str
    reads: int = 0
    distinct_tables: int = 0
    last_read_at: str | None = None


class UsageSensitiveSummary(ApiModel):
    """Aggregated reads for one sensitivity path, with access mode breakdown."""

    sensitivity_path: str
    reads: int = 0
    clear_reads: int = 0
    masked_reads: int = 0
    exempt_reads: int = 0
    distinct_users: int = 0
    distinct_tables: int = 0


class UsageOverview(ApiModel):
    """Full overview payload for the Usage analytics tab."""

    available: bool = True
    detail: str | None = None
    error_code: str | None = None
    days: int = 30
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    kpis: UsageKpis = Field(default_factory=UsageKpis)
    last_run: UsageLastRun | None = None
    daily_trends: list[UsageDailyPoint] = Field(default_factory=list)
    top_tables: list[UsageTableSummary] = Field(default_factory=list)
    top_users: list[UsageUserSummary] = Field(default_factory=list)
    sensitive_breakdown: list[UsageSensitiveSummary] = Field(default_factory=list)


class UsageTableList(ApiModel):
    """List of enrolled tables with usage stats."""

    tables: list[UsageTableSummary] = Field(default_factory=list)
    total: int = 0


class UsageUserList(ApiModel):
    """List of users with enrolled table usage stats."""

    users: list[UsageUserSummary] = Field(default_factory=list)
    total: int = 0


class UsageColumnSummary(ApiModel):
    """Column-level usage breakdown for a single table."""

    column_name: str
    sensitivity_path: str | None = None
    access_mode: str
    reads: int = 0
    distinct_users: int = 0
    last_read_at: str | None = None


class UsageUserTableSummary(ApiModel):
    """Usage of one table by one user."""

    user_name: str
    reads: int = 0
    last_read_at: str | None = None


class UsageRecentReadEvent(ApiModel):
    """A recent read statement against a table."""

    event_time: str
    user_name: str
    entity_type: str | None = None
    direct_access: bool = True


class UsageTableDetail(ApiModel):
    """Drilldown view for a single table."""

    table_full_name: str
    days: int = 30
    reads: int = 0
    distinct_users: int = 0
    last_read_at: str | None = None
    users: list[UsageUserTableSummary] = Field(default_factory=list)
    columns: list[UsageColumnSummary] = Field(default_factory=list)
    recent_reads: list[UsageRecentReadEvent] = Field(default_factory=list)


class UsageRefreshResponse(ApiModel):
    """Status returned when an admin requests a usage rollup refresh."""

    started: bool
    message: str
    job_id: str | None = None
    run_id: str | None = None


class AccessGrant(ApiModel):
    """One grant behind a holder: a direct USER assignment or a GROUP the user belongs to."""

    via_type: str
    via_principal: str
    held_value: str


class AccessHolder(ApiModel):
    """One user who holds access to a governed table, with reads in the window."""

    user_name: str
    grants: list[AccessGrant] = Field(default_factory=list)
    reads: int = 0
    last_read_at: str | None = None
    used: bool = False


class AccessTableSummary(ApiModel):
    """Access vs usage for one governed table."""

    table_full_name: str
    catalog_name: str
    schema_name: str
    table_name: str
    tag_value: str
    holders: int = 0
    holders_with_reads: int = 0
    unused_holders: int = 0
    readers_without_access: int = 0
    reads: int = 0
    last_read_at: str | None = None


class AccessTableList(ApiModel):
    """Governed tables with access and usage counts."""

    as_of: str | None = None
    days: int = 30
    tables: list[AccessTableSummary] = Field(default_factory=list)
    total: int = 0


class AccessTableDetail(ApiModel):
    """Holders and reader gaps for one governed table."""

    table_full_name: str
    tag_value: str = ""
    days: int = 30
    as_of: str | None = None
    holders: list[AccessHolder] = Field(default_factory=list)
    readers_without_access: list[UsageUserTableSummary] = Field(default_factory=list)


class AccessUnusedItem(ApiModel):
    """A holder with no reads in the window on one governed table."""

    table_full_name: str
    tag_value: str = ""
    user_name: str
    grants: list[AccessGrant] = Field(default_factory=list)


class AccessUnusedList(ApiModel):
    """Holders with unused access."""

    days: int = 30
    total: int = 0
    items: list[AccessUnusedItem] = Field(default_factory=list)


class AccessNoAccessItem(ApiModel):
    """A user with reads on a governed table who holds no assigned grant there."""

    table_full_name: str
    user_name: str
    reads: int = 0
    last_read_at: str | None = None


class AccessNoAccessList(ApiModel):
    """Reads without assigned access."""

    days: int = 30
    total: int = 0
    items: list[AccessNoAccessItem] = Field(default_factory=list)

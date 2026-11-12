"""Models for tag-driven data-access groups, dry-run plans, and sync runs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, computed_field, field_validator

from backend.app.models.base import ApiModel

MIN_SYNC_INTERVAL_MINUTES = 5
DEFAULT_SYNC_INTERVAL_MINUTES = 15
# Sample rows kept on the plan for UI inspection; full counts live on the summary.
PLAN_ITEM_LIMIT = 25
# Persist enough history for the UI's 30-day window (paginated); prune older on write.
RUN_HISTORY_LIMIT = 500
RUN_HISTORY_RETENTION_DAYS = 30
RUN_HISTORY_PAGE_SIZE_DEFAULT = 20
RUN_HISTORY_PAGE_SIZE_MAX = 50


class ConflictPolicy(StrEnum):
    """Legacy setting kept for saved configs; SELECT is table-only so conflicts are unused.

    Attributes:
        HOLD: Historical fail-closed label; no longer changes the plan.
        WARN: Historical warn label; no longer changes the plan.
    """

    HOLD = "hold"
    WARN = "warn"


class SyncTrigger(StrEnum):
    """Who started a sync run."""

    SCHEDULE = "schedule"
    USER = "user"


class ScheduleMode(StrEnum):
    """What the background scheduler does each interval when enabled.

    Attributes:
        DRY_RUN: Plan only (no UC mutations).
        APPLY: Inventory → plan → mutate groups/membership/grants (same as Live apply).
    """

    DRY_RUN = "dry_run"
    APPLY = "apply"


class SyncRunStatus(StrEnum):
    """Lifecycle of a dry-run (or later apply) sync."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class DataAccessSettings(ApiModel):
    """Admin-editable configuration for the data-access group sync.

    Attributes:
        access_tag_key: Governed tag whose values map to ``dac_*`` groups.
        review_tag_key: Tag that must also be present on the same object.
        review_tag_value: Required value of ``review_tag_key`` (e.g. ``reviewed``).
        group_prefix: Prefix for managed group names; values are slugified after it.
        hierarchy_separator: Splits an access value into parent prefixes for nested groups.
        enrollment_catalog_tag_key: Catalog tag that enrolls a catalog in the governance perimeter.
        enrollment_catalog_tag_value: Required value of ``enrollment_catalog_tag_key``.
        allowed_catalogs: Deprecated; ignored. Perimeter is enrollment-tag discovery only.
        data_privileges: Privileges granted on the tagged securable (traversal is added).
        interval_minutes: How often the in-app scheduler runs when enabled.
        schedule_enabled: When false, the background scheduler is paused.
        schedule_mode: ``dry_run`` (plan only) or ``apply`` (plan then mutate UC).
        conflict_policy: Legacy field; parent/child tag mismatches are not reported.
        dry_run: Preferred mode for the settings form (legacy; schedule uses schedule_mode).
    """

    access_tag_key: str = "data_access_control"
    review_tag_key: str = "clr_review_status"
    review_tag_value: str = "reviewed"
    group_prefix: str = "dac_"
    hierarchy_separator: str = "."
    enrollment_catalog_tag_key: str = "data_access_controlled"
    enrollment_catalog_tag_value: str = "true"
    allowed_catalogs: list[str] = Field(default_factory=list)
    data_privileges: list[str] = Field(default_factory=lambda: ["SELECT"])
    interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES
    schedule_enabled: bool = True
    schedule_mode: ScheduleMode = ScheduleMode.DRY_RUN
    conflict_policy: ConflictPolicy = ConflictPolicy.HOLD
    dry_run: bool = True

    @field_validator(
        "access_tag_key",
        "review_tag_key",
        "review_tag_value",
        "group_prefix",
        "enrollment_catalog_tag_key",
        "enrollment_catalog_tag_value",
    )
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        """Reject blank tag keys, review values, and group prefixes."""
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("hierarchy_separator")
    @classmethod
    def _single_separator(cls, value: str) -> str:
        """Require a single-character hierarchy separator."""
        text = value.strip()
        if len(text) != 1:
            raise ValueError("hierarchy_separator must be a single character")
        return text

    @field_validator("interval_minutes")
    @classmethod
    def _min_interval(cls, value: int) -> int:
        """Clamp the scheduler interval to the configured minimum."""
        if value < MIN_SYNC_INTERVAL_MINUTES:
            raise ValueError(f"interval_minutes must be at least {MIN_SYNC_INTERVAL_MINUTES}")
        return value

    @field_validator("allowed_catalogs")
    @classmethod
    def _clean_catalogs(cls, values: list[str]) -> list[str]:
        """Strip blanks and drop empty catalog names."""
        return [v.strip() for v in values if v and v.strip()]

    @field_validator("data_privileges")
    @classmethod
    def _clean_privileges(cls, values: list[str]) -> list[str]:
        """Upper-case privilege names and require at least one."""
        cleaned = [v.strip().upper() for v in values if v and v.strip()]
        if not cleaned:
            raise ValueError("data_privileges must not be empty")
        return cleaned


class DataAccessSettingsView(ApiModel):
    """Settings plus constants the UI needs to render the form.

    Attributes:
        settings: Current persisted (or default) settings.
        min_interval_minutes: Smallest interval the API will accept.
        apply_available: True when live apply is implemented (still gated by readiness).
        enrolled_catalogs: Catalogs currently carrying the enrollment tag (from UC).
    """

    settings: DataAccessSettings
    min_interval_minutes: int = MIN_SYNC_INTERVAL_MINUTES
    apply_available: bool = True
    enrolled_catalogs: list[str] = Field(default_factory=list)


class RunDataAccessRequest(ApiModel):
    """Body for ``POST /data-access/runs``.

    Attributes:
        mode: ``dry_run`` plans only; ``apply`` mutates groups, membership, and grants.
    """

    mode: str = "dry_run"

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        text = (value or "dry_run").strip().lower()
        if text not in {"dry_run", "apply"}:
            raise ValueError("mode must be dry_run or apply")
        return text


class ReadinessCheck(ApiModel):
    """One Account Admin SP / warehouse capability probe.

    Attributes:
        id: Stable machine id (e.g. ``credentials``, ``read_tags``).
        label: Short label for App Status.
        ok: True when the probe succeeded.
        required_for: ``dry_run`` or ``apply``.
        detail: Success note or the error text.
    """

    id: str
    label: str
    ok: bool
    required_for: str
    detail: str | None = None


class DataAccessReadiness(ApiModel):
    """Rolled-up Account Admin SP readiness for data-access sync.

    Attributes:
        dry_run_ready: All probes required to inventory tags and plan grants passed.
        apply_ready: All probes required to create groups and GRANT/REVOKE passed.
        checks: Individual Account Admin SP / warehouse probe results.
        setup_checks: Identity definition setup (sensitivity + data-access).
        setup_ready: True when both required Identity definitions are configured.
    """

    dry_run_ready: bool
    apply_ready: bool
    checks: list[ReadinessCheck]
    setup_checks: list[ReadinessCheck] = Field(default_factory=list)
    setup_ready: bool = False


class PlannedGroup(ApiModel):
    """A managed group the dry-run would create or reuse.

    Attributes:
        name: Unity Catalog / account group display name.
        value: Access-tag value this group represents.
        parent_group: Group that should be nested into this one, if any.
        exists: True when the group already exists in the account.
    """

    name: str
    value: str
    parent_group: str | None = None
    exists: bool = False


class PlannedGrant(ApiModel):
    """One privilege on one securable for one managed group.

    Attributes:
        securable_type: ``catalog``, ``schema``, or ``table``.
        full_name: Catalog, ``catalog.schema``, or ``catalog.schema.table``.
        principal: Managed group name.
        privilege: Privilege to add or revoke.
        reason: Why this grant is in the plan.
    """

    securable_type: str
    full_name: str
    principal: str
    privilege: str
    reason: str = ""


class MembershipAction(StrEnum):
    """Whether a principal should be added to or removed from a managed group."""

    ADD = "add"
    REMOVE = "remove"


class MembershipChange(ApiModel):
    """One Identity-driven membership change for a managed ``dac_*`` group.

    Attributes:
        group: Managed group display name.
        principal_name: User name, group name, or SP application id.
        principal_type: ``USER``, ``GROUP``, or ``SERVICE_PRINCIPAL``.
        action: Add or remove.
        reason: Why this change is in the plan.
    """

    group: str
    principal_name: str
    principal_type: str
    action: MembershipAction
    reason: str = ""


class DataAccessConflict(ApiModel):
    """An ancestor grant that would expose a descendant the tags do not allow.

    Attributes:
        ancestor_type: Securable type of the tagged ancestor.
        ancestor_name: Full name of the ancestor.
        ancestor_value: Access-tag value on the ancestor.
        descendant_name: Full name of the conflicting child.
        descendant_value: Access-tag value on the child, if any.
        reason: Human-readable explanation.
    """

    ancestor_type: str
    ancestor_name: str
    ancestor_value: str
    descendant_name: str
    descendant_value: str | None = None
    reason: str


class DataAccessPlanSummary(ApiModel):
    """Counts for a dry-run plan, used in history lists and KPIs.

    Attributes:
        eligible_objects: Tagged+reviewed objects that would receive grants.
        skipped_unreviewed: Objects with the access tag but without the review value.
        unique_values: Distinct access-tag values on eligible objects.
        groups_to_create: Managed groups that do not exist yet.
        groups_existing: Managed groups that already exist.
        grants_to_add: Privileges present in desired state but not in Unity Catalog.
        grants_to_revoke: Managed-prefix privileges in Unity Catalog but not desired.
        grants_unchanged: Desired privileges already present.
        members_to_add: Identity principals missing from their ``dac_*`` group.
        members_to_remove: Stale user/SP members not backed by an active assignment.
        conflicts: Always zero; retained for plan/summary shape compatibility.
        invalid_values: Access values that cannot become group names.
    """

    eligible_objects: int = 0
    skipped_unreviewed: int = 0
    unique_values: int = 0
    groups_to_create: int = 0
    groups_existing: int = 0
    grants_to_add: int = 0
    grants_to_revoke: int = 0
    grants_unchanged: int = 0
    members_to_add: int = 0
    members_to_remove: int = 0
    conflicts: int = 0
    invalid_values: int = 0


class DataAccessPlan(ApiModel):
    """Full dry-run / apply plan (truncated lists plus complete summary counts).

    Attributes:
        summary: Counts for every category.
        fingerprint: Hash of the desired grant/group/membership set; used to detect drift.
        groups: Groups the sync would create or reuse (truncated).
        grants_to_add: Missing privileges (truncated).
        grants_to_revoke: Extra managed-prefix privileges (truncated).
        membership_changes: Identity-driven member add/remove (truncated).
        conflicts: Empty; SELECT is table-only so parent/child mismatches are not conflicts.
        invalid_values: Values that could not be turned into group names.
        truncated: True when any list was cut at ``PLAN_ITEM_LIMIT``.
        drift_since_last_run: Human-readable changes versus the previous successful plan.
        apply_errors: Per-step errors collected during apply (empty for dry-run).
    """

    summary: DataAccessPlanSummary
    fingerprint: str
    groups: list[PlannedGroup] = Field(default_factory=list)
    grants_to_add: list[PlannedGrant] = Field(default_factory=list)
    grants_to_revoke: list[PlannedGrant] = Field(default_factory=list)
    membership_changes: list[MembershipChange] = Field(default_factory=list)
    conflicts: list[DataAccessConflict] = Field(default_factory=list)
    invalid_values: list[str] = Field(default_factory=list)
    truncated: bool = False
    drift_since_last_run: list[str] = Field(default_factory=list)
    apply_errors: list[str] = Field(default_factory=list)


class DataAccessRun(ApiModel):
    """One stored sync execution (dry-run or apply).

    Attributes:
        run_id: Unique identifier.
        status: Running, success, or failed.
        trigger: Schedule or an admin clicking Run now.
        mode: ``dry_run`` or ``apply``.
        started_at: When the run began.
        finished_at: When it finished; None while running.
        triggered_by: User name or ``scheduler``.
        error: Failure message when status is failed.
        summary: Plan counts when the planner finished.
        plan: Truncated plan; omitted from history list entries.
        fingerprint: Desired-state hash from the plan.
        duration_seconds: Elapsed wall time when finished; omitted from stored JSON.
    """

    run_id: str
    status: SyncRunStatus
    trigger: SyncTrigger
    mode: str = "dry_run"
    started_at: datetime
    finished_at: datetime | None = None
    triggered_by: str
    error: str | None = None
    summary: DataAccessPlanSummary | None = None
    plan: DataAccessPlan | None = None
    fingerprint: str | None = None

    @computed_field
    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock seconds from start to finish, or None while the run is in progress."""
        if self.finished_at is None:
            return None
        return round(max(0.0, (self.finished_at - self.started_at).total_seconds()), 1)


class DataAccessRunList(ApiModel):
    """Paginated sync history for the last retention window.

    Attributes:
        runs: Page of runs (summaries only; no full plans).
        total: Total matching runs in the retention window.
        page: 1-based page index.
        page_size: Page size used for this response.
        mode: Mode filter applied (``dry_run``, ``apply``), or None for all.
        retention_days: How many days of history are retained / returned.
    """

    runs: list[DataAccessRun] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = RUN_HISTORY_PAGE_SIZE_DEFAULT
    mode: str | None = None
    retention_days: int = RUN_HISTORY_RETENTION_DAYS

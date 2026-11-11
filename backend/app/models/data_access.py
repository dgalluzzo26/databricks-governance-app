"""Models for tag-driven data-access groups, dry-run plans, and sync runs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel

MIN_SYNC_INTERVAL_MINUTES = 5
DEFAULT_SYNC_INTERVAL_MINUTES = 15
PLAN_ITEM_LIMIT = 200
RUN_HISTORY_LIMIT = 20


class ConflictPolicy(StrEnum):
    """How the planner treats an ancestor grant that would over-expose descendants.

    Attributes:
        HOLD: Omit the ancestor grant from the desired set (fail closed).
        WARN: Keep the ancestor grant and still report the conflict.
    """

    HOLD = "hold"
    WARN = "warn"


class SyncTrigger(StrEnum):
    """Who started a sync run."""

    SCHEDULE = "schedule"
    USER = "user"


class SyncRunStatus(StrEnum):
    """Lifecycle of a dry-run (or later apply) sync."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class DataAccessSettings(ApiModel):
    """Admin-editable configuration for the data-access group sync.

    Apply mode is not available yet: ``dry_run`` is always true regardless of input.

    Attributes:
        access_tag_key: Governed tag whose values map to ``dac_*`` groups.
        review_tag_key: Tag that must also be present on the same object.
        review_tag_value: Required value of ``review_tag_key`` (e.g. ``reviewed``).
        group_prefix: Prefix for managed group names; values are slugified after it.
        hierarchy_separator: Splits an access value into parent prefixes for nested groups.
        allowed_catalogs: Catalogs to scan; empty means every catalog except system catalogs.
        data_privileges: Privileges granted on the tagged securable (traversal is added).
        interval_minutes: How often the in-app scheduler runs a dry-run.
        conflict_policy: Hold or warn when an ancestor grant would over-expose descendants.
        dry_run: When true, the sync never creates groups or changes Unity Catalog grants.
    """

    access_tag_key: str = "data_access_control"
    review_tag_key: str = "clr_review_status"
    review_tag_value: str = "reviewed"
    group_prefix: str = "dac_"
    hierarchy_separator: str = "."
    allowed_catalogs: list[str] = Field(default_factory=list)
    data_privileges: list[str] = Field(default_factory=lambda: ["SELECT"])
    interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES
    conflict_policy: ConflictPolicy = ConflictPolicy.HOLD
    dry_run: bool = True

    @field_validator("access_tag_key", "review_tag_key", "review_tag_value", "group_prefix")
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
        apply_available: False until apply mode is implemented.
    """

    settings: DataAccessSettings
    min_interval_minutes: int = MIN_SYNC_INTERVAL_MINUTES
    apply_available: bool = False


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
        checks: Individual probe results.
    """

    dry_run_ready: bool
    apply_ready: bool
    checks: list[ReadinessCheck]


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
        conflicts: Ancestor/descendant tag mismatches.
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
    conflicts: int = 0
    invalid_values: int = 0


class DataAccessPlan(ApiModel):
    """Full dry-run result (truncated lists plus complete summary counts).

    Attributes:
        summary: Counts for every category.
        fingerprint: Hash of the desired grant/group set; used to detect drift.
        groups: Groups the sync would create or reuse (truncated).
        grants_to_add: Missing privileges (truncated).
        grants_to_revoke: Extra managed-prefix privileges (truncated).
        conflicts: Over-grant risks (truncated).
        invalid_values: Values that could not be turned into group names.
        truncated: True when any list was cut at ``PLAN_ITEM_LIMIT``.
        drift_since_last_run: Human-readable changes versus the previous successful plan.
    """

    summary: DataAccessPlanSummary
    fingerprint: str
    groups: list[PlannedGroup] = Field(default_factory=list)
    grants_to_add: list[PlannedGrant] = Field(default_factory=list)
    grants_to_revoke: list[PlannedGrant] = Field(default_factory=list)
    conflicts: list[DataAccessConflict] = Field(default_factory=list)
    invalid_values: list[str] = Field(default_factory=list)
    truncated: bool = False
    drift_since_last_run: list[str] = Field(default_factory=list)


class DataAccessRun(ApiModel):
    """One stored dry-run execution.

    Attributes:
        run_id: Unique identifier.
        status: Running, success, or failed.
        trigger: Schedule or an admin clicking Run now.
        mode: Always ``dry_run`` until apply exists.
        started_at: When the run began.
        finished_at: When it finished; None while running.
        triggered_by: User name or ``scheduler``.
        error: Failure message when status is failed.
        summary: Plan counts when the planner finished.
        plan: Truncated plan; omitted from history list entries.
        fingerprint: Desired-state hash from the plan.
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

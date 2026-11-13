"""Friendly names and expected elapsed times for every API route.

Each request is attributed to an activity by its method and route template (for example
``POST /api/identity/access-requests/{request_id}/approve``). ``expected_ms`` is the time
the activity should normally finish within; slower requests are counted as slow by the
health monitor. Unknown routes still get timed, with a generic name and a default threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_EXPECTED_MS = 3000.0


@dataclass(frozen=True)
class Activity:
    """One named user-facing activity.

    Attributes:
        name: Friendly label shown in the health monitor.
        expected_ms: Threshold above which a request counts as slow.
    """

    name: str
    expected_ms: float


# (method, route template, friendly name, expected milliseconds)
_CATALOG: tuple[tuple[str, str, str, float], ...] = (
    ("GET", "/api/app-info", "Load app info", 1500),
    ("GET", "/api/me", "Load signed-in user", 1000),
    ("GET", "/api/directory/principals", "Search principals", 2000),
    ("GET", "/api/access-explorer/search", "Search tables (Access explorer)", 3000),
    ("GET", "/api/access-explorer/tree/catalogs", "Browse catalogs", 2000),
    ("GET", "/api/access-explorer/tree/schemas", "Browse schemas", 2000),
    ("GET", "/api/access-explorer/tree/tables", "Browse tables", 2000),
    ("POST", "/api/access-explorer/analyze", "Analyze table access", 5000),
    ("POST", "/api/attribute-explorer/analyze", "Analyze attribute holders", 5000),
    ("GET", "/api/identity/governed-tags", "Load governed tags", 2000),
    ("GET", "/api/identity/definitions", "Load attribute definitions", 1500),
    ("PUT", "/api/identity/definitions", "Save attribute definition", 3000),
    ("DELETE", "/api/identity/definitions/{key}", "Delete attribute definition", 3000),
    ("GET", "/api/identity/assignments", "List identity assignments", 2000),
    ("GET", "/api/identity/assignments/me", "My identity assignments", 1500),
    ("POST", "/api/identity/assignments", "Assign identity attribute", 5000),
    ("POST", "/api/identity/assignments/{assignment_id}/revoke", "Revoke identity assignment", 5000),
    ("POST", "/api/identity/sync-uc", "Sync identity attributes to Unity Catalog", 60000),
    ("GET", "/api/identity/category-approvers", "Load category approvers", 1500),
    ("PUT", "/api/identity/category-approvers", "Save category approver", 2000),
    ("DELETE", "/api/identity/category-approvers/{approver_id}", "Delete category approver", 2000),
    ("POST", "/api/identity/access-requests", "Submit access request", 3000),
    ("GET", "/api/identity/access-requests/mine", "My access requests", 1500),
    ("POST", "/api/identity/access-requests/{request_id}/cancel", "Cancel access request", 2000),
    ("GET", "/api/identity/approvals", "Load approvals inbox", 2000),
    ("POST", "/api/identity/access-requests/{request_id}/approve", "Approve permission request", 5000),
    ("POST", "/api/identity/access-requests/{request_id}/deny", "Deny permission request", 3000),
    ("POST", "/api/identity/renewals/{assignment_id}/approve", "Approve permission renewal", 5000),
    ("POST", "/api/identity/renewals/{assignment_id}/dismiss", "Dismiss permission renewal", 2000),
    ("GET", "/api/mask-rules", "Load mask rules", 2000),
    ("PUT", "/api/mask-rules", "Save mask rule", 3000),
    ("DELETE", "/api/mask-rules/{rule_key}", "Delete mask rule", 3000),
    ("POST", "/api/mask-rules/seed", "Seed mask rules", 5000),
    ("GET", "/api/mask-rules/pack", "Load mask policy pack", 3000),
    ("POST", "/api/mask-rules/pack/ensure", "Ensure mask policy pack", 60000),
    ("POST", "/api/mask-rules/sync-uc", "Sync mask rules to Unity Catalog", 60000),
    ("POST", "/api/mask-rules/salt/rotate", "Rotate joinable salt", 5000),
    ("POST", "/api/mask-rules/handlers/draft", "Draft mask handler", 5000),
    ("POST", "/api/mask-rules/handlers/validate", "Validate mask handler (runs SQL)", 15000),
    ("GET", "/api/row-filters/dimensions", "Load row-filter dimensions", 2000),
    ("PUT", "/api/row-filters/dimensions", "Save row-filter dimension", 3000),
    ("DELETE", "/api/row-filters/dimensions/{key}", "Delete row-filter dimension", 3000),
    ("POST", "/api/row-filters/dimensions/{key}/refresh", "Refresh row-filter dimension values", 15000),
    ("POST", "/api/row-filters/seed-preview", "Preview row-filter seed", 5000),
    ("GET", "/api/row-filters/pack", "Load row-filter policy pack", 3000),
    ("POST", "/api/row-filters/pack/ensure", "Ensure row-filter policy pack", 60000),
    ("GET", "/api/row-filters/attachments", "Load row-filter attachments", 2000),
    ("POST", "/api/row-filters/attachments", "Attach row filter", 15000),
    ("DELETE", "/api/row-filters/attachments", "Detach row filter", 15000),
    ("GET", "/api/help/docs", "Load help docs", 1000),
    ("GET", "/api/help/docs/{slug}", "Open help doc", 1000),
    ("GET", "/api/help/flows", "Load help flows", 1000),
    ("GET", "/api/help/api-reference", "Load API reference", 1000),
    ("GET", "/api/policies", "List policies", 3000),
    ("GET", "/api/policies/{policy_id}", "Open policy", 3000),
    ("GET", "/api/policy-exceptions", "Load policy exceptions", 2000),
    ("PUT", "/api/policy-exceptions", "Save policy exception", 3000),
    ("DELETE", "/api/policy-exceptions/{exception_id}", "Delete policy exception", 3000),
    ("POST", "/api/policy-exceptions/apply", "Apply policy exceptions", 60000),
    ("GET", "/api/data-access/settings", "Load data-access settings", 1500),
    ("PUT", "/api/data-access/settings", "Save data-access settings", 5000),
    ("GET", "/api/data-access/readiness", "Check data-access readiness", 15000),
    ("GET", "/api/data-access/runs", "List data-access runs", 2000),
    ("GET", "/api/data-access/runs/latest", "Latest data-access run", 2000),
    ("GET", "/api/data-access/runs/{run_id}", "Open data-access run", 2000),
    ("POST", "/api/data-access/runs", "Run data-access sync (dry run or apply)", 120000),
    ("GET", "/api/data-access/analytics", "Load data-access analytics", 3000),
    ("GET", "/api/data-access/analytics/runs", "Load data-access run history view", 2000),
    ("GET", "/api/data-access/analytics/grants", "Load data-access grant changes view", 2000),
    ("GET", "/api/recovery/settings", "Load recovery settings", 1500),
    ("PUT", "/api/recovery/settings", "Save recovery settings", 3000),
    ("POST", "/api/recovery/export/preview", "Preview recovery export", 15000),
    ("POST", "/api/recovery/export/push", "Push recovery export to Git", 60000),
    ("POST", "/api/recovery/import", "Preview recovery import", 30000),
    ("POST", "/api/recovery/recover", "Apply recovery import", 120000),
    ("GET", "/api/performance/summary", "Load performance summary", 5000),
    ("GET", "/api/performance/slow-requests", "Load slow requests", 3000),
    ("GET", "/api/performance/capacity", "Read app, Lakebase and warehouse sizing", 15000),
)

_BY_ROUTE: dict[tuple[str, str], Activity] = {
    (method, template): Activity(name=name, expected_ms=expected)
    for method, template, name, expected in _CATALOG
}


def activity_for(method: str, route_template: str) -> Activity:
    """Return the catalog entry for a route, or a generic entry for unlisted routes.

    Args:
        method: HTTP method.
        route_template: Route template, e.g. ``/api/policies/{policy_id}``.

    Returns:
        The named activity with its expected time.
    """
    found = _BY_ROUTE.get((method.upper(), route_template))
    if found is not None:
        return found
    return Activity(name=f"{method.upper()} {route_template}", expected_ms=DEFAULT_EXPECTED_MS)


def catalog_size() -> int:
    """Return how many routes have a named activity (used by tests and docs)."""
    return len(_BY_ROUTE)

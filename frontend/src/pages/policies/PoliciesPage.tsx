/**
 * @file Lists Unity Catalog ABAC policies split by metastore vs catalog scope.
 * Search, type, and catalog filters are local state seeded from the URL; only `scope` is written
 * back as a query parameter. The route is wrapped in `RequireAdmin`.
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { usePolicies } from "@/api/queries";
import type { PolicyScopeKind, PolicySummary } from "@/api/types";
import { Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { formatPolicyType, formatTag, policyTypeTone } from "./policyLabels";
import styles from "./PoliciesPage.module.css";

/** Unity Catalog ABAC policy type values offered in the type filter. */
const POLICY_TYPES = ["COLUMN_MASK", "ROW_FILTER", "GRANT", "DENY"];

/**
 * Table of policy summaries with links to each detail page, or an empty-state message.
 */
function PolicyTable({
  policies,
  emptyTitle,
  emptyBody,
  showCatalog,
}: {
  /** Policy summaries to render as rows. */
  policies: PolicySummary[];
  /** Heading shown when `policies` is empty. */
  emptyTitle: string;
  /** Supporting copy shown when `policies` is empty. */
  emptyBody: string;
  /** When true, the scope column header reads "Catalog / scope". */
  showCatalog: boolean;
}) {
  if (!policies.length) {
    return (
      <div className={styles.empty}>
        <strong>{emptyTitle}</strong>
        <div>{emptyBody}</div>
      </div>
    );
  }

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>{showCatalog ? "Catalog / scope" : "Scope"}</th>
            <th>Principals</th>
            <th>Tags</th>
            <th>Condition</th>
          </tr>
        </thead>
        <tbody>
          {policies.map((policy) => (
            <tr key={policy.id}>
              <td>
                <Link className={styles.rowLink} to={`/policies/${policy.id}`}>
                  {policy.name}
                </Link>
              </td>
              <td>
                <Badge tone={policyTypeTone(policy.policyType)}>{formatPolicyType(policy.policyType)}</Badge>
              </td>
              <td>
                <div>
                  <Badge>{policy.onSecurableType}</Badge>
                </div>
                <code>{policy.onSecurableFullname}</code>
              </td>
              <td>
                <div className={styles.chipRow}>
                  {policy.toPrincipals.length ? (
                    policy.toPrincipals.map((principal) => <Badge key={principal}>{principal}</Badge>)
                  ) : (
                    <span className={styles.muted}>None</span>
                  )}
                </div>
                {policy.exceptPrincipals.length > 0 && (
                  <div className={styles.muted}>except {policy.exceptPrincipals.join(", ")}</div>
                )}
              </td>
              <td>
                <div className={styles.chipRow}>
                  {policy.governedTags.length ? (
                    policy.governedTags.map((tag) => (
                      <Badge key={`${tag.key}:${tag.value ?? ""}`}>{formatTag(tag)}</Badge>
                    ))
                  ) : (
                    <span className={styles.muted}>—</span>
                  )}
                </div>
              </td>
              <td>
                {policy.whenCondition ? (
                  <div className={styles.condition} title={policy.whenCondition}>
                    {policy.whenCondition}
                  </div>
                ) : (
                  <span className={styles.muted}>—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Admin catalog of metastore-wide and catalog-scoped ABAC policies.
 *
 * Loads grouped policies with {@link usePolicies}. The Metastore/Catalog tabs switch `scope` in the
 * URL; type and text filters are applied as query params to that hook, while catalog name is a
 * client-side filter of catalog-scoped rows.
 *
 * @remarks The route is wrapped in `RequireAdmin`. There are no create/edit controls on this page.
 */
export function PoliciesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const scope = (searchParams.get("scope") as PolicyScopeKind | null) ?? "metastore";
  const [policyType, setPolicyType] = useState(searchParams.get("type") ?? "");
  const [catalog, setCatalog] = useState(searchParams.get("catalog") ?? "");
  const [q, setQ] = useState(searchParams.get("q") ?? "");

  const params = useMemo(
    () => ({
      policyType: policyType || undefined,
      q: q.trim() || undefined,
    }),
    [policyType, q],
  );

  const { data, isPending, error } = usePolicies(params);

  const catalogs = useMemo(() => {
    const names = new Set<string>();
    for (const policy of data?.catalog ?? []) {
      if (policy.catalogName) names.add(policy.catalogName);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [data?.catalog]);

  /** Replace the `scope` search param without adding a history entry. */
  const setScope = (next: PolicyScopeKind) => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set("scope", next);
    setSearchParams(nextParams, { replace: true });
  };

  const metastorePolicies = data?.metastore ?? [];
  const catalogPolicies = useMemo(() => {
    const items = data?.catalog ?? [];
    if (!catalog) return items;
    return items.filter((policy) => (policy.catalogName || "").toLowerCase() === catalog.toLowerCase());
  }, [data?.catalog, catalog]);
  const visible = scope === "metastore" ? metastorePolicies : catalogPolicies;

  return (
    <>
      <PageHeader
        title="Policies"
        description="Unity Catalog ABAC policies, separated into metastore-wide rules and catalog-specific rules."
      />

      <div className={styles.tabs} role="tablist" aria-label="Policy scope">
        <button
          type="button"
          role="tab"
          aria-selected={scope === "metastore"}
          className={`${styles.tab}${scope === "metastore" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setScope("metastore")}
        >
          Metastore
          <span className={styles.tabCount}>({data ? metastorePolicies.length : "…"})</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={scope === "catalog"}
          className={`${styles.tab}${scope === "catalog" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setScope("catalog")}
        >
          Catalog
          <span className={styles.tabCount}>({data ? catalogPolicies.length : "…"})</span>
        </button>
      </div>

      <div className={styles.toolbar}>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Search</span>
          <input
            className={styles.input}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="Name, principal, tag, condition"
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Policy type</span>
          <select className={styles.select} value={policyType} onChange={(event) => setPolicyType(event.target.value)}>
            <option value="">All types</option>
            {POLICY_TYPES.map((type) => (
              <option key={type} value={type}>
                {formatPolicyType(type)}
              </option>
            ))}
          </select>
        </label>
        {scope === "catalog" && (
          <label className={styles.field}>
            <span className={styles.label}>Catalog</span>
            <select className={styles.select} value={catalog} onChange={(event) => setCatalog(event.target.value)}>
              <option value="">All catalogs</option>
              {catalogs.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {isPending && <Spinner label="Loading policies" />}
      {error && <ErrorAlert error={error} title="Could not load policies" />}

      {data && (
        <div className={styles.stack}>
          <Card
            title={
              scope === "metastore"
                ? `Metastore policies${data.metastoreName ? ` · ${data.metastoreName}` : ""}`
                : "Catalog policies"
            }
            actions={<Badge tone="neutral">{visible.length}</Badge>}
          >
            <PolicyTable
              policies={visible}
              showCatalog={scope === "catalog"}
              emptyTitle={scope === "metastore" ? "No metastore-level policies" : "No catalog policies"}
              emptyBody={
                scope === "metastore"
                  ? "Metastore ABAC policies apply across every catalog. None are defined yet."
                  : "No catalog, schema, or table policies matched the current filters."
              }
            />
          </Card>
        </div>
      )}
    </>
  );
}

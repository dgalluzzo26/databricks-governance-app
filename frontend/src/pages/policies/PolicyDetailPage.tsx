/**
 * @file Read-only detail view for a single Unity Catalog ABAC policy.
 * Shows scope, principals, WHEN condition, governed tags, mask/filter actions, and tagged assets.
 * The route is wrapped in `RequireAdmin`.
 */
import { Link, useParams } from "react-router-dom";
import { usePolicy } from "@/api/queries";
import type { AffectedAsset } from "@/api/types";
import { Alert, Badge, Card, DescriptionList, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { useClientPagination } from "@/hooks/useClientPagination";
import { formatPolicyType, formatTag, policyTypeTone } from "./policyLabels";
import styles from "./PolicyDetailPage.module.css";

/**
 * Loads one ABAC policy via {@link usePolicy} from the `policyId` route param and renders its
 * definition plus affected assets.
 *
 * The back link restores `/policies?scope=` using the policy's `scopeKind` (default `metastore`).
 *
 * @remarks The route is wrapped in `RequireAdmin`. This page has no edit or mutation controls.
 */
export function PolicyDetailPage() {
  const { policyId } = useParams();
  const { data: policy, isPending, error } = usePolicy(policyId);

  return (
    <>
      <Link className={styles.back} to={`/policies?scope=${policy?.scopeKind ?? "metastore"}`}>
        ← Back to policies
      </Link>
      <PageHeader
        title={policy?.name ?? "Policy"}
        description={
          policy
            ? `${policy.scopeKind === "metastore" ? "Metastore-level" : "Catalog-specific"} ABAC policy`
            : "Unity Catalog ABAC policy detail"
        }
        actions={
          policy ? <Badge tone={policyTypeTone(policy.policyType)}>{formatPolicyType(policy.policyType)}</Badge> : null
        }
      />

      {isPending && <Spinner label="Loading policy" />}
      {error && <ErrorAlert error={error} title="Could not load policy" />}

      {policy && (
        <div className={styles.stack}>
          <Card title="Scope">
            <DescriptionList
              items={[
                {
                  label: "Bucket",
                  value: (
                    <Badge tone={policy.scopeKind === "metastore" ? "warning" : "info"}>
                      {policy.scopeKind === "metastore" ? "Metastore" : "Catalog"}
                    </Badge>
                  ),
                },
                { label: "Attached to", value: <Badge>{policy.onSecurableType}</Badge> },
                { label: "Full name", value: <code>{policy.onSecurableFullname}</code> },
                {
                  label: "Applies to",
                  value: policy.forSecurableType ? <code>{policy.forSecurableType}</code> : "—",
                },
                { label: "Created by", value: policy.createdBy ?? "—" },
                { label: "Comment", value: policy.comment ?? "—" },
              ]}
            />
          </Card>

          <Card title="Principals">
            <DescriptionList
              items={[
                {
                  label: "Applies to",
                  value: (
                    <div className={styles.chipRow}>
                      {policy.toPrincipals.length ? (
                        policy.toPrincipals.map((principal) => <Badge key={principal}>{principal}</Badge>)
                      ) : (
                        <span className={styles.muted}>None</span>
                      )}
                    </div>
                  ),
                },
                {
                  label: "Except",
                  value: policy.exceptPrincipals.length ? (
                    <div className={styles.chipRow}>
                      {policy.exceptPrincipals.map((principal) => (
                        <Badge key={principal} tone="warning">
                          {principal}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    "—"
                  ),
                },
              ]}
            />
          </Card>

          <Card title="Condition & tags">
            <DescriptionList
              items={[
                {
                  label: "WHEN",
                  value: policy.whenCondition ? <pre className={styles.codeBlock}>{policy.whenCondition}</pre> : "—",
                },
                {
                  label: "Governed tags",
                  value: policy.governedTags.length ? (
                    <div className={styles.chipRow}>
                      {policy.governedTags.map((tag) => (
                        <Badge key={`${tag.key}:${tag.value ?? ""}`}>{formatTag(tag)}</Badge>
                      ))}
                    </div>
                  ) : (
                    "—"
                  ),
                },
                {
                  label: "Match columns",
                  value: policy.matchColumns.length ? (
                    <div className={styles.stack}>
                      {policy.matchColumns.map((column) => (
                        <div key={column.alias}>
                          <code>{column.alias}</code>
                          {column.condition ? <pre className={styles.codeBlock}>{column.condition}</pre> : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    "—"
                  ),
                },
              ]}
            />
          </Card>

          <Card title="Action">
            <DescriptionList
              items={[
                {
                  label: "Column mask",
                  value: policy.columnMask ? (
                    <span>
                      <code>{policy.columnMask.functionName}</code> on <code>{policy.columnMask.onColumn}</code>
                    </span>
                  ) : (
                    "—"
                  ),
                },
                {
                  label: "Row filter",
                  value: policy.rowFilter ? (
                    <span>
                      <code>{policy.rowFilter.functionName}</code>
                      {policy.rowFilter.using.length ? ` using ${policy.rowFilter.using.join(", ")}` : ""}
                    </span>
                  ) : (
                    "—"
                  ),
                },
                {
                  label: "Privileges",
                  value: policy.privileges.length ? (
                    <div className={styles.chipRow}>
                      {policy.privileges.map((privilege) => (
                        <Badge key={privilege}>{privilege}</Badge>
                      ))}
                    </div>
                  ) : (
                    "—"
                  ),
                },
              ]}
            />
          </Card>

          <AffectedAssetsCard
            assets={policy.affectedAssets}
            truncated={policy.affectedAssetsTruncated}
            note={policy.affectedAssetsNote}
          />
        </div>
      )}
    </>
  );
}

/**
 * Formats a nullable table cell as an em dash when empty.
 *
 * @param value - Cell contents, or null/undefined when missing.
 */
function cell(value: string | null | undefined): string {
  return value ? value : "—";
}

/**
 * Paginated table of assets that match the policy's governed tags.
 *
 * Warns when the server truncated the list.
 */
function AffectedAssetsCard({
  assets,
  truncated,
  note,
}: {
  /** Resolved assets from the policy payload. */
  assets: AffectedAsset[];
  /** When true, the API capped the result set and more matches may exist. */
  truncated: boolean;
  /** Optional informational note from the API, shown above the table. */
  note: string | null;
}) {
  const pager = useClientPagination(assets);

  return (
    <Card title="Affected assets" actions={assets.length ? <Badge>{assets.length}</Badge> : null}>
      {note && <Alert tone="info">{note}</Alert>}
      {truncated && (
        <Alert tone="warning">Results are capped by the server. Narrow the policy tags to see the rest.</Alert>
      )}
      {assets.length === 0 ? (
        <div className={styles.muted}>No tagged assets resolved for this policy.</div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="Affected assets">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Catalog</th>
                  <th>Schema</th>
                  <th>Table</th>
                  <th>Column</th>
                  <th>Tag</th>
                  <th>Tag value</th>
                </tr>
              </thead>
              <tbody>
                {pager.rows.map((asset) => (
                  <tr key={`${asset.assetType}:${asset.fullName}:${asset.matchedTagKey}:${asset.matchedTagValue}`}>
                    <td>
                      <Badge>{asset.assetType}</Badge>
                    </td>
                    <td>
                      <code>{cell(asset.catalogName)}</code>
                    </td>
                    <td>
                      <code>{cell(asset.schemaName)}</code>
                    </td>
                    <td>
                      <code>{cell(asset.tableName)}</code>
                    </td>
                    <td>
                      <code>{cell(asset.columnName)}</code>
                    </td>
                    <td>
                      <code>{cell(asset.matchedTagKey)}</code>
                    </td>
                    <td>
                      <code>{cell(asset.matchedTagValue)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager
            total={pager.total}
            page={pager.page}
            pageSize={pager.pageSize}
            onPageChange={pager.setPage}
            onPageSizeChange={pager.setPageSize}
          />
        </>
      )}
    </Card>
  );
}

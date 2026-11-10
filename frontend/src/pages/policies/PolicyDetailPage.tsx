import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { usePolicy } from "@/api/queries";
import type { AffectedAsset } from "@/api/types";
import { Alert, Badge, Card, DescriptionList, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { formatPolicyType, formatTag, policyTypeTone } from "./policyLabels";
import styles from "./PolicyDetailPage.module.css";

const PAGE_SIZES = [25, 50, 100] as const;

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

function cell(value: string | null | undefined): string {
  return value ? value : "—";
}

function AffectedAssetsCard({
  assets,
  truncated,
  note,
}: {
  assets: AffectedAsset[];
  truncated: boolean;
  note: string | null;
}) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZES)[number]>(25);

  const pageCount = Math.max(1, Math.ceil(assets.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const rows = useMemo(() => {
    const start = safePage * pageSize;
    return assets.slice(start, start + pageSize);
  }, [assets, pageSize, safePage]);

  const from = assets.length === 0 ? 0 : safePage * pageSize + 1;
  const to = Math.min(assets.length, (safePage + 1) * pageSize);

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
                {rows.map((asset) => (
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
          <div className={styles.pager}>
            <div className={styles.pagerMeta}>
              {from}–{to} of {assets.length}
            </div>
            <div className={styles.pagerControls}>
              <label>
                <span className={styles.muted}>Rows</span>{" "}
                <select
                  className={styles.select}
                  aria-label="Rows per page"
                  value={pageSize}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value) as (typeof PAGE_SIZES)[number]);
                    setPage(0);
                  }}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className={styles.button}
                disabled={safePage === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                Previous
              </button>
              <span className={styles.pagerMeta}>
                Page {safePage + 1} of {pageCount}
              </span>
              <button
                type="button"
                className={styles.button}
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

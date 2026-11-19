/**
 * @file Attribute Explorer — who holds a path and which resources that path affects.
 *
 * Admins see every attribute; path approvers only paths they can decide.
 * Data access lists unlocked tables; sensitivity lists masked columns; row-filter
 * dimensions list every principal (and the values they hold) plus attached columns.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useAnalyzeAttribute,
  useAttributeDefinitions,
  useDataAccessSettings,
  useRevokeAttribute,
} from "@/api/queries";
import { useEffectiveUser } from "@/app/viewAs";
import type {
  AttributeDefinition,
  AttributeExplorerKind,
  AttributeExplorerResult,
  AttributeHolder,
  AttributePrincipalSummary,
  UnlockedTable,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, SearchableSelect, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import styles from "../identity/IdentityPage.module.css";
import { isDataAccessDefinition } from "../identity/definitionRules";

/**
 * Classify a definition for the attribute picker.
 */
/** Formats an expiry for display, or an em dash when none is set. */
function formatExpiry(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function definitionKind(
  item: AttributeDefinition,
  accessTagKey: string,
): "sensitivity" | "data_access" | "row_filter" | "other" {
  if (item.key.toLowerCase() === "sensitivity") return "sensitivity";
  if (isDataAccessDefinition(item, accessTagKey)) return "data_access";
  if (item.valueSource !== "governed_tags") return "row_filter";
  return "other";
}

/**
 * Admin / approver reverse lookup: holders + affected resources for a path.
 */
export function AttributeExplorerPage() {
  const { data: user } = useEffectiveUser();
  const definitions = useAttributeDefinitions();
  const dataAccessSettings = useDataAccessSettings();
  const analyze = useAnalyzeAttribute();
  const [searchParams, setSearchParams] = useSearchParams();

  const accessTagKey =
    dataAccessSettings.data?.settings.accessTagKey?.trim() || "access_path";

  const [attributeKey, setAttributeKey] = useState(searchParams.get("key") ?? "");
  const [attributeValue, setAttributeValue] = useState(searchParams.get("value") ?? "");
  const [result, setResult] = useState<AttributeExplorerResult | null>(null);

  const defs = definitions.data?.definitions ?? [];
  const selected = defs.find((d) => d.key === attributeKey);
  const selectedKind = selected ? definitionKind(selected, accessTagKey) : null;
  const isRowFilterOverview = selectedKind === "row_filter";
  const values = useMemo(
    () => (selected?.allowedValues ?? []).filter((v) => v && v !== "*"),
    [selected],
  );

  useEffect(() => {
    const key = searchParams.get("key");
    const value = searchParams.get("value");
    if (key) setAttributeKey(key);
    if (value) setAttributeValue(value);
  }, [searchParams]);

  const canAnalyze = isRowFilterOverview
    ? Boolean(attributeKey.trim())
    : Boolean(attributeKey.trim() && attributeValue.trim());

  const runAnalyze = () => {
    if (!canAnalyze) return;
    const key = attributeKey.trim();
    const params = new URLSearchParams(searchParams);
    params.set("key", key);
    if (isRowFilterOverview) {
      params.delete("value");
    } else {
      params.set("value", attributeValue.trim());
    }
    setSearchParams(params, { replace: true });
    void analyze
      .mutateAsync({
        attributeKey: key,
        attributeValue: isRowFilterOverview ? "" : attributeValue.trim(),
      })
      .then((body) => setResult(body));
  };

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Attribute explorer"
        description={
          user?.isAdmin
            ? "See who holds an attribute path and which resources it affects — unlocked tables for data access, masked columns for sensitivity, and filtered columns for row filters."
            : "Inspect attribute paths you approve — holders and the resources that path affects."
        }
      />

      <Card title="Look up">
        {definitions.isPending && <Spinner label="Loading definitions" />}
        {definitions.error && (
          <ErrorAlert error={definitions.error} title="Could not load definitions" />
        )}
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Attribute</span>
            <select
              className={styles.select}
              value={attributeKey}
              onChange={(event) => {
                setAttributeKey(event.target.value);
                setAttributeValue("");
                setResult(null);
              }}
              aria-label="Attribute"
            >
              <option value="">Select attribute</option>
              {defs.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.key} ({definitionKind(item, accessTagKey).replaceAll("_", " ")})
                </option>
              ))}
            </select>
          </label>
          {!isRowFilterOverview && (
            <label className={styles.field}>
              <span className={styles.label}>Value / path</span>
              {values.length > 0 ? (
                <SearchableSelect
                  aria-label="Value"
                  value={attributeValue}
                  onChange={setAttributeValue}
                  options={values}
                  placeholder="Select value"
                />
              ) : (
                <input
                  className={styles.input}
                  value={attributeValue}
                  onChange={(event) => setAttributeValue(event.target.value)}
                  placeholder="e.g. finance"
                  aria-label="Value"
                />
              )}
            </label>
          )}
        </div>
        {isRowFilterOverview && attributeKey && (
          <p className={styles.muted} style={{ marginTop: "var(--space-2)" }}>
            Row filter: Analyze lists every principal with values for{" "}
            <code>{attributeKey}</code>, plus table columns tagged with that dimension.
          </p>
        )}
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!canAnalyze || analyze.isPending}
            onClick={runAnalyze}
          >
            {analyze.isPending ? "Analyzing…" : "Analyze"}
          </button>
          <Link className={styles.button} to="/access-explorer">
            Open Access explorer
          </Link>
        </div>
        {analyze.error && <ErrorAlert error={analyze.error} title="Analyze failed" />}
      </Card>

      {result && (
        <ResultPanel
          result={result}
          onRevoked={(assignmentId) =>
            setResult({
              ...result,
              holders: result.holders.filter((item) => item.assignmentId !== assignmentId),
            })
          }
        />
      )}
    </div>
  );
}

/**
 * Analyze results: holders (with revoke) or row-filter principal overview, plus affected resources.
 *
 * @param props.result - Latest analyze response.
 * @param props.onRevoked - Called after a holder assignment is revoked so the list can update.
 */
function ResultPanel({
  result,
  onRevoked,
}: {
  result: AttributeExplorerResult;
  onRevoked: (assignmentId: string) => void;
}) {
  const revoke = useRevokeAttribute();
  const isRowOverview = result.kind === "row_filter" && result.attributeValue === "*";
  const title = isRowOverview
    ? `${result.attributeKey} — all principals`
    : `${result.attributeKey} = ${result.attributeValue}`;
  const showResources =
    result.kind === "data_access" ||
    result.kind === "sensitivity" ||
    result.kind === "row_filter";

  return (
    <div className={styles.stack}>
      <Card title={title} actions={<Badge>{result.kind.replaceAll("_", " ")}</Badge>}>
        {result.groupName && (
          <p className={styles.muted}>
            Group <code>{result.groupName}</code>
            {result.parentGroup ? (
              <>
                {" "}
                (parent nested <code>{result.parentGroup}</code>)
              </>
            ) : null}
          </p>
        )}
        {result.note && <Alert title="Note">{result.note}</Alert>}
      </Card>

      {isRowOverview ? (
        <PrincipalsTable
          principals={result.principals}
          resetKey={`${result.attributeKey}:${result.attributeValue}`}
        />
      ) : (
        <HoldersTable
          holders={result.holders}
          resetKey={`${result.attributeKey}:${result.attributeValue}:${result.holders.length}`}
          revokePending={revoke.isPending}
          revokeError={revoke.error}
          onRevoke={(assignmentId) =>
            void revoke.mutateAsync(assignmentId).then(() => onRevoked(assignmentId))
          }
        />
      )}

      {showResources && (
        <AffectedResourcesTable
          kind={result.kind}
          tables={result.tables}
          resetKey={`${result.attributeKey}:${result.attributeValue}:${result.kind}`}
        />
      )}
    </div>
  );
}

/**
 * Holders for a path lookup, with revoke and pagination.
 */
function HoldersTable({
  holders,
  resetKey,
  revokePending,
  revokeError,
  onRevoke,
}: {
  holders: AttributeHolder[];
  resetKey: string;
  revokePending: boolean;
  revokeError: Error | null;
  onRevoke: (assignmentId: string) => void;
}) {
  const holderColumns: TableColumn<AttributeHolder>[] = [
    {
      key: "principal",
      label: "Principal",
      value: (item) => item.principalName,
      filterText: (item) => `${item.principalType} ${item.principalName}`,
    },
    { key: "type", label: "Type", value: (item) => item.principalType },
    { key: "value", label: "Assigned value", value: (item) => item.attributeValue },
    { key: "match", label: "Match", value: (item) => item.match },
    { key: "expires", label: "Expires", value: (item) => item.expiresAt, filterText: (item) => formatExpiry(item.expiresAt) },
    { key: "actions", label: "Actions" },
  ];
  const pager = useTableView(holders, holderColumns, { resetKey });
  return (
    <Card title="Holders" actions={<Badge>{holders.length}</Badge>}>
      {holders.length === 0 ? (
        <div className={styles.empty}>No active assignments for this path.</div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="Holders">
              <DataTableHead view={pager} />
              <tbody>
                {pager.rows.map((item) => (
                  <tr key={item.assignmentId}>
                    <td>
                      <code>{item.principalName}</code>
                    </td>
                    <td>
                      <Badge>{item.principalType}</Badge>
                    </td>
                    <td>
                      <code>{item.attributeValue}</code>
                    </td>
                    <td>{item.match}</td>
                    <td className={styles.muted}>
                      {item.expiresAt ? new Date(item.expiresAt).toLocaleString() : "—"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonDanger}`}
                        disabled={revokePending}
                        aria-busy={revokePending}
                        aria-label={`Revoke ${item.attributeValue} from ${item.principalName}`}
                        onClick={() => onRevoke(item.assignmentId)}
                      >
                        {revokePending ? "Revoking…" : "Revoke"}
                      </button>
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
      {revokeError && <ErrorAlert error={revokeError} title="Revoke failed" />}
    </Card>
  );
}

/**
 * Row-filter dimension overview: every principal and the values they hold.
 */
function PrincipalsTable({
  principals,
  resetKey,
}: {
  principals: AttributePrincipalSummary[];
  resetKey: string;
}) {
  const principalColumns: TableColumn<AttributePrincipalSummary>[] = [
    {
      key: "principal",
      label: "Principal",
      value: (item) => item.principalName,
      filterText: (item) => `${item.principalType} ${item.principalName}`,
    },
    { key: "type", label: "Type", value: (item) => item.principalType },
    { key: "values", label: "Values", value: (item) => item.values.join(", ") },
    { key: "expires", label: "Expires (soonest)", value: (item) => item.expiresAt, filterText: (item) => formatExpiry(item.expiresAt) },
  ];
  const pager = useTableView(principals, principalColumns, { resetKey });
  return (
    <Card title="Principals" actions={<Badge>{principals.length}</Badge>}>
      {principals.length === 0 ? (
        <div className={styles.empty}>No active assignments for this dimension.</div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="Principals">
              <DataTableHead view={pager} />
              <tbody>
                {pager.rows.map((item) => (
                  <tr key={`${item.principalType}:${item.principalName}`}>
                    <td>
                      <code>{item.principalName}</code>
                    </td>
                    <td>
                      <Badge>{item.principalType}</Badge>
                    </td>
                    <td>
                      <div className={styles.buttonRow} style={{ flexWrap: "wrap", gap: "var(--space-1)" }}>
                        {item.values.map((value) => (
                          <Badge key={value}>{value}</Badge>
                        ))}
                      </div>
                    </td>
                    <td className={styles.muted}>
                      {item.expiresAt ? new Date(item.expiresAt).toLocaleString() : "—"}
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

/**
 * Resources affected by the looked-up path: unlocked tables, masked columns, or filtered columns.
 */
function AffectedResourcesTable({
  kind,
  tables,
  resetKey,
}: {
  kind: AttributeExplorerKind;
  tables: UnlockedTable[];
  resetKey: string;
}) {
  const showColumn = kind === "sensitivity" || kind === "row_filter";
  const title =
    kind === "sensitivity"
      ? "Columns affected"
      : kind === "row_filter"
        ? "Tables filtered"
        : "Tables unlocked";
  const empty =
    kind === "sensitivity"
      ? "No columns tagged with this sensitivity path (or a descendant)."
      : kind === "row_filter"
        ? "No table columns are tagged with this row-filter dimension."
        : "No reviewed tables tagged with this path (or a descendant).";
  const tagLabel =
    kind === "sensitivity" ? "Sensitivity path" : kind === "row_filter" ? "Dimension" : "Tag value";

  const columns: TableColumn<UnlockedTable>[] = [
    { key: "table", label: "Table", value: (item) => item.fullName },
    ...(showColumn
      ? [{ key: "column", label: "Column", value: (item: UnlockedTable) => item.columnName ?? "" }]
      : []),
    { key: "tagValue", label: tagLabel, value: (item) => item.tagValue },
    { key: "match", label: "Match", value: (item) => item.match },
    ...(kind === "data_access"
      ? [{ key: "group", label: "Group", value: (item: UnlockedTable) => item.groupName ?? "" }]
      : []),
  ];
  const pager = useTableView(tables, columns, { resetKey });
  return (
    <Card title={title} actions={<Badge>{tables.length}</Badge>}>
      {tables.length === 0 ? (
        <div className={styles.empty}>{empty}</div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label={title}>
              <DataTableHead view={pager} />
              <tbody>
                {pager.rows.map((item) => (
                  <tr key={`${item.fullName}:${item.columnName ?? ""}:${item.tagValue}`}>
                    <td>
                      <Link to={`/access-explorer?table=${encodeURIComponent(item.fullName)}`}>
                        <code>{item.fullName}</code>
                      </Link>
                    </td>
                    {showColumn && (
                      <td>
                        <code>{item.columnName ?? "—"}</code>
                      </td>
                    )}
                    <td>
                      <code>{item.tagValue}</code>
                    </td>
                    <td>{item.match}</td>
                    {kind === "data_access" && (
                      <td>
                        <code>{item.groupName ?? "—"}</code>
                      </td>
                    )}
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

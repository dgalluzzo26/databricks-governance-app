/**
 * @file Access Explorer page: browse or search Unity Catalog tables and inspect column-mask /
 * row-filter access for the signed-in user.
 *
 * Loads table insights via `useAnalyzeTable`, table search via `useTableSearch`, and can submit
 * attribute access requests via `useCreateAccessRequest`. The selected table is mirrored in the
 * `?table=` search parameter; a URL value bootstraps analysis once on mount.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useAnalyzeTable,
  useAttributeDefinitions,
  useCreateAccessRequest,
  useTableSearch,
} from "@/api/queries";
import type {
  ColumnAccessInsight,
  RowFilterInsight,
  SuggestedAttribute,
  TableAccessInsight,
  TableSearchHit,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import styles from "../identity/IdentityPage.module.css";
import { AccessExplorerTree } from "./AccessExplorerTree";
import layout from "./AccessExplorerPage.module.css";

/**
 * Dotted path prefixes including the leaf itself (e.g. `a.b.c` → `a`, `a.b`, `a.b.c`).
 *
 * @param path - Hierarchical attribute value.
 */
function pathPrefixes(path: string): string[] {
  const parts = path.split(".").map((part) => part.trim()).filter(Boolean);
  return parts.map((_, index) => parts.slice(0, index + 1).join("."));
}

/**
 * Values a user may request instead of the suggested leaf: the leaf plus broader ancestors.
 *
 * When the attribute definition lists allowed values, only those that appear in the
 * vocabulary are offered (so the request will pass server validation).
 *
 * @param leaf - Suggested attribute value from analyze.
 * @param allowedValues - Definition allowed values; empty means free-form.
 */
function editableRequestValues(leaf: string, allowedValues: string[]): string[] {
  const prefixes = pathPrefixes(leaf);
  if (!allowedValues.length) {
    return [...prefixes].reverse();
  }
  const byLower = new Map(allowedValues.map((value) => [value.toLowerCase(), value]));
  const options: string[] = [];
  for (const prefix of [...prefixes].reverse()) {
    const hit = byLower.get(prefix.toLowerCase());
    if (hit && !options.some((value) => value.toLowerCase() === hit.toLowerCase())) {
      options.push(hit);
    }
  }
  // Always keep the original leaf if it is allowed (or vocabulary is empty of matches).
  if (!options.length) {
    const leafHit = byLower.get(leaf.toLowerCase());
    return leafHit ? [leafHit] : [leaf];
  }
  return options;
}

/**
 * Maps a column access state to a Badge visual tone.
 *
 * @param state - Column access state from the analyze insight.
 */
function stateTone(state: ColumnAccessInsight["accessState"]): "success" | "warning" | "danger" | "neutral" | "info" {
  switch (state) {
    case "unmasked":
      return "success";
    case "masked":
    case "geo_masked":
      return "warning";
    case "not_governed":
      return "neutral";
    default:
      return "info";
  }
}

/**
 * Human-readable label for a column access state shown in the columns table.
 *
 * @param state - Column access state from the analyze insight.
 */
function stateLabel(state: ColumnAccessInsight["accessState"]): string {
  switch (state) {
    case "unmasked":
      return "Unmasked for you";
    case "masked":
      return "Masked";
    case "geo_masked":
      return "Geo masked";
    case "not_governed":
      return "Not governed";
    default:
      return state;
  }
}

/**
 * Page for discovering table access: catalog tree browse, table search/analyze, insight cards,
 * and suggested-attribute request submission.
 *
 * Side effects: syncs `?table=` into the URL on analyze; bootstraps `useAnalyzeTable` from the
 * initial URL once; search uses `useTableSearch` when the query is long enough (hook-gated).
 */
export function AccessExplorerPage() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("table") ?? "";
  const [tableInput, setTableInput] = useState(initial);
  const [search, setSearch] = useState("");
  const hits = useTableSearch(search);
  const analyze = useAnalyzeTable();
  const insight = analyze.data;

  useEffect(() => {
    if (initial && !analyze.data && !analyze.isPending && !analyze.isError) {
      void analyze.mutateAsync({ tableFullName: initial });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only bootstrap from URL once
  }, []);

  /** Analyzes `fullName`, updates the input, and writes `?table=` into the URL. */
  const runAnalyze = async (fullName: string) => {
    const name = fullName.trim();
    if (!name) return;
    setTableInput(name);
    const next = new URLSearchParams(params);
    next.set("table", name);
    setParams(next, { replace: true });
    await analyze.mutateAsync({ fullName: name, tableFullName: name });
  };

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Access explorer"
        description={
          <>
            Browse or search a table to see masks, row filters, and requestable attributes.
            Reviewed <code>data_access_control</code> tags on the table offer a data-access
            request (broader paths allowed). Group grants stay dry-run until apply mode is on.
          </>
        }
      />

      <div className={layout.layout}>
      <Card title="Browse data">
        <AccessExplorerTree
          selected={insight?.tableFullName ?? null}
          onSelectTable={(name) => void runAnalyze(name)}
        />
      </Card>
      <div className={layout.main}>
      <Card title="Find a table">
        <div className={styles.formGrid}>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Three-part name</span>
            <input
              className={styles.input}
              value={tableInput}
              placeholder="catalog.schema.table"
              onChange={(event) => setTableInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runAnalyze(tableInput);
              }}
            />
          </label>
          <div className={styles.buttonRow} style={{ alignSelf: "end" }}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={!tableInput.trim() || analyze.isPending}
              onClick={() => void runAnalyze(tableInput)}
            >
              {analyze.isPending ? "Analyzing…" : "Analyze"}
            </button>
          </div>
        </div>
        <label className={styles.fieldGrow} style={{ marginTop: "var(--space-3)" }}>
          <span className={styles.label}>Or search tables</span>
          <input
            className={styles.input}
            value={search}
            placeholder="Type at least 2 characters"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        {hits.isFetching && <Spinner label="Searching tables" />}
        {hits.error && <ErrorAlert error={hits.error} title="Search failed" />}
        {hits.data && hits.data.tables.length > 0 && (
          <SearchResultsTable tables={hits.data.tables} resetKey={search} onAnalyze={(full) => void runAnalyze(full)} />
        )}
        {analyze.error && <ErrorAlert error={analyze.error} title="Analyze failed" />}
      </Card>

      {insight && <InsightView insight={insight} />}
      </div>
      </div>
    </div>
  );
}

/**
 * Paginated list of table search hits with an Analyze action per row.
 *
 * @param props.tables - Search hits from `useTableSearch`.
 * @param props.resetKey - Search text; changing it returns to the first page.
 * @param props.onAnalyze - Called with the three-part table name.
 */
function SearchResultsTable({
  tables,
  resetKey,
  onAnalyze,
}: {
  tables: TableSearchHit[];
  resetKey: string;
  onAnalyze: (fullName: string) => void;
}) {
  const hitColumns: TableColumn<TableSearchHit>[] = [
    {
      key: "table",
      label: "Table",
      value: (hit) => `${hit.catalogName}.${hit.schemaName}.${hit.tableName}`,
    },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(tables, hitColumns, { resetKey });

  return (
    <div style={{ marginTop: "var(--space-3)" }}>
  <div className={styles.tableWrap}>
    <table className={styles.table} aria-label="Table search results">
      <DataTableHead view={pager} />
      <tbody>
        {pager.rows.map((hit) => {
          const full = `${hit.catalogName}.${hit.schemaName}.${hit.tableName}`;
          return (
            <tr key={full}>
              <td>
                <code>{full}</code>
              </td>
              <td>
                <button
                  type="button"
                  className={styles.button}
                  onClick={() => onAnalyze(full)}
                >
                  Analyze
                </button>
              </td>
            </tr>
          );
        })}
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
    </div>
  );
}

/**
 * One row-filter dimension: the column it is attached to and what the signed-in user can see.
 *
 * @param props.rowFilter - Row-filter insight for one dimension, or a not-attached placeholder.
 */
function RowFilterDetails({ rowFilter }: { rowFilter: RowFilterInsight }) {
  if (!rowFilter.attached) {
    return <div className={styles.muted}>{rowFilter.notes ?? "No row filter on this table."}</div>;
  }
  return (
    <div className={styles.stack}>
      <DescriptionRow label="Dimension" value={<code>{rowFilter.dimensionKey}</code>} />
      <DescriptionRow label="Column" value={<code>{rowFilter.columnName}</code>} />
      <DescriptionRow
        label="Your access"
        value={
          <Badge tone={rowFilter.userCanSeeRows ? "success" : "danger"}>
            {rowFilter.userCanSeeRows
              ? rowFilter.userHasWildcard
                ? "All rows (*)"
                : `Values: ${rowFilter.userValues.join(", ")}`
              : "No rows"}
          </Badge>
        }
      />
      <div className={styles.muted}>{rowFilter.notes}</div>
    </div>
  );
}

/**
 * Renders the analyze insight: summary, suggested attributes, row-filter status, and per-column
 * mask/access details.
 *
 * @param props.insight - Full table access insight returned by `useAnalyzeTable`.
 */
function InsightView({ insight }: { insight: TableAccessInsight }) {
  const filters: RowFilterInsight[] = insight.rowFilters?.length ? insight.rowFilters : [insight.rowFilter];
  return (
    <>
      <Card
        title={insight.tableFullName}
        actions={
          <>
            {insight.tableType && <Badge tone="neutral">{insight.tableType}</Badge>}
            <Badge tone="info">{insight.columns.length} columns</Badge>
          </>
        }
      >
        <div className={styles.muted}>{insight.summary}</div>
      </Card>

      <SuggestionsCard suggestions={insight.suggestions} tableFullName={insight.tableFullName} />

      <Card title={filters.length > 1 ? `Row filters (${filters.length})` : "Row filter"}>
        <div className={styles.stack}>
          {filters.map((rowFilter, index) => (
            <RowFilterDetails key={`${rowFilter.dimensionKey ?? "none"}-${index}`} rowFilter={rowFilter} />
          ))}
        </div>
      </Card>

      <Card title="Columns">
        <ColumnAccessTable columns={insight.columns} resetKey={insight.tableFullName} />
      </Card>
    </>
  );
}


/**
 * Paginated per-column access table for one analyzed table.
 *
 * @param props.columns - Column insights from the analyze response.
 * @param props.resetKey - Changes when a different table is analyzed so the pager resets.
 */
function ColumnAccessTable({
  columns,
  resetKey,
}: {
  columns: ColumnAccessInsight[];
  resetKey: string;
}) {
  const accessColumns: TableColumn<ColumnAccessInsight>[] = [
    {
      key: "column",
      label: "Column",
      value: (col) => col.columnName,
      filterText: (col) => `${col.columnName} ${col.dataType ?? ""}`,
    },
    { key: "state", label: "State", value: (col) => stateLabel(col.accessState) },
    { key: "requiredPath", label: "Required path", value: (col) => col.requiredSensitivityPath ?? "" },
    { key: "maskRule", label: "Mask rule", value: (col) => col.maskRuleKey ?? "" },
    {
      key: "handler",
      label: "Handler",
      value: (col) => col.maskHandler ?? "",
      filterText: (col) => `${col.maskHandler ?? ""} ${col.maskKind ?? ""}`,
    },
    {
      key: "notes",
      label: "Notes",
      value: (col) => col.notes ?? col.maskDescription ?? "",
    },
  ];
  const pager = useTableView(columns, accessColumns, { resetKey });

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Column access">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((col) => (
              <tr key={col.columnName}>
                <td>
                  <code>{col.columnName}</code>
                  {col.dataType && (
                    <div className={styles.muted} style={{ fontSize: "0.85em" }}>
                      {col.dataType}
                    </div>
                  )}
                </td>
                <td>
                  <Badge tone={stateTone(col.accessState)}>{stateLabel(col.accessState)}</Badge>
                </td>
                <td>
                  {col.requiredSensitivityPath ? (
                    <code>{col.requiredSensitivityPath}</code>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{col.maskRuleKey ? <code>{col.maskRuleKey}</code> : "—"}</td>
                <td>
                  {col.maskHandler ? (
                    <>
                      <code>{col.maskHandler}</code>
                      {col.maskKind && (
                        <div className={styles.muted} style={{ fontSize: "0.85em" }}>
                          {col.maskKind}
                        </div>
                      )}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className={styles.muted}>{col.notes ?? col.maskDescription ?? ""}</td>
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
  );
}

/**
 * Stable selection key for a suggested attribute (`key:value`).
 *
 * @param item - Suggested attribute from the analyze response.
 */
function suggestionKey(item: SuggestedAttribute): string {
  return `${item.attributeKey}:${item.attributeValue}`;
}

/**
 * Reviewer label for a suggested attribute: category approver names, app admins, already held,
 * or not requestable.
 *
 * @param item - Suggested attribute from the analyze response.
 */
function routeLabel(item: SuggestedAttribute): string {
  if (item.alreadyHeld) return "Already held";
  if (!item.requestable) return "Not requestable yet";
  if (item.routedTo === "category_approvers" && item.approvers.length) {
    return item.approvers.map((a) => `${a.principalName} (${a.principalType})`).join(", ");
  }
  return "App admins";
}

/**
 * Interactive card listing sensitivity and row-filter attribute suggestions with multi-select,
 * value search, and batch access-request submission.
 *
 * Uses `useCreateAccessRequest` for each selected requestable attribute. Selecting a row-filter
 * `*` clears concrete values for that dimension, and selecting a concrete value clears `*`.
 *
 * @param props.suggestions - Suggested attributes from the table insight.
 * @param props.tableFullName - Three-part table name used to seed the justification text.
 */
function SuggestionsCard({
  suggestions,
  tableFullName,
}: {
  suggestions: SuggestedAttribute[];
  tableFullName: string;
}) {
  const create = useCreateAccessRequest();
  const definitions = useAttributeDefinitions();
  const allowedByKey = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const item of definitions.data?.definitions ?? []) {
      map.set(item.key.toLowerCase(), item.allowedValues ?? []);
    }
    return map;
  }, [definitions.data]);

  const requestable = suggestions.filter((item) => item.requestable);
  const dataAccessItems = suggestions.filter((item) => item.kind === "data_access");
  const sensitivityItems = suggestions.filter((item) => item.kind === "sensitivity");
  const rowFilterItems = suggestions.filter((item) => item.kind === "row_filter");
  const rowFilterStar = rowFilterItems.filter((item) => item.attributeValue === "*");
  const rowFilterValues = rowFilterItems.filter((item) => item.attributeValue !== "*");
  const rowFilterKeys = rowFilterItems.map((item) => suggestionKey(item));

  const [selected, setSelected] = useState<string[]>(
    requestable
      .filter(
        (item) =>
          item.kind === "data_access" ||
          (item.kind === "sensitivity" && !item.isHierarchyParent) ||
          (item.kind === "row_filter" && item.attributeValue === "*"),
      )
      .map((item) => suggestionKey(item)),
  );
  /** Edited request values keyed by the original suggestion `key:value`. */
  const [valueOverrides, setValueOverrides] = useState<Record<string, string>>({});
  const [valueFilter, setValueFilter] = useState("");
  const [justification, setJustification] = useState(`Need access for ${tableFullName}`);
  const [expires, setExpires] = useState("");
  const [results, setResults] = useState<string[]>([]);

  const resolvedValue = (item: SuggestedAttribute) =>
    valueOverrides[suggestionKey(item)] ?? item.attributeValue;

  const needle = valueFilter.trim().toLowerCase();
  const matchedValues = needle
    ? rowFilterValues.filter((item) => {
        const haystack = [
          item.attributeKey,
          item.attributeValue,
          ...item.coversPaths,
          ...item.columnNames,
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(needle);
      })
    : [];

  /**
   * Toggles selection for `item`, enforcing mutual exclusion between a dimension's `*` and
   * concrete values.
   */
  const toggle = (item: SuggestedAttribute) => {
    const key = suggestionKey(item);
    setSelected((current) => {
      if (current.includes(key)) return current.filter((value) => value !== key);
      if (item.kind === "row_filter" && item.attributeValue === "*") {
        // "*" already grants every row, so drop the individual values for the same dimension.
        const sameDimension = (value: string) =>
          value.startsWith(`${item.attributeKey}:`) && value !== key;
        return [...current.filter((value) => !sameDimension(value)), key];
      }
      if (item.kind === "row_filter") {
        // Selecting a concrete value drops "*" for that dimension.
        const starKey = `${item.attributeKey}:*`;
        return [...current.filter((value) => value !== starKey), key];
      }
      return [...current, key];
    });
  };

  /** Clears every selected row-filter suggestion key. */
  const clearRowFilterValues = () => {
    setSelected((current) => current.filter((key) => !rowFilterKeys.includes(key)));
  };

  /** Selects all currently filtered concrete row-filter values and drops their `*` keys. */
  const selectMatchedValues = () => {
    if (!matchedValues.length) return;
    setSelected((current) => {
      const next = new Set(current);
      for (const item of matchedValues) {
        next.delete(`${item.attributeKey}:*`);
        next.add(suggestionKey(item));
      }
      return [...next];
    });
  };

  /** Deselects only the currently filtered concrete row-filter values. */
  const clearMatchedValues = () => {
    const matchedKeys = new Set(matchedValues.map((item) => suggestionKey(item)));
    setSelected((current) => current.filter((key) => !matchedKeys.has(key)));
  };

  const selectedRowFilterItems = rowFilterItems.filter((item) =>
    selected.includes(suggestionKey(item)),
  );

  /** Submits one access request per selected suggestion, then clears the selection. */
  const onSubmit = async () => {
    const notes: string[] = [];
    const chosen = suggestions.filter((item) => selected.includes(suggestionKey(item)));
    for (const item of chosen) {
      const created = await create.mutateAsync({
        attributeKey: item.attributeKey,
        attributeValue: resolvedValue(item),
        justification: justification.trim(),
        requestedExpiresAt: new Date(expires).toISOString(),
      });
      const who =
        created.routedTo === "category_approvers" && created.approvers?.length
          ? created.approvers.map((a) => a.principalName).join(", ")
          : "app admins";
      notes.push(`${created.attributeKey}=${created.attributeValue} → ${who}`);
    }
    setResults(notes);
    setSelected([]);
    setValueOverrides({});
  };

  if (suggestions.length === 0) {
    return (
      <Card title="Suggested attributes">
        <Alert title="Nothing to request">
          Based on your current attributes, no additional data-access, sensitivity, or row-filter
          attributes are required for this object.
        </Alert>
      </Card>
    );
  }

  return (
    <Card title="Suggested attributes">
      <div className={styles.muted} style={{ marginBottom: "var(--space-3)" }}>
        Data-access values drive tagged-level group grants once apply mode is on. Sensitivity paths
        unmask columns; row-filter attributes unlock matching rows. For sensitivity and data access,
        change the requested value to a broader allowed path (for example{" "}
        <code>finance.budget.PII</code> instead of <code>finance.budget.PII.Name</code>) before
        submitting.
      </div>

      {dataAccessItems.length > 0 && (
        <div className={styles.stack} style={{ marginBottom: "var(--space-4)" }}>
          <div className={styles.muted}>Data access</div>
          <SuggestionTable
            label="Data access suggestions"
            items={dataAccessItems}
            selected={selected}
            onToggle={toggle}
            valueOverrides={valueOverrides}
            onValueChange={(item, value) =>
              setValueOverrides((current) => ({ ...current, [suggestionKey(item)]: value }))
            }
            allowedByKey={allowedByKey}
          />
        </div>
      )}

      {sensitivityItems.length > 0 && (
        <div className={styles.stack} style={{ marginBottom: "var(--space-4)" }}>
          <div className={styles.muted}>Sensitivity</div>
          <SuggestionTable
            label="Sensitivity suggestions"
            items={sensitivityItems}
            selected={selected}
            onToggle={toggle}
            valueOverrides={valueOverrides}
            onValueChange={(item, value) =>
              setValueOverrides((current) => ({ ...current, [suggestionKey(item)]: value }))
            }
            allowedByKey={allowedByKey}
          />
        </div>
      )}

      {rowFilterItems.length > 0 && (
        <div className={styles.stack}>
          <div className={styles.muted}>Row filter</div>
          {rowFilterStar.length > 0 && (
            <SuggestionTable
              label="Row filter wildcard"
              items={rowFilterStar}
              selected={selected}
              onToggle={toggle}
            />
          )}
          <div className={styles.formGrid}>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>Find values</span>
              <input
                className={styles.input}
                value={valueFilter}
                onChange={(event) => setValueFilter(event.target.value)}
                placeholder="Type to find values (e.g. CA, NY, TX)"
                aria-label="Find row-filter values"
              />
            </label>
          </div>
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={styles.button}
              disabled={!matchedValues.length}
              onClick={() => selectMatchedValues()}
            >
              Select matches ({matchedValues.length})
            </button>
            <button
              type="button"
              className={styles.button}
              disabled={!matchedValues.some((item) => selected.includes(suggestionKey(item)))}
              onClick={() => clearMatchedValues()}
            >
              Clear matches
            </button>
            <button type="button" className={styles.button} onClick={() => clearRowFilterValues()}>
              Clear all row-filter values
            </button>
          </div>
          {!needle && (
            <div className={styles.muted}>
              {rowFilterValues.length} concrete values available. Search to find and select the ones you
              need, or keep <code>*</code> for all rows.
            </div>
          )}
          {needle && matchedValues.length === 0 && (
            <div className={styles.empty}>No values match “{valueFilter.trim()}”.</div>
          )}
          {matchedValues.length > 0 && (
            <SuggestionTable
              label="Row filter value matches"
              items={matchedValues}
              selected={selected}
              onToggle={toggle}
            />
          )}
          {selectedRowFilterItems.length > 0 && (
            <div>
              <div className={styles.muted} style={{ marginBottom: "var(--space-2)" }}>
                Selected ({selectedRowFilterItems.length})
              </div>
              <div className={styles.buttonRow}>
                {selectedRowFilterItems.map((item) => (
                  <button
                    key={suggestionKey(item)}
                    type="button"
                    className={styles.button}
                    aria-label={`Remove ${item.attributeKey}=${item.attributeValue}`}
                    onClick={() => toggle(item)}
                  >
                    <Badge tone="warning">
                      {item.attributeKey}={item.attributeValue}
                    </Badge>{" "}
                    ×
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {requestable.length > 0 && (
        <div className={styles.stack} style={{ marginTop: "var(--space-3)" }}>
          <div className={styles.formGrid}>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>Justification</span>
              <input
                className={styles.input}
                value={justification}
                onChange={(event) => setJustification(event.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Requested expiry</span>
              <input
                className={styles.input}
                type="datetime-local"
                aria-label="Requested expiry"
                value={expires}
                onChange={(event) => setExpires(event.target.value)}
              />
            </label>
          </div>
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={
                selected.length === 0 ||
                !justification.trim() ||
                !expires ||
                create.isPending
              }
              onClick={() => void onSubmit()}
            >
              {create.isPending
                ? "Submitting…"
                : `Request ${selected.length || ""} selected`.trim()}
            </button>
            <Link className={styles.button} to="/identity?tab=my-requests">
              My requests
            </Link>
          </div>
          {create.error && <ErrorAlert error={create.error} title="Request failed" />}
          {results.length > 0 && (
            <Alert tone="success" title="Requests submitted">
              {results.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </Alert>
          )}
        </div>
      )}
    </Card>
  );
}

/** Human label for the kind of suggested attribute (shown under the attribute key). */
function kindLabelOf(item: SuggestedAttribute): string {
  return item.kind === "row_filter" ? "row filter" : item.kind === "data_access" ? "data access" : "sensitivity";
}

/**
 * Checkbox table of suggested attributes with covers paths, related columns, and reviewer routing.
 *
 * Sensitivity and data-access rows can edit the requested value to a broader allowed ancestor.
 *
 * @param props.label - Accessible name for the table.
 * @param props.items - Rows to render (already filtered by the parent).
 * @param props.selected - Currently selected `key:value` suggestion keys.
 * @param props.onToggle - Called when a requestable row's checkbox is toggled.
 * @param props.valueOverrides - Edited request values keyed by original suggestion key.
 * @param props.onValueChange - Called when the user picks a different request value.
 * @param props.allowedByKey - Attribute definition allowed values by lower-cased key.
 */
function SuggestionTable({
  label,
  items,
  selected,
  onToggle,
  valueOverrides = {},
  onValueChange,
  allowedByKey,
}: {
  label: string;
  items: SuggestedAttribute[];
  selected: string[];
  onToggle: (item: SuggestedAttribute) => void;
  valueOverrides?: Record<string, string>;
  onValueChange?: (item: SuggestedAttribute, value: string) => void;
  allowedByKey?: Map<string, string[]>;
}) {
  const suggestionColumns: TableColumn<SuggestedAttribute>[] = [
    { key: "select", label: "" },
    {
      key: "attribute",
      label: "Attribute",
      value: (item) => item.attributeKey,
      filterText: (item) => `${item.attributeKey} ${kindLabelOf(item)}`,
    },
    {
      key: "requestValue",
      label: "Request value",
      value: (item) => valueOverrides[suggestionKey(item)] ?? item.attributeValue,
    },
    {
      key: "covers",
      label: "Covers",
      value: (item) => (item.coversPaths.length ? item.coversPaths : [item.attributeValue]).join(", "),
    },
    { key: "columns", label: "Columns", value: (item) => item.columnNames.join(", ") },
    { key: "reviewedBy", label: "Reviewed by", value: (item) => routeLabel(item) },
  ];
  const view = useTableView(items, suggestionColumns, { paginate: false });
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} aria-label={label}>
        <DataTableHead view={view} />
        <tbody>
          {view.rows.map((item) => {
            const key = suggestionKey(item);
            const selectable = item.requestable;
            const editable =
              selectable &&
              onValueChange &&
              (item.kind === "sensitivity" || item.kind === "data_access");
            const options = editable
              ? editableRequestValues(
                  item.attributeValue,
                  allowedByKey?.get(item.attributeKey.toLowerCase()) ?? [],
                )
              : [item.attributeValue];
            const requested = valueOverrides[key] ?? item.attributeValue;
            const kindLabel = kindLabelOf(item);
            return (
              <tr key={`${key}:${item.isHierarchyParent}`}>
                <td>
                  {selectable ? (
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.attributeKey}=${item.attributeValue}`}
                      checked={selected.includes(key)}
                      onChange={() => onToggle(item)}
                    />
                  ) : null}
                </td>
                <td>
                  <code>{item.attributeKey}</code>
                  <div className={styles.muted} style={{ fontSize: "0.85em" }}>
                    {kindLabel}
                  </div>
                </td>
                <td>
                  {editable && options.length > 1 && selected.includes(key) ? (
                    <select
                      className={styles.select}
                      aria-label={`Request value for ${item.attributeKey}=${item.attributeValue}`}
                      value={requested}
                      onChange={(event) => onValueChange(item, event.target.value)}
                    >
                      {options.map((value) => (
                        <option key={value} value={value}>
                          {value}
                          {value.toLowerCase() === item.attributeValue.toLowerCase()
                            ? " (exact)"
                            : " (broader)"}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <>
                      <Badge tone={item.isHierarchyParent || requested !== item.attributeValue ? "info" : "warning"}>
                        {requested}
                      </Badge>
                      {item.isHierarchyParent && (
                        <div className={styles.muted} style={{ fontSize: "0.85em" }}>
                          broader path
                        </div>
                      )}
                      {editable && options.length > 1 && !selected.includes(key) && (
                        <div className={styles.muted} style={{ fontSize: "0.85em" }}>
                          Select to choose a broader path
                        </div>
                      )}
                    </>
                  )}
                </td>
                <td className={styles.muted}>
                  {(item.coversPaths.length ? item.coversPaths : [item.attributeValue]).map((path) => (
                    <code key={path} style={{ display: "block" }}>
                      {path}
                    </code>
                  ))}
                </td>
                <td>
                  {item.columnNames.map((name) => (
                    <code key={name} style={{ marginRight: 6 }}>
                      {name}
                    </code>
                  ))}
                </td>
                <td className={styles.muted}>{routeLabel(item)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Simple label/value row used in the row-filter insight card.
 *
 * @param props.label - Fixed-width field label.
 * @param props.value - Field value content.
 */
function DescriptionRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "baseline" }}>
      <span className={styles.muted} style={{ minWidth: 110 }}>
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}

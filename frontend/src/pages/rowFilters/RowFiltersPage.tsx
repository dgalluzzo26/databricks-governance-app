/**
 * @file Admin UI for row-filter dimensions, column attachments, and the metastore policy pack.
 * Tagging a column `row_filter=<dimension>` restricts rows to the viewer's matching attribute
 * values (`*` sees all; missing attribute sees none). The route is wrapped in `RequireAdmin`.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useAttachRowFilter,
  useAttributeDefinitions,
  useCurrentUser,
  useDeleteRowFilterDimension,
  useDetachRowFilter,
  useEnsureRowFilterPack,
  usePreviewSeed,
  useRefreshRowFilterDimension,
  useRowFilterAttachments,
  useRowFilterDimensions,
  useRowFilterPack,
  useUpsertRowFilterDimension,
} from "@/api/queries";
import type { RowFilterAttachment, RowFilterDimension } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import styles from "../identity/IdentityPage.module.css";

/** How a dimension's allowed values are sourced: a typed list or a seed SQL query. */
type ValueMode = "free_form" | "sql";

/**
 * Splits a free-form value list on commas and newlines, trimming empty entries.
 *
 * @param text - Raw textarea contents.
 */
function parseValues(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean);
}

/**
 * Row-filter administration: policy pack (admins), dimensions, and attached columns.
 *
 * Uses {@link useCurrentUser} to derive `isAdmin`. Assignments of dimension values (or `*`) live
 * on Identity → Assignments.
 *
 * @remarks The route is wrapped in `RequireAdmin`. Pack sync and attach/detach/edit still hide
 * when `isAdmin` is false.
 */
export function RowFiltersPage() {
  const { data: user } = useCurrentUser();
  const isAdmin = Boolean(user?.isAdmin);

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Row filters"
        description={
          <>
            Tag a column with <code>row_filter=&lt;dimension&gt;</code> and users only see rows whose value matches
            one of their <code>&lt;dimension&gt;</code> attributes. <code>*</code> grants every row; no attribute
            means no rows.
          </>
        }
      />
      {isAdmin && <PolicyPackCard />}
      <DimensionsCard isAdmin={isAdmin} />
      <AttachmentsCard isAdmin={isAdmin} />
      <Alert title="Granting access">
        Assign dimension values (or <code>*</code>) to users and groups on{" "}
        <Link to="/identity?tab=assignments">Identity → Assignments</Link>.
      </Alert>
    </div>
  );
}

/**
 * Metastore row-filter pack status and sync action.
 *
 * Loads pack state with {@link useRowFilterPack}. {@link useEnsureRowFilterPack} creates or
 * updates the governed tag, filter UDF, and ABAC policy for current dimensions.
 */
function PolicyPackCard() {
  const { data, error, isPending } = useRowFilterPack();
  const ensure = useEnsureRowFilterPack();

  return (
    <Card
      title="Policy pack"
      actions={data ? <Badge>{data.inSync ? "In sync" : "Needs sync"}</Badge> : null}
    >
      {isPending && <Spinner label="Loading policy status" />}
      {error && <ErrorAlert error={error} title="Could not load policy status" />}
      {data && (
        <div className={styles.stack}>
          <div className={styles.muted}>
            Policy <code>{data.policyName}</code> on the metastore calls <code>{data.functionName}</code> for
            columns tagged <code>{data.tagKey}</code> with:{" "}
            {data.dimensions.length ? data.dimensions.map((d) => <Badge key={d}>{d}</Badge>) : "no dimensions"}
          </div>
          {!data.policyExists && data.dimensions.length > 0 && (
            <Alert tone="warning" title="Policy missing">
              Dimensions exist but the metastore policy has not been created.
            </Alert>
          )}
          {data.missingTagValues.length > 0 && (
            <Alert tone="warning" title="Tag values missing">
              {data.missingTagValues.join(", ")} not yet allowed on <code>{data.tagKey}</code>.
            </Alert>
          )}
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={ensure.isPending}
              onClick={() => void ensure.mutateAsync()}
            >
              {ensure.isPending ? "Syncing…" : "Sync tag, function and policy"}
            </button>
          </div>
          {ensure.error && <ErrorAlert error={ensure.error} title="Sync failed" />}
        </div>
      )}
    </Card>
  );
}

/**
 * Dimension catalog: allowed values, attachment counts, and admin edit/refresh/delete.
 *
 * Lists dimensions via {@link useRowFilterDimensions}. SQL-backed dimensions can refresh values
 * with {@link useRefreshRowFilterDimension}. Deletes use {@link useDeleteRowFilterDimension}.
 *
 * @param props.isAdmin - When false, hides New/Edit/Refresh/Delete and shows a count badge.
 */
function DimensionsCard({ isAdmin }: { isAdmin: boolean }) {
  const { data, error, isPending } = useRowFilterDimensions();
  const remove = useDeleteRowFilterDimension();
  const refresh = useRefreshRowFilterDimension();
  const [editing, setEditing] = useState<RowFilterDimension | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  return (
    <Card
      title="Dimensions"
      actions={
        isAdmin ? (
          <button
            type="button"
            className={styles.button}
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            New dimension
          </button>
        ) : data ? (
          <Badge>{data.total}</Badge>
        ) : null
      }
    >
      {isPending && <Spinner label="Loading dimensions" />}
      {error && <ErrorAlert error={error} title="Could not load dimensions" />}
      {isAdmin && formOpen && (
        <DimensionForm
          key={editing?.key ?? "new"}
          initial={editing}
          onDone={() => {
            setFormOpen(false);
            setEditing(null);
          }}
        />
      )}
      {data && data.dimensions.length === 0 && (
        <div className={styles.empty}>No row filter dimensions yet.</div>
      )}
      {data && data.dimensions.length > 0 && (
        <DimensionsTable
          dimensions={data.dimensions}
          isAdmin={isAdmin}
          refreshing={refresh.isPending}
          deleting={remove.isPending}
          onEdit={(dim) => {
            setEditing(dim);
            setFormOpen(true);
          }}
          onRefresh={(key) => void refresh.mutateAsync(key)}
          onDelete={(key) => void remove.mutateAsync(key)}
        />
      )}
      {refresh.error && <ErrorAlert error={refresh.error} title="Refresh failed" />}
      {remove.error && <ErrorAlert error={remove.error} title="Delete failed" />}
    </Card>
  );
}

/**
 * Paginated table of row-filter dimensions with optional admin actions.
 */
function DimensionsTable({
  dimensions,
  isAdmin,
  refreshing,
  deleting,
  onEdit,
  onRefresh,
  onDelete,
}: {
  /** All dimensions from the API. */
  dimensions: RowFilterDimension[];
  /** When true, shows the action column. */
  isAdmin: boolean;
  /** True while a refresh mutation is in flight. */
  refreshing: boolean;
  /** True while a delete mutation is in flight. */
  deleting: boolean;
  /** Opens the dimension form for editing. */
  onEdit: (dimension: RowFilterDimension) => void;
  /** Re-runs the seed query for a SQL-backed dimension. */
  onRefresh: (key: string) => void;
  /** Deletes the dimension. */
  onDelete: (key: string) => void;
}) {
  const columns: TableColumn<(typeof dimensions)[number]>[] = [
    {
      key: "dimension",
      label: "Dimension",
      value: (dim) => dim.key,
      filterText: (dim) => `${dim.key} ${dim.description ?? ""}`,
    },
    {
      key: "values",
      label: "Values",
      value: (dim) => dim.allowedValueCount,
      filterText: (dim) => dim.sampleValues.join(" "),
    },
    { key: "columns", label: "Columns", value: (dim) => dim.attachmentCount },
    ...(isAdmin ? [{ key: "actions", label: "" }] : []),
  ];
  const pager = useTableView(dimensions, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Row filter dimensions">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((dim) => (
              <tr key={dim.key}>
                <td>
                  <code>{dim.key}</code>
                  {dim.description && <div className={styles.muted}>{dim.description}</div>}
                  <div className={styles.muted}>
                    {dim.valueSource === "sql" ? "SQL seed query" : "Free-form list"}
                  </div>
                </td>
                <td>
                  <div className={styles.chipRow}>
                    <Badge>*</Badge>
                    {dim.sampleValues.map((value) => (
                      <Badge key={value}>{value}</Badge>
                    ))}
                    {dim.allowedValueCount > dim.sampleValues.length && (
                      <span className={styles.muted}>
                        +{dim.allowedValueCount - dim.sampleValues.length} more
                      </span>
                    )}
                  </div>
                </td>
                <td>{dim.attachmentCount}</td>
                {isAdmin && (
                  <td>
                    <div className={styles.buttonRow}>
                      <button
                        type="button"
                        className={styles.button}
                        onClick={() => onEdit(dim)}
                      >
                        Edit
                      </button>
                      {dim.valueSource === "sql" && (
                        <button
                          type="button"
                          className={styles.button}
                          disabled={refreshing}
                          onClick={() => onRefresh(dim.key)}
                        >
                          Refresh values
                        </button>
                      )}
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonDanger}`}
                        disabled={deleting}
                        onClick={() => onDelete(dim.key)}
                      >
                        Delete
                      </button>
                    </div>
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
  );
}

/**
 * Loads stored allowed values from {@link useAttributeDefinitions} before rendering the form.
 *
 * While editing, waits for definitions so the free-form textarea is seeded from the identity
 * attribute (minus `*`), falling back to the dimension's sample values.
 *
 * @param props.initial - Dimension being edited, or `null` when creating.
 * @param props.onDone - Closes the form after save or cancel.
 */
function DimensionForm({ initial, onDone }: { initial: RowFilterDimension | null; onDone: () => void }) {
  const definitions = useAttributeDefinitions();
  const storedValues =
    definitions.data?.definitions
      .find((item) => item.key === initial?.key)
      ?.allowedValues.filter((value) => value !== "*") ?? initial?.sampleValues ?? [];
  return definitions.isPending && initial ? (
    <Spinner label="Loading dimension" />
  ) : (
    <DimensionFormFields initial={initial} storedValues={storedValues} onDone={onDone} />
  );
}

/**
 * Create/edit form for a dimension's key, description, and value source.
 *
 * Saves with {@link useUpsertRowFilterDimension}. SQL mode can preview distinct values via
 * {@link usePreviewSeed} before save. `*` is always an additional allowed value.
 */
function DimensionFormFields({
  initial,
  storedValues,
  onDone,
}: {
  /** Existing dimension when editing; `null` starts a new key (then editable). */
  initial: RowFilterDimension | null;
  /** Seed for the free-form list when editing a free-form dimension. */
  storedValues: string[];
  /** Closes the form after a successful save or cancel. */
  onDone: () => void;
}) {
  const upsert = useUpsertRowFilterDimension();
  const preview = usePreviewSeed();
  const [key, setKey] = useState(initial?.key ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [mode, setMode] = useState<ValueMode>(initial?.valueSource === "free_form" ? "free_form" : "sql");
  const [valuesText, setValuesText] = useState(
    initial?.valueSource === "free_form" ? storedValues.join("\n") : "",
  );
  const [valueSql, setValueSql] = useState(initial?.valueSql ?? "");

  const canSave =
    /^[a-z][a-z0-9_]{0,62}$/.test(key.trim().toLowerCase()) &&
    (mode === "sql" ? valueSql.trim().length > 0 : parseValues(valuesText).length > 0);

  /** Upserts the dimension with either parsed free-form values or a seed SQL query. */
  const onSave = async () => {
    await upsert.mutateAsync({
      key: key.trim().toLowerCase(),
      description: description.trim() || null,
      valueSource: mode,
      allowedValues: mode === "free_form" ? parseValues(valuesText) : [],
      valueSql: mode === "sql" ? valueSql.trim() : null,
    });
    onDone();
  };

  return (
    <div className={styles.approvalCard}>
      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span className={styles.label}>Dimension key</span>
          <input
            className={styles.input}
            value={key}
            disabled={Boolean(initial)}
            placeholder="state"
            onChange={(event) => setKey(event.target.value)}
          />
        </label>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Description</span>
          <input
            className={styles.input}
            value={description}
            placeholder="US state abbreviation"
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Values from</span>
          <select
            className={styles.select}
            value={mode}
            onChange={(event) => setMode(event.target.value as ValueMode)}
          >
            <option value="sql">SQL seed query</option>
            <option value="free_form">Free-form list</option>
          </select>
        </label>
      </div>
      {mode === "sql" ? (
        <label className={styles.field}>
          <span className={styles.label}>Seed query (single SELECT returning one column)</span>
          <textarea
            className={styles.textarea}
            value={valueSql}
            placeholder="SELECT DISTINCT abbreviation FROM catalog.schema.us_states"
            onChange={(event) => setValueSql(event.target.value)}
          />
        </label>
      ) : (
        <label className={styles.field}>
          <span className={styles.label}>Values (one per line or comma separated)</span>
          <textarea
            className={styles.textarea}
            value={valuesText}
            placeholder={"East\nWest"}
            onChange={(event) => setValuesText(event.target.value)}
          />
        </label>
      )}
      {mode === "sql" && preview.data && (
        <div className={styles.chipRow} aria-label="Seed preview">
          <span className={styles.muted}>{preview.data.total} values:</span>
          {preview.data.values.slice(0, 30).map((value) => (
            <Badge key={value}>{value}</Badge>
          ))}
        </div>
      )}
      <div className={styles.muted}>
        <code>*</code> (all rows) is always allowed in addition to these values.
      </div>
      <div className={styles.buttonRow}>
        {mode === "sql" && (
          <button
            type="button"
            className={styles.button}
            disabled={!valueSql.trim() || preview.isPending}
            onClick={() => void preview.mutateAsync(valueSql.trim())}
          >
            {preview.isPending ? "Running…" : "Preview values"}
          </button>
        )}
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          disabled={!canSave || upsert.isPending}
          onClick={() => void onSave()}
        >
          {upsert.isPending ? "Saving…" : initial ? "Save dimension" : "Create dimension"}
        </button>
        <button type="button" className={styles.button} onClick={onDone}>
          Cancel
        </button>
      </div>
      {preview.error && <ErrorAlert error={preview.error} title="Seed query failed" />}
      {upsert.error && <ErrorAlert error={upsert.error} title="Save failed" />}
    </div>
  );
}

/**
 * Columns tagged with a row-filter dimension, plus admin attach/detach controls.
 *
 * Lists attachments via {@link useRowFilterAttachments} and dimension options via
 * {@link useRowFilterDimensions}. {@link useAttachRowFilter} tags a fully-qualified table column;
 * {@link useDetachRowFilter} removes the tag. One dimension per table.
 *
 * @param props.isAdmin - When false, hides the attach form and detach buttons.
 */
function AttachmentsCard({ isAdmin }: { isAdmin: boolean }) {
  const { data, error, isPending } = useRowFilterAttachments();
  const dimensions = useRowFilterDimensions();
  const attach = useAttachRowFilter();
  const detach = useDetachRowFilter();
  const [tableFullName, setTableFullName] = useState("");
  const [columnName, setColumnName] = useState("");
  const [dimension, setDimension] = useState("");

  const dimensionOptions = dimensions.data?.dimensions ?? [];
  const chosenDimension = dimension || dimensionOptions[0]?.key || "";

  /** Attaches `chosenDimension` to the table.column and clears the form fields. */
  const onAttach = async () => {
    await attach.mutateAsync({
      tableFullName: tableFullName.trim(),
      columnName: columnName.trim(),
      dimension: chosenDimension,
    });
    setTableFullName("");
    setColumnName("");
  };

  return (
    <Card title="Filtered columns" actions={data ? <Badge>{data.total}</Badge> : null}>
      {isAdmin && (
        <div className={styles.formGrid}>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Table</span>
            <input
              className={styles.input}
              value={tableFullName}
              placeholder="catalog.schema.table"
              onChange={(event) => setTableFullName(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Column</span>
            <input
              className={styles.input}
              value={columnName}
              placeholder="abbreviation"
              onChange={(event) => setColumnName(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Dimension</span>
            <select
              className={styles.select}
              value={chosenDimension}
              onChange={(event) => setDimension(event.target.value)}
            >
              {dimensionOptions.map((dim) => (
                <option key={dim.key} value={dim.key}>
                  {dim.key}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={
                attach.isPending ||
                !chosenDimension ||
                tableFullName.trim().split(".").length !== 3 ||
                !columnName.trim()
              }
              onClick={() => void onAttach()}
            >
              {attach.isPending ? "Attaching…" : "Attach row filter"}
            </button>
          </div>
        </div>
      )}
      {isAdmin && (
        <div className={styles.muted}>
          One dimension per table. Once tagged, users without a matching attribute see no rows.
        </div>
      )}
      {attach.error && <ErrorAlert error={attach.error} title="Attach failed" />}
      {isPending && <Spinner label="Loading filtered columns" />}
      {error && <ErrorAlert error={error} title="Could not load filtered columns" />}
      {data && data.attachments.length === 0 && <div className={styles.empty}>No filtered columns yet.</div>}
      {data && data.attachments.length > 0 && (
        <AttachmentsTable
          attachments={data.attachments}
          isAdmin={isAdmin}
          detaching={detach.isPending}
          onDetach={(item) =>
            void detach.mutateAsync({
              tableFullName: item.tableFullName,
              columnName: item.columnName,
            })
          }
        />
      )}
      {detach.error && <ErrorAlert error={detach.error} title="Detach failed" />}
    </Card>
  );
}

/**
 * Paginated table of columns tagged with a row-filter dimension.
 */
function AttachmentsTable({
  attachments,
  isAdmin,
  detaching,
  onDetach,
}: {
  /** All attachments from the API. */
  attachments: RowFilterAttachment[];
  /** When true, shows the detach action column. */
  isAdmin: boolean;
  /** True while a detach mutation is in flight. */
  detaching: boolean;
  /** Removes the row-filter tag from the given column. */
  onDetach: (attachment: RowFilterAttachment) => void;
}) {
  const columns: TableColumn<(typeof attachments)[number]>[] = [
    { key: "table", label: "Table", value: (item) => item.tableFullName },
    { key: "column", label: "Column", value: (item) => item.columnName },
    { key: "dimension", label: "Dimension", value: (item) => item.dimension },
    ...(isAdmin ? [{ key: "actions", label: "" }] : []),
  ];
  const pager = useTableView(attachments, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Filtered columns">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={`${item.tableFullName}.${item.columnName}`}>
                <td>
                  <code>{item.tableFullName}</code>
                </td>
                <td>
                  <code>{item.columnName}</code>
                </td>
                <td>
                  <Badge>{item.dimension}</Badge>
                </td>
                {isAdmin && (
                  <td>
                    <button
                      type="button"
                      className={`${styles.button} ${styles.buttonDanger}`}
                      disabled={detaching}
                      onClick={() => onDetach(item)}
                    >
                      Detach
                    </button>
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
  );
}


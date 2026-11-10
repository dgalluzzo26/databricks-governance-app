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
import type { RowFilterDimension } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

type ValueMode = "free_form" | "sql";

function parseValues(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean);
}

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
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="Row filter dimensions">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Values</th>
                <th>Columns</th>
                {isAdmin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.dimensions.map((dim) => (
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
                          onClick={() => {
                            setEditing(dim);
                            setFormOpen(true);
                          }}
                        >
                          Edit
                        </button>
                        {dim.valueSource === "sql" && (
                          <button
                            type="button"
                            className={styles.button}
                            disabled={refresh.isPending}
                            onClick={() => void refresh.mutateAsync(dim.key)}
                          >
                            Refresh values
                          </button>
                        )}
                        <button
                          type="button"
                          className={`${styles.button} ${styles.buttonDanger}`}
                          disabled={remove.isPending}
                          onClick={() => void remove.mutateAsync(dim.key)}
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
      )}
      {refresh.error && <ErrorAlert error={refresh.error} title="Refresh failed" />}
      {remove.error && <ErrorAlert error={remove.error} title="Delete failed" />}
    </Card>
  );
}

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

function DimensionFormFields({
  initial,
  storedValues,
  onDone,
}: {
  initial: RowFilterDimension | null;
  storedValues: string[];
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
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="Filtered columns">
            <thead>
              <tr>
                <th>Table</th>
                <th>Column</th>
                <th>Dimension</th>
                {isAdmin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.attachments.map((item) => (
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
                        disabled={detach.isPending}
                        onClick={() =>
                          void detach.mutateAsync({
                            tableFullName: item.tableFullName,
                            columnName: item.columnName,
                          })
                        }
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
      )}
      {detach.error && <ErrorAlert error={detach.error} title="Detach failed" />}
    </Card>
  );
}

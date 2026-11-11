/**
 * @file Admin UI for the tag-driven data-access group dry-run.
 *
 * Configures the access and review tags, runs a planner that never creates groups or
 * Unity Catalog grants, and shows adds/revokes/conflicts versus the last run.
 */
import { useEffect, useState } from "react";
import {
  useDataAccessLatestRun,
  useDataAccessReadiness,
  useDataAccessRuns,
  useDataAccessSettings,
  useRunDataAccessSync,
  useSaveDataAccessSettings,
} from "@/api/queries";
import type { DataAccessConflictPolicy, DataAccessSettings } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

/**
 * Data-access dry-run administration: settings, readiness, Run now, and the latest plan.
 */
export function DataAccessPage() {
  const settingsQuery = useDataAccessSettings();
  const readiness = useDataAccessReadiness();
  const latest = useDataAccessLatestRun();
  const history = useDataAccessRuns();
  const save = useSaveDataAccessSettings();
  const run = useRunDataAccessSync();

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Data access"
        description="Until Unity Catalog ABAC grants exist, the app plans one group per data_access_control value and SELECT at the tagged object. This page is dry-run only: it never creates groups or changes grants."
      />
      {readiness.isPending && <Spinner label="Checking Account Admin SP" />}
      {readiness.error && <ErrorAlert error={readiness.error} title="Could not load readiness" />}
      {readiness.data && (
        <Alert
          tone={readiness.data.dryRunReady ? "success" : "warning"}
          title={
            readiness.data.dryRunReady
              ? "Dry-run ready"
              : "Dry-run is not ready — fix the failing checks on App status"
          }
        >
          Apply mode stays locked until every check passes and we turn it on.{" "}
          {readiness.data.applyReady ? "Apply probes currently pass." : "Apply probes are not all passing yet."}
        </Alert>
      )}
      {settingsQuery.isPending && <Spinner label="Loading settings" />}
      {settingsQuery.error && <ErrorAlert error={settingsQuery.error} title="Could not load settings" />}
      {settingsQuery.data && (
        <SettingsCard
          initial={settingsQuery.data.settings}
          minInterval={settingsQuery.data.minIntervalMinutes}
          saving={save.isPending}
          error={save.error}
          onSave={(body) => void save.mutateAsync(body)}
        />
      )}
      <Card
        title="Dry-run"
        actions={
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={run.isPending || readiness.data?.dryRunReady === false}
            onClick={() => void run.mutateAsync()}
          >
            {run.isPending ? "Running…" : "Run now"}
          </button>
        }
      >
        {run.error && <ErrorAlert error={run.error} title="Dry-run failed" />}
        {latest.isPending && <Spinner label="Loading latest run" />}
        {latest.error && <ErrorAlert error={latest.error} title="Could not load latest run" />}
        {latest.data == null && !latest.isPending && <p className={styles.muted}>No dry-run has been stored yet.</p>}
        {latest.data && <RunReport run={latest.data} />}
      </Card>
      {history.data && history.data.length > 0 && (
        <Card title="Recent runs">
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Add / revoke / conflicts</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((item) => (
                  <tr key={item.runId}>
                    <td>{new Date(item.startedAt).toLocaleString()}</td>
                    <td>
                      <Badge tone={item.status === "success" ? "success" : item.status === "failed" ? "danger" : "neutral"}>
                        {item.status}
                      </Badge>
                    </td>
                    <td>{item.trigger}</td>
                    <td>
                      {item.summary
                        ? `${item.summary.grantsToAdd} / ${item.summary.grantsToRevoke} / ${item.summary.conflicts}`
                        : item.error ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/**
 * Editable settings form for tags, catalogs, interval, and conflict policy.
 */
function SettingsCard(props: {
  initial: DataAccessSettings;
  minInterval: number;
  saving: boolean;
  error: Error | null;
  onSave: (body: DataAccessSettings) => void;
}) {
  const [form, setForm] = useState(props.initial);
  useEffect(() => {
    setForm(props.initial);
  }, [props.initial]);

  return (
    <Card title="Settings">
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault();
          props.onSave({
            ...form,
            allowedCatalogs: form.allowedCatalogs,
            dryRun: true,
          });
        }}
      >
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Access tag</span>
            <input
              className={styles.input}
              value={form.accessTagKey}
              onChange={(e) => setForm({ ...form, accessTagKey: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Review tag</span>
            <input
              className={styles.input}
              value={form.reviewTagKey}
              onChange={(e) => setForm({ ...form, reviewTagKey: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Review value</span>
            <input
              className={styles.input}
              value={form.reviewTagValue}
              onChange={(e) => setForm({ ...form, reviewTagValue: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Group prefix</span>
            <input
              className={styles.input}
              value={form.groupPrefix}
              onChange={(e) => setForm({ ...form, groupPrefix: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Hierarchy separator</span>
            <input
              className={styles.input}
              value={form.hierarchySeparator}
              onChange={(e) => setForm({ ...form, hierarchySeparator: e.target.value })}
              maxLength={1}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>{`Interval (minutes, min ${props.minInterval})`}</span>
            <input
              className={styles.input}
              type="number"
              min={props.minInterval}
              value={form.intervalMinutes}
              onChange={(e) => setForm({ ...form, intervalMinutes: Number(e.target.value) })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Conflict policy</span>
            <select
              className={styles.select}
              value={form.conflictPolicy}
              onChange={(e) =>
                setForm({ ...form, conflictPolicy: e.target.value as DataAccessConflictPolicy })
              }
            >
              <option value="hold">Hold ancestor grant (fail closed)</option>
              <option value="warn">Warn but keep ancestor grant</option>
            </select>
          </label>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Allowed catalogs (comma-separated; empty = all)</span>
            <input
              className={styles.input}
              value={form.allowedCatalogs.join(", ")}
              onChange={(e) =>
                setForm({
                  ...form,
                  allowedCatalogs: e.target.value
                    .split(",")
                    .map((part) => part.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
        </div>
        <div className={styles.buttonRow}>
          <button type="submit" className={`${styles.button} ${styles.buttonPrimary}`} disabled={props.saving}>
            {props.saving ? "Saving…" : "Save settings"}
          </button>
        </div>
        {props.error && <ErrorAlert error={props.error} title="Could not save settings" />}
      </form>
    </Card>
  );
}

/**
 * KPI summary plus truncated grant/group/conflict tables for one run.
 */
function RunReport({ run }: { run: import("@/api/types").DataAccessRun }) {
  const plan = run.plan;
  const summary = run.summary ?? plan?.summary;
  return (
    <div className={styles.stack}>
      <div className={styles.muted}>
        {run.status} · {run.trigger} · {new Date(run.startedAt).toLocaleString()}
        {run.triggeredBy ? ` · ${run.triggeredBy}` : ""}
      </div>
      {run.error && <Alert tone="danger" title="Run failed">{run.error}</Alert>}
      {summary && (
        <p>
          Eligible objects {summary.eligibleObjects}. Unreviewed skipped {summary.skippedUnreviewed}. Groups to
          create {summary.groupsToCreate}. Grants to add {summary.grantsToAdd}, revoke {summary.grantsToRevoke},
          unchanged {summary.grantsUnchanged}. Conflicts {summary.conflicts}.
        </p>
      )}
      {plan?.driftSinceLastRun.map((note) => (
        <div key={note} className={styles.muted}>
          {note}
        </div>
      ))}
      {plan?.truncated && <Alert tone="warning" title="Lists truncated">Showing the first 200 rows in each table.</Alert>}
      {plan && plan.groups.length > 0 && (
        <GrantTable
          caption="Groups"
          headers={["Group", "Value", "Parent", "Exists"]}
          rows={plan.groups.map((g) => [g.name, g.value, g.parentGroup ?? "—", g.exists ? "yes" : "would create"])}
        />
      )}
      {plan && plan.grantsToAdd.length > 0 && (
        <GrantTable
          caption="Would grant"
          headers={["Type", "Object", "Group", "Privilege"]}
          rows={plan.grantsToAdd.map((g) => [g.securableType, g.fullName, g.principal, g.privilege])}
        />
      )}
      {plan && plan.grantsToRevoke.length > 0 && (
        <GrantTable
          caption="Would revoke"
          headers={["Type", "Object", "Group", "Privilege"]}
          rows={plan.grantsToRevoke.map((g) => [g.securableType, g.fullName, g.principal, g.privilege])}
        />
      )}
      {plan && plan.conflicts.length > 0 && (
        <GrantTable
          caption="Conflicts"
          headers={["Ancestor", "Value", "Descendant", "Reason"]}
          rows={plan.conflicts.map((c) => [
            `${c.ancestorType} ${c.ancestorName}`,
            c.ancestorValue,
            c.descendantName,
            c.reason,
          ])}
        />
      )}
    </div>
  );
}

function GrantTable(props: { caption: string; headers: string[]; rows: string[][] }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} aria-label={props.caption}>
        <caption className={styles.label}>{props.caption}</caption>
        <thead>
          <tr>
            {props.headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row, index) => (
            <tr key={`${row[0]}-${index}`}>
              {row.map((cell) => (
                <td key={cell}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

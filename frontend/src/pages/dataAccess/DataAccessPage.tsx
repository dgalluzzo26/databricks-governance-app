/**
 * @file Admin UI for the tag-driven data-access group planner.
 *
 * Dry-run tab: configure schedule, run the planner, inspect dry-run logs.
 * Live tab: apply readiness, manual apply, live-only logs.
 * Schedule can pause, dry-run only, or plan-then-apply each interval.
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useDataAccessLatestRun,
  useDataAccessReadiness,
  useDataAccessRuns,
  useDataAccessSettings,
  useRunDataAccessSync,
  useSaveDataAccessSettings,
} from "@/api/queries";
import type {
  DataAccessConflictPolicy,
  DataAccessRun,
  DataAccessRunList,
  DataAccessSettings,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

type DataAccessTab = "dry-run" | "live";

/**
 * Data-access administration with Dry-run and Live tabs.
 */
export function DataAccessPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = (searchParams.get("tab") as DataAccessTab | null) ?? "dry-run";
  const tab: DataAccessTab = requested === "live" ? "live" : "dry-run";
  const [dryPage, setDryPage] = useState(1);
  const [livePage, setLivePage] = useState(1);

  const setTab = (next: DataAccessTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  const settingsQuery = useDataAccessSettings();
  const readiness = useDataAccessReadiness();
  const dryLatest = useDataAccessLatestRun("dry_run");
  const liveLatest = useDataAccessLatestRun("apply");
  const dryHistory = useDataAccessRuns("dry_run", dryPage);
  const liveHistory = useDataAccessRuns("apply", livePage);
  const save = useSaveDataAccessSettings();
  const run = useRunDataAccessSync();

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Data access"
        description="Plans one nested group per data_access_control value and SELECT only on reviewed tagged tables (plus USE CATALOG/SCHEMA). Schema/catalog tags still seed parent groups for nesting."
      />

      <div className={styles.tabs} role="tablist" aria-label="Data access views">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "dry-run"}
          className={`${styles.tab}${tab === "dry-run" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("dry-run")}
        >
          Dry-run
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "live"}
          className={`${styles.tab}${tab === "live" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("live")}
        >
          Live
        </button>
      </div>

      {tab === "dry-run" && (
        <>
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
              Dry-run never creates groups or grants. Use{" "}
              <button type="button" className={styles.linkButton} onClick={() => setTab("live")}>
                Live
              </button>{" "}
              for apply, or set the schedule mode to Apply so each interval plans then mutates.{" "}
              {!readiness.data.dryRunReady && <Link to="/status">Open App status</Link>}
            </Alert>
          )}
          {settingsQuery.isPending && <Spinner label="Loading settings" />}
          {settingsQuery.error && (
            <ErrorAlert error={settingsQuery.error} title="Could not load settings" />
          )}
          {settingsQuery.data && (
            <SettingsCard
              initial={settingsQuery.data.settings}
              minInterval={settingsQuery.data.minIntervalMinutes}
              applyReady={readiness.data?.applyReady === true && readiness.data?.setupReady === true}
              saving={save.isPending}
              error={save.error}
              onSave={(body) => void save.mutateAsync(body)}
            />
          )}
          <Card
            title="Manual sync (dry-run)"
            actions={
              <button
                type="button"
                className={`${styles.button} ${styles.buttonPrimary}`}
                disabled={run.isPending || readiness.data?.dryRunReady === false}
                onClick={() => void run.mutateAsync("dry_run")}
              >
                {run.isPending ? "Running…" : "Run now"}
              </button>
            }
          >
            <p className={styles.muted}>
              Plans groups, nesting, Identity→membership diffs, and table SELECT grants without
              mutating Unity Catalog.
            </p>
            {run.error && <ErrorAlert error={run.error} title="Dry-run failed" />}
            {dryLatest.isPending && <Spinner label="Loading latest dry-run" />}
            {dryLatest.error && (
              <ErrorAlert error={dryLatest.error} title="Could not load latest dry-run" />
            )}
            {dryLatest.data == null && !dryLatest.isPending && (
              <p className={styles.muted}>No dry-run has been stored yet.</p>
            )}
            {dryLatest.data && <RunReport run={dryLatest.data} />}
          </Card>
          <RunHistoryCard
            title="Dry-run logs"
            emptyHint="No dry-run logs in the last 30 days."
            history={dryHistory.data}
            isPending={dryHistory.isPending}
            error={dryHistory.error}
            page={dryPage}
            onPageChange={setDryPage}
          />
        </>
      )}

      {tab === "live" && (
        <LiveTab
          readiness={readiness}
          applyAvailable={settingsQuery.data?.applyAvailable === true}
          setupReady={readiness.data?.setupReady === true}
          history={liveHistory.data}
          historyPending={liveHistory.isPending}
          historyError={liveHistory.error}
          page={livePage}
          onPageChange={setLivePage}
          latest={liveLatest.data}
          latestPending={liveLatest.isPending}
          scheduleMode={settingsQuery.data?.settings.scheduleMode}
          scheduleEnabled={settingsQuery.data?.settings.scheduleEnabled}
          run={run}
        />
      )}
    </div>
  );
}

/**
 * Live apply: readiness, membership preview from latest apply plan, confirm + Run apply.
 */
function LiveTab(props: {
  readiness: ReturnType<typeof useDataAccessReadiness>;
  applyAvailable: boolean;
  setupReady: boolean;
  history: DataAccessRunList | undefined;
  historyPending: boolean;
  historyError: Error | null;
  page: number;
  onPageChange: (page: number) => void;
  latest: DataAccessRun | null | undefined;
  latestPending: boolean;
  scheduleMode: "dry_run" | "apply" | undefined;
  scheduleEnabled: boolean | undefined;
  run: ReturnType<typeof useRunDataAccessSync>;
}) {
  const {
    readiness,
    applyAvailable,
    setupReady,
    history,
    historyPending,
    historyError,
    page,
    onPageChange,
    latest,
    latestPending,
    scheduleMode,
    scheduleEnabled,
    run,
  } = props;
  const canApply =
    applyAvailable && readiness.data?.applyReady === true && setupReady && !run.isPending;
  const scheduleApplies = scheduleEnabled === true && scheduleMode === "apply";

  return (
    <div className={styles.stack}>
      {readiness.isPending && <Spinner label="Checking apply readiness" />}
      {readiness.error && <ErrorAlert error={readiness.error} title="Could not load readiness" />}
      {readiness.data && (
        <Alert
          tone={canApply ? "success" : "warning"}
          title={canApply ? "Apply ready" : "Apply not ready"}
        >
          {canApply
            ? "Account Admin SP probes and Identity definitions look good. Apply will mutate groups, membership, and grants."
            : "Fix failing checks on App status (Account Admin SP, warehouse, definitions) before applying."}{" "}
          <Link to="/status">Open App status</Link>
        </Alert>
      )}

      <Card title="Schedule">
        {scheduleApplies ? (
          <Alert tone="success" title="Scheduled apply is on">
            Every interval the scheduler inventories tags, builds a plan, then mutates groups /
            membership / grants — the same path as Run apply. Logs appear below (apply only).
          </Alert>
        ) : (
          <p className={styles.muted}>
            Scheduled apply is off
            {scheduleEnabled === false
              ? " (schedule paused)."
              : " (schedule mode is Dry-run)."}{" "}
            Turn it on under Dry-run → Settings → Schedule mode = Apply.
          </p>
        )}
      </Card>

      <Card title="Before you apply">
        <ul className={styles.list}>
          <li>
            Each apply (manual or scheduled) plans first, then creates/nests groups, syncs
            membership, and GRANT/REVOKE.
          </li>
          <li>
            Identity <code>data_access</code> assignments drive who is added to each{" "}
            <code>dac_*</code> group. Review holders in{" "}
            <Link to="/attribute-explorer">Attribute explorer</Link>.
          </li>
          <li>Path approvers are optional for requests; they do not gate apply.</li>
        </ul>
      </Card>

      <Card
        title="Apply now"
        actions={
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!canApply}
            onClick={() => {
              if (
                window.confirm(
                  "Apply will create/nest dac_* groups, sync membership from Identity, and GRANT/REVOKE table access. Continue?",
                )
              ) {
                void run.mutateAsync("apply");
              }
            }}
          >
            {run.isPending ? "Applying…" : "Run apply"}
          </button>
        }
      >
        <p className={styles.muted}>
          Order: create groups → nest parents into children → sync members from Identity assignments
          → GRANT adds → REVOKE stale managed grants.
        </p>
        {run.error && <ErrorAlert error={run.error} title="Apply failed" />}
        {latestPending && <Spinner label="Loading latest apply" />}
        {latest?.plan && (
          <>
            <p>
              Latest apply plan: members +{latest.plan.summary.membersToAdd ?? 0} / −
              {latest.plan.summary.membersToRemove ?? 0}; grants +{latest.plan.summary.grantsToAdd}{" "}
              / −{latest.plan.summary.grantsToRevoke}; groups to create{" "}
              {latest.plan.summary.groupsToCreate}.
            </p>
            {(latest.plan.membershipChanges?.length ?? 0) > 0 && (
              <GrantTable
                caption="Membership changes"
                headers={["Action", "Group", "Principal", "Type", "Reason"]}
                rows={latest.plan.membershipChanges.map((m) => [
                  m.action,
                  m.group,
                  m.principalName,
                  m.principalType,
                  m.reason,
                ])}
              />
            )}
            {(latest.plan.applyErrors?.length ?? 0) > 0 && (
              <Alert tone="warning" title="Apply reported step errors">
                {latest.plan.applyErrors.join("; ")}
              </Alert>
            )}
          </>
        )}
      </Card>

      <RunHistoryCard
        title="Live apply logs"
        emptyHint="No apply logs in the last 30 days."
        history={history}
        isPending={historyPending}
        error={historyError}
        page={page}
        onPageChange={onPageChange}
      />
    </div>
  );
}

function RunHistoryCard(props: {
  history: DataAccessRunList | undefined;
  title: string;
  emptyHint: string;
  isPending: boolean;
  error: Error | null;
  page: number;
  onPageChange: (page: number) => void;
}) {
  const { history, title, emptyHint, isPending, error, page, onPageChange } = props;
  const totalPages = history ? Math.max(1, Math.ceil(history.total / history.pageSize)) : 1;

  return (
    <Card title={title}>
      {isPending && <Spinner label="Loading logs" />}
      {error && <ErrorAlert error={error} title="Could not load logs" />}
      {!isPending && !error && (!history || history.runs.length === 0) && (
        <p className={styles.muted}>{emptyHint}</p>
      )}
      {history && history.runs.length > 0 && (
        <>
          <p className={styles.muted}>
            Last {history.retentionDays} days · {history.total} run
            {history.total === 1 ? "" : "s"} · page {history.page} of {totalPages}
          </p>
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label={title}>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Add / revoke / conflicts</th>
                </tr>
              </thead>
              <tbody>
                {history.runs.map((item) => (
                  <tr key={item.runId}>
                    <td>{new Date(item.startedAt).toLocaleString()}</td>
                    <td>
                      <Badge
                        tone={
                          item.status === "success"
                            ? "success"
                            : item.status === "failed"
                              ? "danger"
                              : "neutral"
                        }
                      >
                        {item.status}
                      </Badge>
                    </td>
                    <td>{item.trigger}</td>
                    <td>
                      {item.summary
                        ? `${item.summary.grantsToAdd} / ${item.summary.grantsToRevoke} / ${item.summary.conflicts}`
                        : (item.error ?? "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={styles.button}
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              Previous
            </button>
            <button
              type="button"
              className={styles.button}
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </Card>
  );
}

/**
 * Editable settings form for tags, catalogs, interval, schedule, and conflict policy.
 */
function SettingsCard(props: {
  initial: DataAccessSettings;
  minInterval: number;
  applyReady: boolean;
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
            <span className={styles.label}>Schedule</span>
            <select
              className={styles.select}
              value={form.scheduleEnabled ? "on" : "paused"}
              onChange={(e) =>
                setForm({ ...form, scheduleEnabled: e.target.value === "on" })
              }
              aria-label="Schedule"
            >
              <option value="on">Enabled</option>
              <option value="paused">Paused</option>
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Schedule mode</span>
            <select
              className={styles.select}
              value={form.scheduleMode}
              onChange={(e) =>
                setForm({
                  ...form,
                  scheduleMode: e.target.value as "dry_run" | "apply",
                })
              }
              aria-label="Schedule mode"
              title="Apply still plans first, then mutates Unity Catalog"
            >
              <option value="dry_run">Dry-run (plan only)</option>
              <option value="apply" disabled={!props.applyReady}>
                Apply (plan then mutate)
              </option>
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Conflict reporting</span>
            <select
              className={styles.select}
              value={form.conflictPolicy}
              onChange={(e) =>
                setForm({ ...form, conflictPolicy: e.target.value as DataAccessConflictPolicy })
              }
              title="SELECT is table-only; this only controls how parent/child tag mismatches are labeled in the plan"
            >
              <option value="hold">Report mismatches (hold)</option>
              <option value="warn">Report mismatches (warn)</option>
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
        {form.scheduleEnabled && form.scheduleMode === "apply" && (
          <Alert tone="warning" title="Scheduled apply will mutate Unity Catalog">
            Each interval invents the desired state from tags + Identity, then creates groups,
            syncs members, and GRANT/REVOKE. Pause the schedule or switch mode to Dry-run to stop.
          </Alert>
        )}
        <div className={styles.buttonRow}>
          <button
            type="submit"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={props.saving}
          >
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
function RunReport({ run }: { run: DataAccessRun }) {
  const plan = run.plan;
  const summary = run.summary ?? plan?.summary;
  return (
    <div className={styles.stack}>
      <div className={styles.muted}>
        {run.status} · {run.mode} · {run.trigger} · {new Date(run.startedAt).toLocaleString()}
        {run.triggeredBy ? ` · ${run.triggeredBy}` : ""}
      </div>
      {run.error && <Alert tone="danger" title="Run failed">{run.error}</Alert>}
      {summary && (
        <p>
          Eligible objects {summary.eligibleObjects}. Unreviewed skipped {summary.skippedUnreviewed}.
          Groups to create {summary.groupsToCreate}. Members to add {summary.membersToAdd ?? 0},
          remove {summary.membersToRemove ?? 0}. Grants to add {summary.grantsToAdd}, revoke{" "}
          {summary.grantsToRevoke}, unchanged {summary.grantsUnchanged}. Conflicts {summary.conflicts}.
        </p>
      )}
      {plan?.driftSinceLastRun.map((note) => (
        <div key={note} className={styles.muted}>
          {note}
        </div>
      ))}
      {plan?.truncated && (
        <Alert tone="warning" title="Lists truncated">
          Showing the first 200 rows in each table.
        </Alert>
      )}
      {(plan?.applyErrors?.length ?? 0) > 0 && (
        <Alert tone="warning" title="Apply step errors">
          {plan!.applyErrors.join("; ")}
        </Alert>
      )}
      {plan && plan.groups.length > 0 && (
        <GrantTable
          caption="Groups"
          headers={["Group", "Value", "Parent", "Exists"]}
          rows={plan.groups.map((g) => [
            g.name,
            g.value,
            g.parentGroup ?? "—",
            g.exists ? "yes" : "would create",
          ])}
        />
      )}
      {plan && (plan.membershipChanges?.length ?? 0) > 0 && (
        <GrantTable
          caption="Membership"
          headers={["Action", "Group", "Principal", "Type", "Reason"]}
          rows={plan.membershipChanges.map((m) => [
            m.action,
            m.group,
            m.principalName,
            m.principalType,
            m.reason,
          ])}
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
          rows={plan.grantsToRevoke.map((g) => [
            g.securableType,
            g.fullName,
            g.principal,
            g.privilege,
          ])}
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

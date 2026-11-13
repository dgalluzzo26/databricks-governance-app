/**
 * @file Admin UI for the tag-driven data-access group planner.
 *
 * Dry-run tab: configure schedule, run the planner, inspect dry-run logs.
 * Live tab: apply readiness, manual apply, live-only logs.
 * Outstanding work tab: the full list of changes the next sync still has to make, plus tag changes.
 * Schedule is a Databricks Job; Run now triggers that Job and waits.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useDataAccessLatestRun,
  useDataAccessReadiness,
  useDataAccessRun,
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
import { OutstandingWorkPanel } from "./DataAccessOutstanding";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useServerTableView, type ServerTableView } from "@/hooks/useServerTableView";

type DataAccessTab = "dry-run" | "live" | "outstanding";

/**
 * Data-access administration with Dry-run, Live, and Outstanding work tabs.
 */
export function DataAccessPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = (searchParams.get("tab") as DataAccessTab | null) ?? "dry-run";
  const tab: DataAccessTab =
    requested === "live" || requested === "outstanding" ? requested : "dry-run";
  const [dryPage, setDryPage] = useState(1);
  const [livePage, setLivePage] = useState(1);
  const [drySelectedId, setDrySelectedId] = useState<string | null>(null);
  const [liveSelectedId, setLiveSelectedId] = useState<string | null>(null);

  const setTab = (next: DataAccessTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  const settingsQuery = useDataAccessSettings();
  const readiness = useDataAccessReadiness();
  const dryLatest = useDataAccessLatestRun("dry_run");
  const liveLatest = useDataAccessLatestRun("apply");
  const dryView = useServerTableView(RUN_HISTORY_COLUMNS, { onChange: () => setDryPage(1) });
  const liveView = useServerTableView(RUN_HISTORY_COLUMNS, { onChange: () => setLivePage(1) });
  const dryHistory = useDataAccessRuns("dry_run", dryPage, dryView.query);
  const liveHistory = useDataAccessRuns("apply", livePage, liveView.query);
  const drySelected = useDataAccessRun(drySelectedId);
  const liveSelected = useDataAccessRun(liveSelectedId);
  const save = useSaveDataAccessSettings();
  const run = useRunDataAccessSync();
  const dryReport = resolveDisplayedRun(dryLatest.data, drySelectedId, drySelected.data);
  const liveReport = resolveDisplayedRun(liveLatest.data, liveSelectedId, liveSelected.data);

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
        <button
          type="button"
          role="tab"
          aria-selected={tab === "outstanding"}
          className={`${styles.tab}${tab === "outstanding" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("outstanding")}
        >
          Outstanding work
        </button>
      </div>

      {tab === "outstanding" && <OutstandingWorkPanel />}

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
              enrolledCatalogs={settingsQuery.data.enrolledCatalogs}
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
                onClick={() =>
                  void run.mutateAsync("dry_run").then((finished) => setDrySelectedId(finished.runId))
                }
              >
                {run.isPending ? "Running…" : "Run now"}
              </button>
            }
          >
            <p className={styles.muted}>
              Starts the sync Job and waits for it to finish. Plans groups, nesting,
              Identity→membership diffs, and table SELECT grants without mutating Unity Catalog.
            </p>
            {run.error && <ErrorAlert error={run.error} title="Dry-run failed" />}
            {(dryLatest.isPending || (drySelectedId !== null && drySelected.isPending)) && (
              <Spinner label="Loading dry-run" />
            )}
            {dryLatest.error && (
              <ErrorAlert error={dryLatest.error} title="Could not load latest dry-run" />
            )}
            {drySelected.error && (
              <ErrorAlert error={drySelected.error} title="Could not load selected dry-run" />
            )}
            {dryLatest.data == null && !dryLatest.isPending && (
              <p className={styles.muted}>No dry-run has been stored yet.</p>
            )}
            {dryReport && <RunReport run={dryReport} />}
          </Card>
          <RunHistoryCard
            title="Dry-run logs"
            emptyHint="No dry-run logs in the last 30 days."
            history={dryHistory.data}
            view={dryView}
            isPending={dryHistory.isPending}
            error={dryHistory.error}
            page={dryPage}
            onPageChange={setDryPage}
            selectedRunId={drySelectedId ?? dryLatest.data?.runId ?? null}
            onSelectRun={setDrySelectedId}
          />
        </>
      )}

      {tab === "live" && (
        <LiveTab
          readiness={readiness}
          applyAvailable={settingsQuery.data?.applyAvailable === true}
          setupReady={readiness.data?.setupReady === true}
          history={liveHistory.data}
          historyView={liveView}
          historyPending={liveHistory.isPending}
          historyError={liveHistory.error}
          page={livePage}
          onPageChange={setLivePage}
          latestPending={liveLatest.isPending}
          displayed={liveReport}
          selectedPending={liveSelectedId !== null && liveSelected.isPending}
          selectedError={liveSelected.error}
          selectedRunId={liveSelectedId ?? liveLatest.data?.runId ?? null}
          onSelectRun={setLiveSelectedId}
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
/** Run-history columns. Keys match the backend `RUN_HISTORY_READERS` whitelist. */
const RUN_HISTORY_COLUMNS: TableColumn<unknown>[] = [
  { key: "when", label: "When", sortable: true, filterable: true },
  { key: "elapsed", label: "Elapsed", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "trigger", label: "Trigger", sortable: true, filterable: true },
  { key: "changes", label: "Add / revoke / conflicts", sortable: true, filterable: true },
];

function LiveTab(props: {
  readiness: ReturnType<typeof useDataAccessReadiness>;
  applyAvailable: boolean;
  setupReady: boolean;
  history: DataAccessRunList | undefined;
  historyView: ServerTableView;
  historyPending: boolean;
  historyError: Error | null;
  page: number;
  onPageChange: (page: number) => void;
  latestPending: boolean;
  displayed: DataAccessRun | null | undefined;
  selectedPending: boolean;
  selectedError: Error | null;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  scheduleMode: "dry_run" | "apply" | undefined;
  scheduleEnabled: boolean | undefined;
  run: ReturnType<typeof useRunDataAccessSync>;
}) {
  const {
    readiness,
    applyAvailable,
    setupReady,
    history,
    historyView,
    historyPending,
    historyError,
    page,
    onPageChange,
    latestPending,
    displayed,
    selectedPending,
    selectedError,
    selectedRunId,
    onSelectRun,
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
            Every Job interval inventories tags, builds a plan, then mutates groups /
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
                void run.mutateAsync("apply").then((finished) => onSelectRun(finished.runId));
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
        {(latestPending || selectedPending) && <Spinner label="Loading apply" />}
        {selectedError && <ErrorAlert error={selectedError} title="Could not load selected apply" />}
        {displayed && <RunReport run={displayed} />}
      </Card>

      <RunHistoryCard
        title="Live apply logs"
        emptyHint="No apply logs in the last 30 days."
        history={history}
        view={historyView}
        isPending={historyPending}
        error={historyError}
        page={page}
        onPageChange={onPageChange}
        selectedRunId={selectedRunId}
        onSelectRun={onSelectRun}
      />
    </div>
  );
}

function RunHistoryCard(props: {
  history: DataAccessRunList | undefined;
  view: ServerTableView;
  title: string;
  emptyHint: string;
  isPending: boolean;
  error: Error | null;
  page: number;
  onPageChange: (page: number) => void;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  const {
    history,
    view,
    title,
    emptyHint,
    isPending,
    error,
    page,
    onPageChange,
    selectedRunId,
    onSelectRun,
  } = props;
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
              <DataTableHead view={view} />
              <tbody>
                {history.runs.map((item) => (
                  <tr
                    key={item.runId}
                    className={`${styles.tableRowClickable}${
                      selectedRunId === item.runId ? ` ${styles.tableRowSelected}` : ""
                    }`}
                    tabIndex={0}
                    role="button"
                    aria-pressed={selectedRunId === item.runId}
                    aria-label={`View run from ${new Date(item.startedAt).toLocaleString()}`}
                    onClick={() => onSelectRun(item.runId)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelectRun(item.runId);
                      }
                    }}
                  >
                    <td>{new Date(item.startedAt).toLocaleString()}</td>
                    <td>{formatElapsed(item)}</td>
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
 * Editable settings form for tags, enrollment, interval, schedule, and conflict policy.
 */
function SettingsCard(props: {
  initial: DataAccessSettings;
  enrolledCatalogs: string[];
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
            <span className={styles.label}>Enrollment catalog tag</span>
            <input
              className={styles.input}
              value={form.enrollmentCatalogTagKey}
              onChange={(e) => setForm({ ...form, enrollmentCatalogTagKey: e.target.value })}
              aria-label="Enrollment catalog tag"
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Enrollment tag value</span>
            <input
              className={styles.input}
              value={form.enrollmentCatalogTagValue}
              onChange={(e) => setForm({ ...form, enrollmentCatalogTagValue: e.target.value })}
              aria-label="Enrollment tag value"
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
            <span className={styles.muted}>
              Saved to the sync Job schedule (snapped to a cron-friendly step such as 5, 15, 30, or
              60).
            </span>
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
              title="Unused: SELECT is table-only, so catalog/schema tags do not create conflicts"
            >
              <option value="hold">Unused (hold)</option>
              <option value="warn">Unused (warn)</option>
            </select>
          </label>
          <div className={styles.fieldGrow}>
            <span className={styles.label}>Enrolled catalogs</span>
            <p className={styles.muted}>
              Catalogs tagged{" "}
              <code>
                {form.enrollmentCatalogTagKey}={form.enrollmentCatalogTagValue}
              </code>
              . Access Explorer, sync, and ABAC only include these. Unset the tag to unenroll.
            </p>
            {props.enrolledCatalogs.length === 0 ? (
              <p className={styles.muted}>No catalogs currently carry this tag.</p>
            ) : (
              <ul className={styles.muted} aria-label="Enrolled catalogs">
                {props.enrolledCatalogs.map((name) => (
                  <li key={name}>
                    <code>{name}</code>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {form.scheduleEnabled && form.scheduleMode === "apply" && (
          <Alert tone="warning" title="Scheduled apply will mutate Unity Catalog">
            Each Job interval invents the desired state from tags + Identity, then creates groups,
            syncs members, and GRANT/REVOKE. Pause the schedule or switch mode to Dry-run to stop.
            The Job keeps running even if the app restarts.
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
 * Summary-first report for one run. Detail tables stay collapsed as small samples.
 */
function RunReport({ run }: { run: DataAccessRun }) {
  const plan = run.plan;
  const summary = run.summary ?? plan?.summary;
  const grantSample = summarizeGrantSample(plan?.grantsToAdd ?? []);
  const revokeSample = summarizeGrantSample(plan?.grantsToRevoke ?? []);

  return (
    <div className={styles.stack}>
      <div className={styles.muted}>
        {run.status} · {run.mode} · {run.trigger} · {new Date(run.startedAt).toLocaleString()}
        {` · ${formatElapsed(run)}`}
        {run.triggeredBy ? ` · ${run.triggeredBy}` : ""}
      </div>
      {run.error && <Alert tone="danger" title="Run failed">{run.error}</Alert>}
      {summary && (
        <div className={styles.formGrid} aria-label="Plan summary">
          <SummaryStat label="Eligible objects" value={summary.eligibleObjects} />
          <SummaryStat label="Unreviewed skipped" value={summary.skippedUnreviewed} />
          <SummaryStat label="Groups to create" value={summary.groupsToCreate} />
          <SummaryStat label="Groups existing" value={summary.groupsExisting} />
          <SummaryStat label="Members +" value={summary.membersToAdd ?? 0} />
          <SummaryStat label="Members −" value={summary.membersToRemove ?? 0} />
          <SummaryStat label="Grants +" value={summary.grantsToAdd} />
          <SummaryStat label="Grants −" value={summary.grantsToRevoke} />
          <SummaryStat label="Grants unchanged" value={summary.grantsUnchanged} />
          <SummaryStat label="Conflicts" value={summary.conflicts} />
        </div>
      )}
      {plan?.driftSinceLastRun.map((note) => (
        <div key={note} className={styles.muted}>
          {note}
        </div>
      ))}
      {(plan?.applyErrors?.length ?? 0) > 0 && (
        <Alert tone="warning" title="Apply step errors">
          {plan!.applyErrors.join("; ")}
        </Alert>
      )}
      {plan && (
        <p className={styles.muted}>
          Detail tables below are samples only
          {plan.truncated ? " (full counts are in the summary above)." : "."} At scale, use the
          summary — listing every grant would be thousands of rows.
        </p>
      )}
      {plan && plan.groups.length > 0 && (
        <SampleDetails
          title={`Groups sample (${plan.groups.length}${summary ? ` of ${summary.groupsToCreate + summary.groupsExisting}` : ""})`}
        >
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
        </SampleDetails>
      )}
      {plan && (plan.membershipChanges?.length ?? 0) > 0 && (
        <SampleDetails
          title={`Membership sample (${plan.membershipChanges.length}${
            summary
              ? ` of ${(summary.membersToAdd ?? 0) + (summary.membersToRemove ?? 0)}`
              : ""
          })`}
        >
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
        </SampleDetails>
      )}
      {plan && plan.grantsToAdd.length > 0 && (
        <SampleDetails
          title={`Grants to add sample (${plan.grantsToAdd.length}${
            summary ? ` of ${summary.grantsToAdd}` : ""
          }${grantSample ? ` · ${grantSample}` : ""})`}
        >
          <GrantTable
            caption="Would grant"
            headers={["Type", "Object", "Group", "Privilege"]}
            rows={plan.grantsToAdd.map((g) => [
              g.securableType,
              g.fullName,
              g.principal,
              g.privilege,
            ])}
          />
        </SampleDetails>
      )}
      {plan && plan.grantsToRevoke.length > 0 && (
        <SampleDetails
          title={`Grants to revoke sample (${plan.grantsToRevoke.length}${
            summary ? ` of ${summary.grantsToRevoke}` : ""
          }${revokeSample ? ` · ${revokeSample}` : ""})`}
        >
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
        </SampleDetails>
      )}
      {plan && plan.conflicts.length > 0 && (
        <SampleDetails
          title={`Conflicts sample (${plan.conflicts.length}${
            summary ? ` of ${summary.conflicts}` : ""
          })`}
        >
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
        </SampleDetails>
      )}
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className={styles.field}>
      <span className={styles.label}>{label}</span>
      <div style={{ fontSize: "1.25rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function SampleDetails({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details>
      <summary className={styles.label} style={{ cursor: "pointer" }}>
        {title}
      </summary>
      <div style={{ marginTop: "var(--space-2)" }}>{children}</div>
    </details>
  );
}

function summarizeGrantSample(
  grants: Array<{ securableType: string }>,
): string | null {
  if (grants.length === 0) return null;
  const counts: Record<string, number> = {};
  for (const grant of grants) {
    const key = grant.securableType;
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return Object.entries(counts)
    .map(([type, count]) => `${count} ${type}`)
    .join(", ");
}

function GrantTable(props: { caption: string; headers: string[]; rows: string[][] }) {
  const columns: TableColumn<string[]>[] = props.headers.map((header, index) => ({
    key: `${index}-${header}`,
    label: header,
    value: (row: string[]) => row[index] ?? "",
  }));
  const view = useTableView(props.rows, columns, { paginate: false });
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} aria-label={props.caption}>
        <caption className={styles.label}>{props.caption}</caption>
        <DataTableHead view={view} />
        <tbody>
          {view.rows.map((row, index) => (
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

function resolveDisplayedRun(
  latest: DataAccessRun | null | undefined,
  selectedId: string | null,
  selected: DataAccessRun | undefined,
): DataAccessRun | null | undefined {
  if (selectedId) {
    if (selected?.runId === selectedId) return selected;
    if (latest?.runId === selectedId) return latest;
    return selected;
  }
  return latest;
}

function formatElapsed(
  run: Pick<DataAccessRun, "startedAt" | "finishedAt" | "durationSeconds">,
): string {
  const seconds =
    run.durationSeconds ??
    (run.finishedAt
      ? Math.max(0, (Date.parse(run.finishedAt) - Date.parse(run.startedAt)) / 1000)
      : null);
  if (seconds == null || Number.isNaN(seconds)) return "running";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
}

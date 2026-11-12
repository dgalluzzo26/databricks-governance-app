/**
 * @file Recovery page: export to Git, import a snapshot, and recover into app storage.
 *
 * Recover is meant for seeding a new app (different workspace). It upserts/merges by key and
 * keeps local extras. The joinable salt and UC objects are not restored automatically.
 */
import { useEffect, useState } from "react";
import {
  useImportRecoveryFromGit,
  usePreviewRecoveryExport,
  usePushRecoveryExport,
  useRecoverFromGit,
  useRecoverySettings,
  useSaveRecoverySettings,
} from "@/api/queries";
import type {
  RecoveryApplyResult,
  RecoveryGitSettings,
  RecoveryImportPreview,
  RecoveryPreview,
  RecoveryPushResult,
} from "@/api/types";
import { Alert, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./RecoveryPage.module.css";

/**
 * Admin page for recovery settings, export, import, and recover.
 *
 * @returns The recovery page.
 */
export function RecoveryPage() {
  const settings = useRecoverySettings();
  const save = useSaveRecoverySettings();
  const preview = usePreviewRecoveryExport();
  const push = usePushRecoveryExport();
  const importGit = useImportRecoveryFromGit();
  const recover = useRecoverFromGit();
  const repoConfigured = Boolean(settings.data?.repoUrl);

  return (
    <>
      <PageHeader
        title="Recovery"
        description="Export app-owned governance state as YAML, or import a snapshot to seed a new app."
      />
      {settings.isPending && <Spinner label="Loading recovery settings" />}
      {settings.error && <ErrorAlert error={settings.error} title="Could not load recovery settings" />}
      {settings.data && (
        <GitTargetCard
          initial={settings.data}
          saving={save.isPending}
          error={save.error}
          onSave={(body) => void save.mutateAsync(body)}
        />
      )}
      <Card
        title="Export preview"
        actions={
          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={preview.isPending}
              onClick={() => void preview.mutateAsync()}
            >
              {preview.isPending ? "Building…" : "Preview export"}
            </button>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={!preview.data || !repoConfigured || push.isPending}
              onClick={() => void push.mutateAsync()}
            >
              {push.isPending ? "Pushing…" : "Push to Git"}
            </button>
          </div>
        }
      >
        <p className={styles.muted}>
          Preview builds the files without writing to Git. Push commits the same files to the saved
          repository and branch, using the saved settings and the token in the secret scope. Secret
          values are never included.
        </p>
        {!repoConfigured && <p className={styles.muted}>Save a repository URL above before pushing.</p>}
        {preview.error && <ErrorAlert error={preview.error} title="Preview failed" />}
        {push.error && <ErrorAlert error={push.error} title="Push to Git failed" />}
        {push.data && <PushReport result={push.data} />}
        {preview.data && <FileReport meta={previewMeta(preview.data)} warnings={preview.data.warnings} files={preview.data.files} warningTitle="Before pushing" />}
      </Card>
      <Card
        title="Import / Recover"
        actions={
          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={!repoConfigured || importGit.isPending}
              onClick={() => void importGit.mutateAsync()}
            >
              {importGit.isPending ? "Importing…" : "Import from Git"}
            </button>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={!importGit.data || recover.isPending}
              onClick={() => {
                const occupied = importGit.data?.occupancy.appHasState;
                const message = occupied
                  ? `This app already has governance state (${importGit.data?.occupancy.summary}). Recover will upsert/merge by key and keep local rows that are not in the snapshot. Continue?`
                  : "Recover will write the imported snapshot into this app's storage. Use this to seed a new/empty app. Continue?";
                if (window.confirm(message)) {
                  void recover.mutateAsync();
                }
              }}
            >
              {recover.isPending ? "Recovering…" : "Recover"}
            </button>
          </div>
        }
      >
        <p className={styles.muted}>
          Import reads the snapshot from Git without writing. Recover upserts it into app storage
          (definitions, assignments, approvers, mask rules, exceptions, data-access settings). It
          does not restore the joinable salt or sync Unity Catalog — re-run those afterward.
        </p>
        {!repoConfigured && (
          <p className={styles.muted}>Save a repository URL above before importing.</p>
        )}
        {importGit.error && <ErrorAlert error={importGit.error} title="Import from Git failed" />}
        {recover.error && <ErrorAlert error={recover.error} title="Recover failed" />}
        {recover.data && <RecoverReport result={recover.data} />}
        {importGit.data && <ImportReport preview={importGit.data} />}
      </Card>
    </>
  );
}

/**
 * Form for the Git repository, branch, path, user, and token secret key name.
 *
 * @param props - Initial settings, save state, and submit handler.
 * @returns The settings card.
 */
function GitTargetCard(props: {
  initial: RecoveryGitSettings;
  saving: boolean;
  error: Error | null;
  onSave: (body: RecoveryGitSettings) => void;
}) {
  const [form, setForm] = useState(props.initial);
  useEffect(() => {
    setForm(props.initial);
  }, [props.initial]);

  return (
    <Card title="Git target">
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault();
          props.onSave(form);
        }}
      >
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Repository URL (https)</span>
            <input
              className={styles.input}
              value={form.repoUrl}
              placeholder="https://github.com/org/repo.git"
              onChange={(e) => setForm({ ...form, repoUrl: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Branch</span>
            <input
              className={styles.input}
              value={form.branch}
              onChange={(e) => setForm({ ...form, branch: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Path in repository</span>
            <input
              className={styles.input}
              value={form.pathPrefix}
              onChange={(e) => setForm({ ...form, pathPrefix: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Git user name</span>
            <input
              className={styles.input}
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Token secret key</span>
            <input
              className={styles.input}
              value={form.tokenSecretKey}
              onChange={(e) => setForm({ ...form, tokenSecretKey: e.target.value })}
            />
          </label>
        </div>
        <p className={styles.muted}>
          The token itself lives in the admin secret scope under this key name. It is not stored in
          the app.
        </p>
        {props.error && <ErrorAlert error={props.error} title="Could not save settings" />}
        <div className={styles.actions}>
          <button
            type="submit"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={props.saving}
          >
            {props.saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>
    </Card>
  );
}

/**
 * Summarizes a completed push: where it went and whether a commit was created.
 *
 * @param props - The push result returned by the API.
 * @returns A status banner.
 */
function PushReport({ result }: { result: RecoveryPushResult }) {
  const commit = result.commitSha.slice(0, 12);
  return (
    <Alert tone="success" title={result.committed ? "Pushed to Git" : "Git already up to date"}>
      {result.committed
        ? `Committed ${commit} to ${result.target} (${result.files} files).`
        : `${result.target} already matches this export at ${commit}; nothing was pushed.`}
      {result.branchCreated ? " The branch was created." : ""}
    </Alert>
  );
}

/**
 * Summarizes a completed recover with per-section write counts.
 *
 * @param props - The recover result returned by the API.
 * @returns A status banner.
 */
function RecoverReport({ result }: { result: RecoveryApplyResult }) {
  const commit = result.commitSha.slice(0, 12);
  const { counts } = result;
  return (
    <div className={styles.stack}>
      <Alert tone="success" title="Recovered from Git">
        Applied {commit} from {result.source}: {counts.definitions} definitions, {counts.assignments}{" "}
        assignments, {counts.approvers} approvers, {counts.maskRules} mask rules,{" "}
        {counts.policyExceptions} exceptions
        {counts.dataAccess ? ", data-access settings" : ""}.
        {result.appHadState ? " Existing local rows were upserted/merged." : ""}
      </Alert>
      {result.warnings.length > 0 && (
        <Alert tone="warning" title="After recovering">
          <ul className={styles.warnings}>
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
    </div>
  );
}

/**
 * Renders an imported snapshot: occupancy warning, files, and notes.
 *
 * @param props - The import preview returned by the API.
 * @returns The import report.
 */
function ImportReport({ preview }: { preview: RecoveryImportPreview }) {
  return (
    <div className={styles.stack}>
      {preview.occupancy.appHasState && (
        <Alert tone="warning" title="This app already has governance state">
          {preview.occupancy.summary}. Recover will upsert/merge by key and keep local rows that are
          not in the snapshot. Prefer an empty app when seeding a new workspace.
        </Alert>
      )}
      <FileReport
        meta={`Imported ${new Date(preview.importedAt).toLocaleString()} by ${preview.importedBy} · ${preview.source} @ ${preview.commitSha.slice(0, 12)}`}
        warnings={preview.warnings}
        files={preview.files}
        warningTitle="Before recovering"
      />
    </div>
  );
}

function previewMeta(preview: RecoveryPreview): string {
  return `Generated ${new Date(preview.generatedAt).toLocaleString()} by ${preview.generatedBy}${
    preview.target ? ` · target ${preview.target}` : ""
  }`;
}

/**
 * Shared file list used by export preview and import preview.
 *
 * @param props - Meta line, warnings, and YAML files.
 * @returns The report block.
 */
function FileReport(props: {
  meta: string;
  warnings: string[];
  files: RecoveryPreview["files"];
  warningTitle: string;
}) {
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>{props.meta}</p>
      {props.warnings.length > 0 && (
        <Alert tone="warning" title={props.warningTitle}>
          <ul className={styles.warnings}>
            {props.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {props.files.map((file) => (
        <details key={file.path} className={styles.file}>
          <summary className={styles.fileSummary}>
            <span>{file.path}</span>
            <span className={styles.muted}>{file.records} records</span>
          </summary>
          <pre className={styles.yaml} aria-label={`YAML for ${file.path}`}>
            {file.content}
          </pre>
        </details>
      ))}
    </div>
  );
}

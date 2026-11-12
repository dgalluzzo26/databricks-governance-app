/**
 * @file Recovery export page: Git target settings, a dry-run preview of the YAML files, and the
 * push that commits those files to the saved repository and branch.
 */
import { useEffect, useState } from "react";
import {
  usePreviewRecoveryExport,
  usePushRecoveryExport,
  useRecoverySettings,
  useSaveRecoverySettings,
} from "@/api/queries";
import type { RecoveryGitSettings, RecoveryPreview, RecoveryPushResult } from "@/api/types";
import { Alert, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./RecoveryPage.module.css";

/**
 * Admin page for recovery settings and the export preview.
 *
 * @returns The recovery page.
 */
export function RecoveryPage() {
  const settings = useRecoverySettings();
  const save = useSaveRecoverySettings();
  const preview = usePreviewRecoveryExport();
  const push = usePushRecoveryExport();
  const repoConfigured = Boolean(settings.data?.repoUrl);

  return (
    <>
      <PageHeader
        title="Recovery"
        description="Export app-owned governance state as YAML so it can be restored after a failure."
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
          Preview builds the files without writing to Git. Push commits the same files to the saved repository
          and branch, using the saved settings and the token in the secret scope. Secret values are never
          included.
        </p>
        {!repoConfigured && <p className={styles.muted}>Save a repository URL above before pushing.</p>}
        {preview.error && <ErrorAlert error={preview.error} title="Preview failed" />}
        {push.error && <ErrorAlert error={push.error} title="Push to Git failed" />}
        {push.data && <PushReport result={push.data} />}
        {preview.data && <PreviewReport preview={preview.data} />}
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
          The token itself lives in the admin secret scope under this key name. It is not stored in the app.
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
 * Renders the preview: target, warnings, and each generated YAML file.
 *
 * @param props - The preview returned by the API.
 * @returns The preview report.
 */
function PreviewReport({ preview }: { preview: RecoveryPreview }) {
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        Generated {new Date(preview.generatedAt).toLocaleString()} by {preview.generatedBy}
        {preview.target ? ` · target ${preview.target}` : ""}
      </p>
      {preview.warnings.length > 0 && (
        <Alert tone="warning" title="Before pushing">
          <ul className={styles.warnings}>
            {preview.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {preview.files.map((file) => (
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

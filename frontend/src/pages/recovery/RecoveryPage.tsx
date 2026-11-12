/**
 * @file Recovery export page: Git target settings and a dry-run preview of the YAML files.
 *
 * Phase 1 writes nothing to Git. The preview shows exactly what a push would commit, so an
 * admin can review values and redactions before the push is enabled.
 */
import { useEffect, useState } from "react";
import {
  usePreviewRecoveryExport,
  useRecoverySettings,
  useSaveRecoverySettings,
} from "@/api/queries";
import type { RecoveryGitSettings, RecoveryPreview } from "@/api/types";
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
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={preview.isPending}
            onClick={() => void preview.mutateAsync()}
          >
            {preview.isPending ? "Building…" : "Preview export"}
          </button>
        }
      >
        <p className={styles.muted}>
          Builds the files without writing to Git. Secret values are never included.
        </p>
        {preview.error && <ErrorAlert error={preview.error} title="Preview failed" />}
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
          The token itself lives in the admin secret scope under this key name. It is not stored in
          the app.
        </p>
        {props.error && <ErrorAlert error={props.error} title="Could not save settings" />}
        <div className={styles.actions}>
          <button type="submit" className={`${styles.button} ${styles.buttonPrimary}`} disabled={props.saving}>
            {props.saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>
    </Card>
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

/**
 * @file App settings page: app-wide runtime switches that are not tied to data-access sync.
 */
import { useEffect, useState } from "react";
import { useAppSettings, useSaveAppSettings } from "@/api/queries";
import type { AppSettings } from "@/api/types";
import { Alert, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./AppSettingsPage.module.css";

/**
 * Admin page for app-wide runtime settings.
 *
 * @returns The app settings page.
 */
export function AppSettingsPage() {
  const settings = useAppSettings();
  const save = useSaveAppSettings();

  return (
    <>
      <PageHeader
        title="App settings"
        description="App-wide runtime switches. These apply to every page, not only to data-access sync."
      />
      {settings.isPending && <Spinner label="Loading app settings" />}
      {settings.error && <ErrorAlert error={settings.error} title="Could not load app settings" />}
      {settings.data && (
        <AppSettingsCard
          initial={settings.data}
          saving={save.isPending}
          error={save.error}
          onSave={(body) => void save.mutateAsync(body)}
        />
      )}
    </>
  );
}

function AppSettingsCard(props: {
  initial: AppSettings;
  saving: boolean;
  error: Error | null;
  onSave: (body: AppSettings) => void;
}) {
  const [form, setForm] = useState(props.initial);
  useEffect(() => {
    setForm(props.initial);
  }, [props.initial]);

  return (
    <Card title="Metadata cache">
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault();
          props.onSave(form);
        }}
      >
        <label className={styles.field}>
          <span className={styles.label}>Metadata cache</span>
          <select
            className={styles.select}
            value={form.disableMetadataCache ? "off" : "on"}
            onChange={(e) => setForm({ ...form, disableMetadataCache: e.target.value === "off" })}
            aria-label="Metadata cache"
          >
            <option value="on">Enabled (normal)</option>
            <option value="off">Disabled (live / demo)</option>
          </select>
        </label>
        <p className={styles.muted}>
          Access Explorer, Attribute Explorer, and policy lists reuse Unity Catalog metadata for a
          short time (about 1 to 5 minutes) to stay fast. Disable for a demo so tag and policy changes
          made outside the app show up on the next request. Other app instances pick up the change
          within a few seconds.
        </p>
        {form.disableMetadataCache !== props.initial.disableMetadataCache && (
          <Alert tone="warning" title="Unsaved change">
            Save to apply.
          </Alert>
        )}
        <div className={styles.buttonRow}>
          <button type="submit" className={styles.button} disabled={props.saving}>
            {props.saving ? "Saving…" : "Save"}
          </button>
        </div>
        {props.error && <ErrorAlert error={props.error} title="Could not save app settings" />}
      </form>
    </Card>
  );
}

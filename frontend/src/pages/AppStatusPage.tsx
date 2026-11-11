/**
 * @file App status page showing deployment configuration and storage-backend health.
 *
 * Surfaces storage health, Account Admin service-principal configuration, and deployment
 * metadata (app name, version, workspace, auth mode, warehouse, schema, admin group) from
 * `useAppInfo`.
 */
import { useAppInfo, useDataAccessReadiness } from "@/api/queries";
import { Alert, Badge, Card, DescriptionList, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./AppStatusPage.module.css";

/**
 * Displays live app deployment and storage health from `useAppInfo`.
 *
 * Warns when storage is unhealthy or the Account Admin SP credentials are missing from the
 * configured secret scope.
 */
export function AppStatusPage() {
  const { data: info, isPending, error } = useAppInfo();
  const readiness = useDataAccessReadiness();

  return (
    <>
      <PageHeader title="App status" description="Deployment configuration and health of the app's storage backend." />
      {isPending && <Spinner />}
      {error && <ErrorAlert error={error} title="Could not load app status" />}
      {info && (
        <div className={styles.stack}>
          {!info.storage.healthy && (
            <Alert tone="danger" title={`Storage backend "${info.storage.backend}" is unavailable`}>
              {info.storage.detail}
            </Alert>
          )}
          {!info.adminSpConfigured && (
            <Alert tone="warning" title="Account Admin service principal is not configured">
              Metastore-level ABAC needs <code>client_id</code> / <code>client_secret</code> in secret scope{" "}
              <code>{info.adminSecretScope}</code>.
            </Alert>
          )}
          <Card title="Storage">
            <DescriptionList
              items={[
                { label: "Backend", value: <code>{info.storage.backend}</code> },
                {
                  label: "Health",
                  value: (
                    <Badge tone={info.storage.healthy ? "success" : "danger"}>
                      {info.storage.healthy ? "Healthy" : "Unavailable"}
                    </Badge>
                  ),
                },
                { label: "Location", value: info.storage.detail },
              ]}
            />
          </Card>
          <Card title="Unity Catalog access">
            <DescriptionList
              items={[
                { label: "Secret scope", value: <code>{info.adminSecretScope}</code> },
                {
                  label: "Account Admin SP",
                  value: (
                    <Badge tone={info.adminSpConfigured ? "success" : "warning"}>
                      {info.adminSpConfigured ? "Configured" : "Missing"}
                    </Badge>
                  ),
                },
              ]}
            />
          </Card>
          <Card title="Governance setup">
            {readiness.isPending && <Spinner label="Checking governance setup" />}
            {readiness.error && <ErrorAlert error={readiness.error} title="Could not load governance setup" />}
            {readiness.data && (
              <DescriptionList
                items={[
                  {
                    label: "Required definitions",
                    value: (
                      <Badge tone={readiness.data.setupReady ? "success" : "warning"}>
                        {readiness.data.setupReady ? "Ready" : "Incomplete"}
                      </Badge>
                    ),
                  },
                  ...(readiness.data.setupChecks ?? []).map((check) => ({
                    label: check.label,
                    value: (
                      <span>
                        <Badge tone={check.ok ? "success" : "danger"}>{check.ok ? "ok" : "missing"}</Badge>
                        {check.detail ? ` · ${check.detail}` : ""}
                      </span>
                    ),
                  })),
                ]}
              />
            )}
          </Card>
          <Card title="Data access sync">
            {readiness.isPending && <Spinner label="Checking data-access readiness" />}
            {readiness.error && <ErrorAlert error={readiness.error} title="Could not load data-access readiness" />}
            {readiness.data && (
              <DescriptionList
                items={[
                  {
                    label: "Dry-run",
                    value: (
                      <Badge tone={readiness.data.dryRunReady ? "success" : "warning"}>
                        {readiness.data.dryRunReady ? "Ready" : "Not ready"}
                      </Badge>
                    ),
                  },
                  {
                    label: "Apply",
                    value: (
                      <Badge tone={readiness.data.applyReady ? "success" : "neutral"}>
                        {readiness.data.applyReady ? "Probes pass (still locked)" : "Locked"}
                      </Badge>
                    ),
                  },
                  ...readiness.data.checks.map((check) => ({
                    label: check.label,
                    value: (
                      <span>
                        <Badge tone={check.ok ? "success" : "danger"}>{check.ok ? "ok" : "fail"}</Badge>
                        {check.detail ? ` · ${check.detail}` : ""}
                      </span>
                    ),
                  })),
                ]}
              />
            )}
          </Card>
          <Card title="Deployment">
            <DescriptionList
              items={[
                { label: "App name", value: <code>{info.appName}</code> },
                { label: "Version", value: info.version },
                {
                  label: "Workspace",
                  value: info.workspaceHost ? (
                    <a href={info.workspaceHost} target="_blank" rel="noreferrer">
                      {info.workspaceHost}
                    </a>
                  ) : null,
                },
                { label: "Auth mode", value: <code>{info.authMode}</code> },
                { label: "SQL warehouse", value: info.warehouseId ? <code>{info.warehouseId}</code> : "Not configured" },
                { label: "App schema", value: <code>{`${info.appCatalog}.${info.appSchema}`}</code> },
                { label: "Admin group", value: <code>{info.adminGroup}</code> },
              ]}
            />
          </Card>
        </div>
      )}
    </>
  );
}

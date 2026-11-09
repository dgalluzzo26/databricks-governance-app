import { useAppInfo } from "@/api/queries";
import { Alert, Badge, Card, DescriptionList, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./AppStatusPage.module.css";

export function AppStatusPage() {
  const { data: info, isPending, error } = useAppInfo();

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

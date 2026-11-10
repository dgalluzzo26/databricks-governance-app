import { Link } from "react-router-dom";
import { useAppInfo, useCurrentUser, useMyAttributes } from "@/api/queries";
import { Alert, Avatar, Badge, Card, DescriptionList, PageHeader, Spinner } from "@/components/ui";
import styles from "./HomePage.module.css";

export function HomePage() {
  const { data: user } = useCurrentUser();
  const appInfo = useAppInfo();
  const myAttributes = useMyAttributes();
  if (!user) return null;

  const firstName = user.displayName.split(" ")[0] ?? user.displayName;

  return (
    <>
      <PageHeader
        title={`Welcome, ${firstName}`}
        description="Federated data access governance for Unity Catalog: policies, identity attributes, and access requests in one place."
      />
      <div className={styles.grid}>
        <Card title="Your identity">
          <div className={styles.identity}>
            <Avatar name={user.displayName} size={48} />
            <div>
              <div className={styles.name}>{user.displayName}</div>
              <div className={styles.muted}>{user.email}</div>
            </div>
            <Badge tone={user.isAdmin ? "info" : "neutral"}>{user.isAdmin ? "App admin" : "User"}</Badge>
          </div>
          <DescriptionList
            items={[
              { label: "User name", value: <code>{user.userName}</code> },
              {
                label: "Workspace groups",
                value: user.groups.length ? (
                  <span className={styles.groups}>
                    {user.groups.map((g) => (
                      <Badge key={g}>{g}</Badge>
                    ))}
                  </span>
                ) : (
                  <span className={styles.muted}>No group memberships found</span>
                ),
              },
              {
                label: "Attributes",
                value: myAttributes.isPending ? (
                  <Spinner />
                ) : myAttributes.data?.assignments.length ? (
                  <span className={styles.groups}>
                    {myAttributes.data.assignments.map((item) => (
                      <Badge key={item.assignmentId} tone="info">
                        {item.attributeKey}={item.attributeValue}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  <span className={styles.muted}>
                    None yet · <Link to="/identity?tab=request">request access</Link>
                  </span>
                ),
              },
            ]}
          />
        </Card>

        <Card title="App status">
          {appInfo.isPending && <Spinner />}
          {appInfo.error && <Alert tone="warning" title="Status unavailable">{appInfo.error.message}</Alert>}
          {appInfo.data && (
            <DescriptionList
              items={[
                {
                  label: "Storage",
                  value: (
                    <Badge tone={appInfo.data.storage.healthy ? "success" : "danger"}>
                      {appInfo.data.storage.backend} · {appInfo.data.storage.healthy ? "healthy" : "unavailable"}
                    </Badge>
                  ),
                },
                { label: "Version", value: appInfo.data.version },
              ]}
            />
          )}
        </Card>
      </div>
    </>
  );
}

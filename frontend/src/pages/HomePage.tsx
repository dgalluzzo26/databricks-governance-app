/**
 * @file Signed-in home / welcome page with identity summary and role-specific next steps.
 *
 * Shows the current user's profile, groups, and attribute assignments. Admins also see a compact
 * app-storage health card; non-admins get Get started links (Access Explorer, request access,
 * and approvals when they are category approvers).
 */
import { Link } from "react-router-dom";
import { useAppInfo, useCurrentUser, useMyAttributes } from "@/api/queries";
import { Alert, Avatar, Badge, Card, DescriptionList, PageHeader, Spinner } from "@/components/ui";
import styles from "./HomePage.module.css";

/**
 * Landing page after authentication: identity card plus admin status or non-admin get-started links.
 *
 * Uses `useCurrentUser`, `useMyAttributes`, and (admins only) `useAppInfo`. Renders nothing until
 * the current user is available.
 */
export function HomePage() {
  const { data: user } = useCurrentUser();
  const appInfo = useAppInfo(Boolean(user?.isAdmin));
  const myAttributes = useMyAttributes();
  if (!user) return null;

  const firstName = user.displayName.split(" ")[0] ?? user.displayName;
  const roleLabel = user.isAdmin ? "Admin" : user.isApprover ? "Approver" : "User";

  return (
    <>
      <PageHeader
        title={`Welcome, ${firstName}`}
        description={
          user.isAdmin
            ? "Federated data access governance for Unity Catalog: policies, identity attributes, and access requests in one place."
            : "Explore governed tables, request the attributes you need, and approve requests when you are a category approver."
        }
      />
      <div className={styles.grid}>
        <Card title="Your identity">
          <div className={styles.identity}>
            <Avatar name={user.displayName} size={48} />
            <div>
              <div className={styles.name}>{user.displayName}</div>
              <div className={styles.muted}>{user.email}</div>
            </div>
            <Badge tone={user.isAdmin ? "info" : "neutral"}>{roleLabel}</Badge>
          </div>
          <DescriptionList
            items={[
              { label: "User name", value: <code>{user.userName}</code> },
              { label: "Role", value: <Badge tone={user.isAdmin ? "info" : "neutral"}>{roleLabel}</Badge> },
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

        {user.isAdmin ? (
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
        ) : (
          <Card title="Get started">
            <DescriptionList
              items={[
                {
                  label: "Explore access",
                  value: <Link to="/access-explorer">Open Access explorer</Link>,
                },
                {
                  label: "Request attributes",
                  value: <Link to="/identity?tab=request">Request access</Link>,
                },
                ...(user.isApprover
                  ? [
                      {
                        label: "Approvals",
                        value: <Link to="/identity?tab=approvals">Open approvals inbox</Link>,
                      },
                    ]
                  : []),
              ]}
            />
          </Card>
        )}
      </div>
    </>
  );
}

/**
 * @file Approvals inbox: pending access requests and upcoming renewals the caller may decide.
 *
 * Available to app admins and configured category / data-access / row-filter approvers.
 */
import { useState } from "react";
import {
  useApproveAccessRequest,
  useApproveRenewal,
  useApprovalsInbox,
  useDenyAccessRequest,
  useDismissRenewal,
} from "@/api/queries";
import type { ApprovalInboxItem } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

/**
 * Converts an ISO timestamp into the `YYYY-MM-DDTHH:mm` local-time format expected by
 * `<input type="datetime-local">`.
 */
function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Standalone Approvals page with the same inbox previously hosted under Identity.
 */
export function ApprovalsPage() {
  const { data, isPending, error } = useApprovalsInbox();
  const approveRequest = useApproveAccessRequest();
  const denyRequest = useDenyAccessRequest();
  const approveRenewal = useApproveRenewal();
  const dismissRenewal = useDismissRenewal();
  const [draftExpiry, setDraftExpiry] = useState<Record<string, string>>({});
  const [draftNote, setDraftNote] = useState<Record<string, string>>({});

  const keyFor = (item: ApprovalInboxItem) =>
    item.kind === "REQUEST" ? `req:${item.request?.requestId}` : `ren:${item.assignment?.assignmentId}`;

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Approvals"
        description="Pending sensitivity, data-access, and row-filter requests plus sensitivity assignments expiring within 30 days."
      />
      <Card title="Inbox" actions={data ? <Badge>{data.total}</Badge> : null}>
        <p className={styles.muted}>
          Category and dimension approvers decide matching requests; when none are configured for a
          path, app admins review.
        </p>
        {isPending && <Spinner label="Loading approvals" />}
        {error && <ErrorAlert error={error} title="Could not load approvals" />}
        {data && data.items.length === 0 && (
          <div className={styles.empty}>Nothing waiting for your approval.</div>
        )}
        {data && data.items.length > 0 && (
          <div className={styles.stack}>
            {data.items.map((item) => {
              const key = keyFor(item);
              const expiry = draftExpiry[key] ?? toLocalInput(item.expiresAt);
              const note = draftNote[key] ?? "";
              return (
                <div key={key} className={styles.approvalCard}>
                  <div className={styles.toolbar}>
                    <Badge tone={item.kind === "RENEWAL" ? "warning" : "info"}>{item.kind}</Badge>
                    <code>
                      {item.request?.attributeKey ?? item.assignment?.attributeKey ?? "sensitivity"}=
                      {item.attributeValue}
                    </code>
                    <span className={styles.muted}>{item.principalName}</span>
                    {item.resolvedCategory ? (
                      <Badge>via {item.resolvedCategory}</Badge>
                    ) : (
                      <Badge tone="warning">app admins</Badge>
                    )}
                  </div>
                  {item.kind === "REQUEST" && item.request && (
                    <div className={styles.muted}>Justification: {item.request.justification}</div>
                  )}
                  <div className={styles.formGrid}>
                    <label className={styles.field}>
                      <span className={styles.label}>Approve until</span>
                      <input
                        className={styles.input}
                        type="datetime-local"
                        value={expiry}
                        onChange={(event) =>
                          setDraftExpiry((current) => ({ ...current, [key]: event.target.value }))
                        }
                      />
                    </label>
                    <label className={styles.fieldGrow}>
                      <span className={styles.label}>Note</span>
                      <input
                        className={styles.input}
                        value={note}
                        onChange={(event) =>
                          setDraftNote((current) => ({ ...current, [key]: event.target.value }))
                        }
                        placeholder="Optional"
                      />
                    </label>
                  </div>
                  <div className={styles.buttonRow}>
                    {item.kind === "REQUEST" && item.request && (
                      <>
                        <button
                          type="button"
                          className={`${styles.button} ${styles.buttonPrimary}`}
                          disabled={!expiry || approveRequest.isPending}
                          onClick={() =>
                            void approveRequest.mutateAsync({
                              requestId: item.request!.requestId,
                              body: {
                                expiresAt: new Date(expiry).toISOString(),
                                note: note.trim() || null,
                              },
                            })
                          }
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          className={styles.button}
                          disabled={denyRequest.isPending}
                          onClick={() =>
                            void denyRequest.mutateAsync({
                              requestId: item.request!.requestId,
                              body: { note: note.trim() || null },
                            })
                          }
                        >
                          Deny
                        </button>
                      </>
                    )}
                    {item.kind === "RENEWAL" && item.assignment && (
                      <>
                        <button
                          type="button"
                          className={`${styles.button} ${styles.buttonPrimary}`}
                          disabled={!expiry || approveRenewal.isPending}
                          onClick={() =>
                            void approveRenewal.mutateAsync({
                              assignmentId: item.assignment!.assignmentId,
                              body: {
                                expiresAt: new Date(expiry).toISOString(),
                                note: note.trim() || null,
                              },
                            })
                          }
                        >
                          Renew
                        </button>
                        <button
                          type="button"
                          className={styles.button}
                          disabled={dismissRenewal.isPending}
                          onClick={() => void dismissRenewal.mutateAsync(item.assignment!.assignmentId)}
                        >
                          Dismiss
                        </button>
                      </>
                    )}
                  </div>
                  {(approveRequest.error || denyRequest.error || approveRenewal.error) && (
                    <Alert tone="danger" title="Decision failed">
                      {(approveRequest.error ?? denyRequest.error ?? approveRenewal.error)?.message}
                    </Alert>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

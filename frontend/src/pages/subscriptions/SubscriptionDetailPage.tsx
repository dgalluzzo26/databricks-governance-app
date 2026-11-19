/**
 * @file One subscription: what it grants, to whom, until when, and how the last sync went.
 *
 * Admins approve or deny pending subscriptions; admins and the creator can extend, revoke, or sync.
 */
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCurrentUser, useSubscription, useSubscriptionAction, useSubscriptionOptions } from "@/api/queries";
import type { SecurableKind, Subscription } from "@/api/types";
import { Alert, Badge, Card, DescriptionList, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import shared from "../identity/IdentityPage.module.css";
import styles from "./Subscriptions.module.css";
import { ObjectTable } from "./SubscriptionWizardPage";
import {
  PHASE_LABELS,
  describePrivileges,
  describeResource,
  formatWhen,
  fromLocalInput,
  relativeTime,
  toLocalInput,
} from "./subscriptionText";

/**
 * Subscription detail page (`/subscriptions/:subscriptionId`).
 *
 * @returns The detail view with lifecycle actions.
 */
export function SubscriptionDetailPage() {
  const { subscriptionId } = useParams();
  const query = useSubscription(subscriptionId);
  if (query.isPending) return <Spinner label="Loading subscription" />;
  if (query.error) return <ErrorAlert error={query.error} title="Could not load subscription" />;
  return <Detail subscription={query.data} />;
}

function Detail({ subscription: s }: { subscription: Subscription }) {
  const navigate = useNavigate();
  const options = useSubscriptionOptions();
  const user = useCurrentUser();
  const action = useSubscriptionAction();
  const [note, setNote] = useState("");
  const [extendTo, setExtendTo] = useState(() => toLocalInput(s.expiresAt));
  const isAdmin = options.data?.isAdmin ?? false;
  const isCreator = user.data?.userName?.toLowerCase() === s.createdBy.toLowerCase();
  const pending = s.status === "PENDING_APPROVAL";
  const ended = ["EXPIRED", "REVOKED", "DENIED"].includes(s.phase);
  const canManage = isAdmin || isCreator;
  const canEdit = isAdmin ? !ended : isCreator && pending;
  const phase = PHASE_LABELS[s.phase];
  const id = s.subscriptionId;
  const run = (body: Parameters<typeof action.mutate>[0]) =>
    action.mutate(body, { onSuccess: () => (body.kind === "delete" ? navigate("/subscriptions") : undefined) });

  return (
    <>
      <PageHeader
        title={s.displayName}
        description={
          <>
            <Badge tone={phase.tone}>{phase.label}</Badge> {s.description}
          </>
        }
        actions={
          <div className={styles.actions}>
            <Link to="/subscriptions" className={shared.button}>
              All subscriptions
            </Link>
            {canEdit && (
              <Link to={`/subscriptions/${s.subscriptionId}/edit`} className={shared.button}>
                Edit
              </Link>
            )}
            {canManage && s.status === "APPROVED" && (
              <button type="button" className={shared.button} disabled={action.isPending} onClick={() => run({ kind: "sync", id })}>
                Sync now
              </button>
            )}
            {canManage && !ended && (
              <button
                type="button"
                className={`${shared.button} ${shared.buttonDanger}`}
                disabled={action.isPending}
                onClick={() => {
                  if (window.confirm(`Revoke ${s.displayName}? Members lose access on the next sync.`)) run({ kind: "revoke", id });
                }}
              >
                Revoke
              </button>
            )}
            {isAdmin && ended && (
              <button
                type="button"
                className={`${shared.button} ${shared.buttonDanger}`}
                disabled={action.isPending}
                onClick={() => {
                  if (window.confirm(`Delete ${s.displayName}? This removes its record.`)) run({ kind: "delete", id });
                }}
              >
                Delete
              </button>
            )}
          </div>
        }
      />
      {action.error && <ErrorAlert error={action.error} title="That did not work" />}

      {pending && (
        <Card title="Waiting for approval">
          {isAdmin ? (
            <div className={shared.stack}>
              <label className={shared.field}>
                <span className={shared.label}>Note (optional)</span>
                <input className={shared.input} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className={shared.buttonRow}>
                <button
                  type="button"
                  className={`${shared.button} ${shared.buttonPrimary}`}
                  disabled={action.isPending}
                  onClick={() => run({ kind: "approve", id, note: note || undefined })}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className={`${shared.button} ${shared.buttonDanger}`}
                  disabled={action.isPending}
                  onClick={() => run({ kind: "deny", id, note: note || undefined })}
                >
                  Deny
                </button>
              </div>
            </div>
          ) : (
            <p className={shared.muted}>An app admin needs to approve this before it takes effect.</p>
          )}
        </Card>
      )}

      <Card title="Summary">
        <DescriptionList
          items={[
            { label: "Group", value: <code>{s.groupName}</code> },
            { label: "Starts", value: `${formatWhen(s.startsAt)} (${relativeTime(s.startsAt)})` },
            { label: "Ends", value: `${formatWhen(s.expiresAt)} (${relativeTime(s.expiresAt)})` },
            { label: "Review gate", value: s.reviewExempt ? "Waived" : "Only reviewed objects" },
            { label: "Created by", value: `${s.createdBy} · ${formatWhen(s.createdAt)}` },
            ...(s.decidedBy
              ? [
                  {
                    label: s.status === "DENIED" ? "Denied by" : "Approved by",
                    value: `${s.decidedBy} · ${formatWhen(s.decidedAt)}${s.decisionNote ? ` — ${s.decisionNote}` : ""}`,
                  },
                ]
              : []),
            ...(s.revokedBy ? [{ label: "Revoked by", value: `${s.revokedBy} · ${formatWhen(s.revokedAt)}` }] : []),
          ]}
        />
        {canManage && !ended && (
          <div className={styles.conditionRow}>
            <label className={shared.field}>
              <span className={shared.label}>Change end date</span>
              <input
                type="datetime-local"
                className={shared.input}
                value={extendTo}
                onChange={(e) => setExtendTo(e.target.value)}
              />
            </label>
            <button
              type="button"
              className={shared.button}
              disabled={action.isPending || !extendTo}
              onClick={() => run({ kind: "extend", id, expiresAt: fromLocalInput(extendTo) })}
            >
              Update end date
            </button>
          </div>
        )}
      </Card>

      <Card title={`People (${s.subjects.length})`}>
        <div className={shared.chipRow}>
          {s.subjects.map((p) => (
            <span key={`${p.principalType}:${p.principalName}`} className={shared.chip}>
              <Badge>{p.principalType === "SERVICE_PRINCIPAL" ? "SP" : p.principalType.toLowerCase()}</Badge>
              {p.principalName}
            </span>
          ))}
        </div>
      </Card>

      <Card title="Access">
        <ul>
          {s.resources.map((r, i) => (
            <li key={i}>
              <strong>{describePrivileges(r)}</strong> on {describeResource(r).replace(/^./, (c) => c.toLowerCase())}
            </li>
          ))}
        </ul>
        {s.attributes.length > 0 && (
          <>
            <span className={shared.label}>Attributes held while active</span>
            <div className={shared.chipRow}>
              {s.attributes.map((a) => (
                <span key={`${a.attributeKey}=${a.attributeValue}`} className={shared.chip}>
                  {a.attributeKey} = {a.attributeValue}
                </span>
              ))}
            </div>
          </>
        )}
      </Card>

      <Card title="Last sync">
        {!s.lastSync ? (
          <p className={shared.empty}>
            {s.status === "APPROVED" ? "Not synced yet. It runs with the Data Access sync, or use Sync now." : "Syncs once approved."}
          </p>
        ) : (
          <div className={shared.stack}>
            <div className={styles.stats}>
              <Stat label="Synced" value={relativeTime(s.lastSync.syncedAt)} />
              <Stat label="Objects" value={s.lastSync.matchedObjects} />
              <Stat label="Grants held" value={s.lastSync.grants.length} />
              <Stat label="Members" value={s.lastSync.members} />
              <Stat
                label="Last change"
                value={`+${s.lastSync.grantsAdded} / −${s.lastSync.grantsRevoked} grants`}
              />
            </div>
            {s.lastSync.errors.length > 0 && (
              <Alert tone="danger" title="Sync errors">
                <ul className={styles.errorList}>
                  {s.lastSync.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {s.lastSync.grants.length > 0 && <GrantTable subscription={s} />}
          </div>
        )}
      </Card>
    </>
  );
}

function GrantTable({ subscription }: { subscription: Subscription }) {
  const grouped = new Map<string, { securableKind: SecurableKind; fullName: string; privileges: string[] }>();
  for (const g of subscription.lastSync?.grants ?? []) {
    const key = `${g.securableKind}:${g.fullName}`;
    const row = grouped.get(key) ?? { securableKind: g.securableKind, fullName: g.fullName, privileges: [] };
    row.privileges.push(g.privilege);
    grouped.set(key, row);
  }
  return <ObjectTable objects={[...grouped.values()].slice(0, 200)} />;
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{value}</span>
      <span className={shared.label}>{label}</span>
    </div>
  );
}

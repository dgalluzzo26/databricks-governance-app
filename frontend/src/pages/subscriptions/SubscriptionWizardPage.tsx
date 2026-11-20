/**
 * @file Five-step wizard to create or edit a subscription: basics, people, access, attributes, review.
 *
 * Access can be picked object by object (catalogs, schemas, tables, volumes, functions, models,
 * external locations, workspace folders, dashboards, Genie spaces, apps, SQL warehouses) or matched
 * by governed tags, which re-match on every sync. The review step
 * reads the draft back in plain language and previews what it reaches right now.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  useAttributeDefinitions,
  usePreviewSubscription,
  useSaveSubscription,
  useSubscription,
  useSubscriptionBrowse,
  useSubscriptionOptions,
} from "@/api/queries";
import type {
  PrincipalKind,
  PrivilegePreset,
  SecurableKind,
  Subscription,
  SubscriptionDraft,
  SubscriptionOptions,
  SubscriptionResource,
  TagCondition,
} from "@/api/types";
import { PrincipalPicker } from "@/components/PrincipalPicker";
import { Alert, Badge, Card, ErrorAlert, PageHeader, SearchableSelect, Spinner } from "@/components/ui";
import shared from "../identity/IdentityPage.module.css";
import styles from "./Subscriptions.module.css";
import {
  DATA_KINDS,
  KIND_LABELS,
  LISTED_KINDS,
  PICKABLE_KINDS,
  PRESET_LABELS,
  PRIVILEGE_GROUPS,
  TAG_MATCH_KINDS,
  capitalize,
  describePrivileges,
  describeResource,
  formatWhen,
  fromLocalInput,
  isValidName,
  slugify,
  toLocalInput,
} from "./subscriptionText";

const STEPS = ["Basics", "People", "Access", "Attributes", "Review"] as const;
const DAY_MS = 86_400_000;

/** Draft held by the wizard; dates are `datetime-local` strings. */
interface WizardDraft extends Omit<SubscriptionDraft, "startsAt" | "expiresAt"> {
  startsAt: string;
  expiresAt: string;
}

function emptyDraft(): WizardDraft {
  const now = new Date();
  return {
    name: "",
    displayName: "",
    description: "",
    startsAt: toLocalInput(now),
    expiresAt: toLocalInput(new Date(now.getTime() + 30 * DAY_MS)),
    reviewExempt: false,
    subjects: [],
    resources: [],
    attributes: [],
  };
}

function fromSubscription(s: Subscription): WizardDraft {
  return {
    name: s.name,
    displayName: s.displayName,
    description: s.description ?? "",
    startsAt: toLocalInput(s.startsAt),
    expiresAt: toLocalInput(s.expiresAt),
    reviewExempt: s.reviewExempt,
    subjects: s.subjects,
    resources: s.resources,
    attributes: s.attributes,
  };
}

function toBody(draft: WizardDraft): SubscriptionDraft {
  return {
    ...draft,
    description: draft.description?.trim() || null,
    startsAt: draft.startsAt ? fromLocalInput(draft.startsAt) : null,
    expiresAt: fromLocalInput(draft.expiresAt),
  };
}

function resourceProblem(resource: SubscriptionResource): string | null {
  if (resource.selector === "OBJECT") {
    if (!resource.securableKind || !resource.fullName?.trim()) return "Pick an object";
  } else {
    if (resource.conditions.length === 0) return "Add a tag condition";
    if (resource.conditions.some((c) => !c.tagKey || c.values.length === 0)) return "Every condition needs a tag and a value";
    if (resource.levels.length === 0) return "Choose at least one object type";
  }
  if (resource.preset === "CUSTOM" && resource.privileges.length === 0) return "Choose at least one privilege";
  return null;
}

function stepProblems(draft: WizardDraft, options: SubscriptionOptions | undefined): string[][] {
  const basics: string[] = [];
  if (!draft.displayName.trim()) basics.push("Give the subscription a name");
  if (!isValidName(draft.name)) basics.push("The short name must be lowercase letters and digits joined by single underscores");
  const start = draft.startsAt ? new Date(draft.startsAt).getTime() : Date.now();
  const end = new Date(draft.expiresAt).getTime();
  if (!(end > start)) basics.push("The end must be after the start");
  if (end <= Date.now()) basics.push("The end must be in the future");
  if (options && end - start > options.maxDays * DAY_MS) basics.push(`Subscriptions can last at most ${options.maxDays} days`);
  const people = draft.subjects.length ? [] : ["Add at least one person, group, or service principal"];
  const access = draft.resources.length
    ? draft.resources.map(resourceProblem).filter((p): p is string => Boolean(p))
    : ["Add at least one resource"];
  const attributes = draft.attributes.some((a) => !a.attributeKey || !a.attributeValue) ? ["Finish or remove the empty attribute row"] : [];
  return [basics, people, access, attributes, []];
}

/**
 * Create (`/subscriptions/new`) or edit (`/subscriptions/:subscriptionId/edit`) a subscription.
 *
 * @returns The wizard page.
 */
export function SubscriptionWizardPage() {
  const { subscriptionId } = useParams();
  const existing = useSubscription(subscriptionId);
  const options = useSubscriptionOptions();
  if (subscriptionId && existing.isPending) return <Spinner label="Loading subscription" />;
  if (existing.error) return <ErrorAlert error={existing.error} title="Could not load subscription" />;
  if (options.isPending) return <Spinner label="Loading" />;
  if (options.error) return <ErrorAlert error={options.error} title="Could not load subscription options" />;
  if (!options.data.canCreate) {
    return <Alert tone="warning">Subscriptions are managed by app admins and approvers.</Alert>;
  }
  return <Wizard existing={existing.data} options={options.data} />;
}

function Wizard({ existing, options }: { existing?: Subscription; options: SubscriptionOptions }) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<WizardDraft>(() => (existing ? fromSubscription(existing) : emptyDraft()));
  const [nameTouched, setNameTouched] = useState(Boolean(existing));
  const [step, setStep] = useState(0);
  const save = useSaveSubscription();
  const problems = stepProblems(draft, options);
  const stepIssues = problems[step] ?? [];
  const update = (patch: Partial<WizardDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const canSubmit = problems.every((p) => p.length === 0);
  const submitLabel = existing ? "Save changes" : options.requiresApproval ? "Submit for approval" : "Create subscription";

  const submit = async () => {
    const saved = await save.mutateAsync({ id: existing?.subscriptionId, body: toBody(draft) });
    navigate(`/subscriptions/${saved.subscriptionId}`);
  };

  return (
    <>
      <PageHeader
        title={existing ? `Edit ${existing.displayName}` : "New subscription"}
        description="Name who gets access, to what, and for how long. Access starts and ends on its own."
        actions={
          <Link to={existing ? `/subscriptions/${existing.subscriptionId}` : "/subscriptions"} className={shared.button}>
            Cancel
          </Link>
        }
      />
      <div className={styles.wizard}>
        <ol className={styles.steps} aria-label="Steps">
          {STEPS.map((label, index) => (
            <li key={label}>
              <button
                type="button"
                className={`${styles.step} ${index === step ? styles.stepActive : ""} ${
                  index < step && (problems[index]?.length ?? 0) === 0 ? styles.stepDone : ""
                }`}
                aria-current={index === step ? "step" : undefined}
                onClick={() => setStep(index)}
              >
                <span className={styles.stepNumber}>{index < step && (problems[index]?.length ?? 0) === 0 ? "✓" : index + 1}</span>
                {label}
              </button>
            </li>
          ))}
        </ol>
        <div className={styles.panel}>
          {step === 0 && (
            <BasicsStep
              draft={draft}
              options={options}
              editing={Boolean(existing)}
              onChange={(patch) => {
                if (patch.name !== undefined) setNameTouched(true);
                else if (patch.displayName !== undefined && !nameTouched) patch = { ...patch, name: slugify(patch.displayName) };
                update(patch);
              }}
            />
          )}
          {step === 1 && <PeopleStep draft={draft} onChange={update} />}
          {step === 2 && <AccessStep draft={draft} options={options} onChange={update} />}
          {step === 3 && <AttributesStep draft={draft} onChange={update} />}
          {step === 4 && <ReviewStep draft={draft} options={options} problems={problems} />}
          {stepIssues.length > 0 && step < 4 && (
            <Alert tone="warning" title="Before you continue">
              <ul className={styles.errorList}>
                {stepIssues.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </Alert>
          )}
          {save.error && <ErrorAlert error={save.error} title="Could not save the subscription" />}
          <div className={styles.footer}>
            <button type="button" className={shared.button} disabled={step === 0} onClick={() => setStep(step - 1)}>
              Back
            </button>
            {step < STEPS.length - 1 ? (
              <button type="button" className={`${shared.button} ${shared.buttonPrimary}`} onClick={() => setStep(step + 1)}>
                Next: {STEPS[step + 1]}
              </button>
            ) : (
              <button
                type="button"
                className={`${shared.button} ${shared.buttonPrimary}`}
                disabled={!canSubmit || save.isPending}
                onClick={() => void submit()}
              >
                {save.isPending ? "Saving…" : submitLabel}
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ basics

function BasicsStep({
  draft,
  options,
  editing,
  onChange,
}: {
  draft: WizardDraft;
  options: SubscriptionOptions;
  editing: boolean;
  onChange: (patch: Partial<WizardDraft>) => void;
}) {
  const quick = [7, 30, 90, 365].filter((d) => d <= options.maxDays);
  const setDuration = (days: number) => {
    const start = draft.startsAt ? new Date(draft.startsAt) : new Date();
    onChange({ expiresAt: toLocalInput(new Date(start.getTime() + days * DAY_MS)) });
  };
  return (
    <Card title="Basics">
      <div className={shared.stack}>
        <label className={shared.field}>
          <span className={shared.label}>Name</span>
          <input
            className={shared.input}
            value={draft.displayName}
            placeholder="IA engineers build access"
            onChange={(e) => onChange({ displayName: e.target.value })}
          />
        </label>
        <label className={shared.field}>
          <span className={shared.label}>Short name (unique; lowercase letters, digits and underscores)</span>
          <input
            className={shared.input}
            value={draft.name}
            disabled={editing}
            placeholder="ia_engineers_build"
            onChange={(e) => onChange({ name: e.target.value.toLowerCase() })}
          />
        </label>
        <div>
          <span className={shared.label}>Group this subscription manages</span>
          <div className={styles.groupPreview}>{`${options.groupPrefix}${draft.name || "…"}`}</div>
        </div>
        <label className={shared.field}>
          <span className={shared.label}>Purpose</span>
          <textarea
            className={shared.textarea}
            value={draft.description ?? ""}
            placeholder="Why this access is needed"
            onChange={(e) => onChange({ description: e.target.value })}
          />
        </label>
        <div className={shared.formGrid}>
          <label className={shared.field}>
            <span className={shared.label}>Starts</span>
            <input
              type="datetime-local"
              className={shared.input}
              value={draft.startsAt}
              onChange={(e) => onChange({ startsAt: e.target.value })}
            />
          </label>
          <label className={shared.field}>
            <span className={shared.label}>Ends</span>
            <input
              type="datetime-local"
              className={shared.input}
              value={draft.expiresAt}
              onChange={(e) => onChange({ expiresAt: e.target.value })}
            />
          </label>
        </div>
        <div className={styles.quickRow}>
          <span className={shared.label}>Length:</span>
          {quick.map((days) => (
            <button key={days} type="button" className={shared.button} onClick={() => setDuration(days)}>
              {days === 365 ? "1 year" : `${days} days`}
            </button>
          ))}
          <span className={shared.muted}>Up to {options.maxDays} days.</span>
        </div>
        {options.isAdmin && (
          <label className={shared.checkboxRow}>
            <input
              type="checkbox"
              checked={draft.reviewExempt}
              onChange={(e) => onChange({ reviewExempt: e.target.checked })}
            />
            Waive the review gate (reach objects that are not marked ready yet)
          </label>
        )}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ people

function PeopleStep({ draft, onChange }: { draft: WizardDraft; onChange: (patch: Partial<WizardDraft>) => void }) {
  const [name, setName] = useState("");
  const add = (principalName: string, kind: PrincipalKind) => {
    const trimmed = principalName.trim();
    if (!trimmed) return;
    if (
      !draft.subjects.some(
        (s) => s.principalName.toLowerCase() === trimmed.toLowerCase() && s.principalType === kind,
      )
    ) {
      onChange({ subjects: [...draft.subjects, { principalName: trimmed, principalType: kind }] });
    }
    setName("");
  };
  return (
    <Card title="Who gets access">
      <div className={shared.stack}>
        <div className={shared.fieldGrow}>
          <span className={shared.label}>Person, group, or service principal</span>
          <PrincipalPicker
            value={name}
            placeholder="Search users or groups to add…"
            aria-label="Add principal"
            onChange={(value, picked) => {
              setName(value);
              if (picked) add(value, picked);
            }}
          />
          <p className={shared.muted} style={{ marginTop: "var(--space-1)" }}>
            Pick a search result to add them. The directory tells us whether it is a user, group, or
            service principal.
          </p>
        </div>
        {draft.subjects.length === 0 ? (
          <p className={shared.empty}>Nobody yet. Members are added to the group when the subscription starts.</p>
        ) : (
          <div className={shared.chipRow} aria-label="Selected principals">
            {draft.subjects.map((s) => (
              <span key={`${s.principalType}:${s.principalName}`} className={shared.chip}>
                <Badge>{s.principalType === "SERVICE_PRINCIPAL" ? "SP" : s.principalType.toLowerCase()}</Badge>
                {s.principalName}
                <button
                  type="button"
                  className={shared.chipRemove}
                  aria-label={`Remove ${s.principalName}`}
                  onClick={() => onChange({ subjects: draft.subjects.filter((x) => x !== s) })}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ access

function AccessStep({
  draft,
  options,
  onChange,
}: {
  draft: WizardDraft;
  options: SubscriptionOptions;
  onChange: (patch: Partial<WizardDraft>) => void;
}) {
  const setResource = (index: number, resource: SubscriptionResource) =>
    onChange({ resources: draft.resources.map((r, i) => (i === index ? resource : r)) });
  const addTagMatch = () =>
    onChange({
      resources: [
        ...draft.resources,
        {
          selector: "TAG_MATCH",
          conditions: [{ tagKey: options.accessTagKey, values: [], match: "PREFIX" }],
          levels: ["TABLE"],
          preset: "READ",
          privileges: [],
        },
      ],
    });
  const addObject = () =>
    onChange({
      resources: [
        ...draft.resources,
        { selector: "OBJECT", securableKind: "SCHEMA", fullName: "", conditions: [], levels: [], preset: "READ", privileges: [] },
      ],
    });
  return (
    <>
      {!options.isAdmin && options.allowedPaths.length > 0 && (
        <Alert tone="info">
          You can grant objects under {options.allowedPaths.join(", ")}. Tag matches must include an{" "}
          {options.accessTagKey} condition inside those paths.
        </Alert>
      )}
      {draft.resources.map((resource, index) => (
        <ResourceEditor
          key={index}
          index={index}
          resource={resource}
          options={options}
          onChange={(r) => setResource(index, r)}
          onRemove={() => onChange({ resources: draft.resources.filter((_, i) => i !== index) })}
        />
      ))}
      <Card title={draft.resources.length ? "Add more access" : "What should they reach?"}>
        <div className={styles.choiceGrid}>
          <button type="button" className={styles.choice} onClick={addTagMatch}>
            <span className={styles.choiceTitle}>Match by tags</span>
            <span className={styles.choiceHint}>
              Every catalog, schema, table or volume whose tags match. New matches are picked up on each sync.
            </span>
          </button>
          <button type="button" className={styles.choice} onClick={addObject}>
            <span className={styles.choiceTitle}>Pick an object</span>
            <span className={styles.choiceHint}>
              One catalog, schema, table, volume, function or model
              {options.isAdmin ? "; or an external location, folder, dashboard, Genie space, app or SQL warehouse" : ""}.
            </span>
          </button>
        </div>
      </Card>
    </>
  );
}

function ResourceEditor({
  index,
  resource,
  options,
  onChange,
  onRemove,
}: {
  index: number;
  resource: SubscriptionResource;
  options: SubscriptionOptions;
  onChange: (resource: SubscriptionResource) => void;
  onRemove: () => void;
}) {
  const kinds: SecurableKind[] =
    resource.selector === "OBJECT" ? (resource.securableKind ? [resource.securableKind] : []) : resource.levels;
  return (
    <section className={styles.resourceCard} aria-label={`Resource ${index + 1}`}>
      <div className={styles.resourceHeader}>
        <strong>
          {index + 1}. {resource.selector === "OBJECT" ? "Picked object" : "Tag match"}
        </strong>
        <button type="button" className={`${shared.button} ${shared.buttonDanger}`} onClick={onRemove}>
          Remove
        </button>
      </div>
      {resource.selector === "OBJECT" ? (
        <ObjectPicker resource={resource} isAdmin={options.isAdmin} onChange={onChange} />
      ) : (
        <TagMatchEditor resource={resource} options={options} onChange={onChange} />
      )}
      <PrivilegePicker resource={resource} kinds={kinds} options={options} onChange={onChange} />
      <p className={shared.muted}>
        {describeResource(resource)} · {describePrivileges(resource)}
      </p>
    </section>
  );
}

function ObjectPicker({
  resource,
  isAdmin,
  onChange,
}: {
  resource: SubscriptionResource;
  isAdmin: boolean;
  onChange: (resource: SubscriptionResource) => void;
}) {
  const kind = resource.securableKind ?? "SCHEMA";
  const inCatalog = DATA_KINDS.includes(kind);
  const parts = (resource.fullName ?? "").split(".");
  const [catalog, setCatalog] = useState(inCatalog ? parts[0] ?? "" : "");
  const [schema, setSchema] = useState(parts[1] ?? "");
  const needsSchema = inCatalog && kind !== "CATALOG";
  const needsLeaf = needsSchema && kind !== "SCHEMA";
  const catalogs = useSubscriptionBrowse("CATALOG", null, inCatalog);
  const schemas = useSubscriptionBrowse("SCHEMA", catalog || null, needsSchema && Boolean(catalog));
  const leaves = useSubscriptionBrowse(kind, catalog && schema ? `${catalog}.${schema}` : null, needsLeaf && Boolean(catalog && schema));
  const leafName = needsLeaf ? parts[2] ?? "" : "";
  const kinds = PICKABLE_KINDS.filter((k) => isAdmin || DATA_KINDS.includes(k));
  const set = (fullName: string, nextKind: SecurableKind = kind, label: string | null = null) =>
    onChange({ ...resource, securableKind: nextKind, fullName, label });
  const tail = (name: string) => name.split(".").pop() ?? name;

  return (
    <div className={shared.toolbar}>
      <label className={shared.field}>
        <span className={shared.label}>Type</span>
        <select
          className={shared.select}
          value={kind}
          onChange={(e) => {
            const next = e.target.value as SecurableKind;
            setCatalog("");
            setSchema("");
            set(next === "WORKSPACE_FOLDER" ? "/" : "", next);
          }}
        >
          {kinds.map((k) => (
            <option key={k} value={k}>
              {capitalize(KIND_LABELS[k].one)}
            </option>
          ))}
        </select>
      </label>
      {kind === "WORKSPACE_FOLDER" ? (
        <FolderPicker path={resource.fullName || "/"} onChange={(path) => set(path)} />
      ) : LISTED_KINDS.includes(kind) ? (
        <ListedPicker
          kind={kind}
          value={resource.fullName ?? ""}
          onChange={(name, label) => set(name, kind, label)}
        />
      ) : (
        <>
          <div className={shared.fieldGrow}>
            <span className={shared.label}>Catalog</span>
            <SearchableSelect
              aria-label="Catalog"
              value={catalog}
              options={(catalogs.data?.items ?? []).map((i) => i.name)}
              placeholder={catalogs.isPending ? "Loading…" : "Choose a catalog"}
              onChange={(value) => {
                setCatalog(value);
                setSchema("");
                set(kind === "CATALOG" ? value : "");
              }}
            />
          </div>
          {needsSchema && (
            <div className={shared.fieldGrow}>
              <span className={shared.label}>Schema</span>
              <SearchableSelect
                aria-label="Schema"
                value={schema}
                disabled={!catalog}
                options={(schemas.data?.items ?? []).map((i) => tail(i.name))}
                placeholder={catalog ? (schemas.isPending ? "Loading…" : "Choose a schema") : "Choose a catalog first"}
                onChange={(value) => {
                  setSchema(value);
                  set(kind === "SCHEMA" && value ? `${catalog}.${value}` : "");
                }}
              />
            </div>
          )}
          {needsLeaf && (
            <div className={shared.fieldGrow}>
              <span className={shared.label}>{capitalize(KIND_LABELS[kind].one)}</span>
              <SearchableSelect
                aria-label={KIND_LABELS[kind].one}
                value={leafName}
                disabled={!schema}
                options={(leaves.data?.items ?? []).map((i) => tail(i.name))}
                placeholder={schema ? (leaves.isPending ? "Loading…" : `Choose a ${KIND_LABELS[kind].one}`) : "Choose a schema first"}
                onChange={(value) => set(value ? `${catalog}.${schema}.${value}` : "")}
              />
            </div>
          )}
        </>
      )}
      {(catalogs.error || schemas.error || leaves.error) && (
        <ErrorAlert error={catalogs.error ?? schemas.error ?? leaves.error} title="Could not list objects" />
      )}
    </div>
  );
}

function ListedPicker({
  kind,
  value,
  onChange,
}: {
  kind: SecurableKind;
  value: string;
  onChange: (name: string, label: string | null) => void;
}) {
  const items = useSubscriptionBrowse(kind, null);
  const noun = KIND_LABELS[kind].one;
  const choices = (items.data?.items ?? []).map((i) => ({
    value: i.name,
    label: i.label && i.label !== i.name ? `${i.label} (${i.name})` : i.name,
  }));
  return (
    <div className={shared.fieldGrow}>
      <span className={shared.label}>{capitalize(noun)}</span>
      <SearchableSelect
        aria-label={noun}
        value={value}
        options={choices}
        placeholder={items.isPending ? "Loading…" : `Choose a ${noun}`}
        onChange={(name) => onChange(name, items.data?.items.find((i) => i.name === name)?.label ?? null)}
      />
      {items.error && <ErrorAlert error={items.error} title={`Could not list ${KIND_LABELS[kind].many}`} />}
    </div>
  );
}

function FolderPicker({ path, onChange }: { path: string; onChange: (path: string) => void }) {
  const children = useSubscriptionBrowse("WORKSPACE_FOLDER", path || "/", Boolean(path));
  const parent = path === "/" ? null : path.replace(/\/[^/]+\/?$/, "") || "/";
  return (
    <div className={shared.fieldGrow}>
      <span className={shared.label}>Workspace folder</span>
      <input className={shared.input} value={path} onChange={(e) => onChange(e.target.value)} placeholder="/Workspace/Shared/team" />
      <div className={styles.quickRow}>
        {parent && (
          <button type="button" className={shared.button} onClick={() => onChange(parent)}>
            ↑ Up
          </button>
        )}
        {(children.data?.items ?? []).slice(0, 30).map((item) => (
          <button key={item.name} type="button" className={shared.button} onClick={() => onChange(item.name)}>
            {item.name.split("/").pop()}
          </button>
        ))}
        {children.isPending && <span className={shared.muted}>Loading folders…</span>}
      </div>
    </div>
  );
}

function TagMatchEditor({
  resource,
  options,
  onChange,
}: {
  resource: SubscriptionResource;
  options: SubscriptionOptions;
  onChange: (resource: SubscriptionResource) => void;
}) {
  const setCondition = (index: number, condition: TagCondition) =>
    onChange({ ...resource, conditions: resource.conditions.map((c, i) => (i === index ? condition : c)) });
  const toggleLevel = (kind: SecurableKind) =>
    onChange({
      ...resource,
      levels: resource.levels.includes(kind) ? resource.levels.filter((k) => k !== kind) : [...resource.levels, kind],
    });
  return (
    <div className={shared.stack}>
      {resource.conditions.map((condition, index) => {
        const isAccess = condition.tagKey.toLowerCase() === options.accessTagKey.toLowerCase();
        const known = (options.tagValues[condition.tagKey.toLowerCase()] ?? []).filter(
          (v) =>
            !isAccess ||
            options.allowedPaths.length === 0 ||
            options.allowedPaths.some((p) => v === p || v.startsWith(`${p}.`)),
        );
        return (
          <div key={index} className={styles.conditionRow}>
            <label className={shared.field}>
              <span className={shared.label}>{index === 0 ? "Where tag" : "and tag"}</span>
              <select
                className={shared.select}
                value={condition.tagKey}
                onChange={(e) =>
                  setCondition(index, {
                    tagKey: e.target.value,
                    values: [],
                    match: e.target.value === options.accessTagKey ? "PREFIX" : "EXACT",
                  })
                }
              >
                {options.tagKeys.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label className={shared.field}>
              <span className={shared.label}>Match</span>
              <select
                className={shared.select}
                value={condition.match}
                onChange={(e) => setCondition(index, { ...condition, match: e.target.value as TagCondition["match"] })}
              >
                <option value="PREFIX">is under</option>
                <option value="EXACT">is exactly</option>
              </select>
            </label>
            <div className={shared.fieldGrow}>
              <span className={shared.label}>Values (any of)</span>
              <ValueChips
                values={condition.values}
                suggestions={known}
                onChange={(values) => setCondition(index, { ...condition, values })}
              />
            </div>
            {resource.conditions.length > 1 && (
              <div className={shared.field}>
                <span className={shared.label} aria-hidden="true">
                  &nbsp;
                </span>
                <button
                  type="button"
                  className={shared.button}
                  onClick={() => onChange({ ...resource, conditions: resource.conditions.filter((_, i) => i !== index) })}
                >
                  Remove
                </button>
              </div>
            )}
          </div>
        );
      })}
      <div>
        <button
          type="button"
          className={shared.button}
          onClick={() =>
            onChange({
              ...resource,
              conditions: [...resource.conditions, { tagKey: options.tagKeys[0] ?? "", values: [], match: "EXACT" }],
            })
          }
        >
          + Add condition
        </button>
      </div>
      <div>
        <span className={shared.label}>Grant on matching</span>
        <div className={styles.quickRow}>
          {TAG_MATCH_KINDS.map((kind) => (
            <label key={kind} className={shared.checkboxRow}>
              <input type="checkbox" checked={resource.levels.includes(kind)} onChange={() => toggleLevel(kind)} />
              {KIND_LABELS[kind].many}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function ValueChips({
  values,
  suggestions,
  onChange,
}: {
  values: string[];
  suggestions: string[];
  onChange: (values: string[]) => void;
}) {
  const [text, setText] = useState("");
  const add = (value: string) => {
    const v = value.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setText("");
  };
  return (
    <div className={shared.stack}>
      {suggestions.length > 0 ? (
        <SearchableSelect
          aria-label="Add value"
          value=""
          options={suggestions.filter((s) => !values.includes(s))}
          placeholder="Add a value…"
          onChange={(value) => value && add(value)}
        />
      ) : (
        <input
          className={shared.input}
          value={text}
          placeholder="Type a value and press Enter"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(text);
            }
          }}
          onBlur={() => text && add(text)}
        />
      )}
      {values.length > 0 && (
        <div className={shared.chipRow}>
          {values.map((v) => (
            <span key={v} className={shared.chip}>
              {v}
              <button
                type="button"
                className={shared.chipRemove}
                aria-label={`Remove ${v}`}
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const ADMIN_ONLY = new Set(["ALL PRIVILEGES", "MANAGE", "CAN_MANAGE"]);

function PrivilegePicker({
  resource,
  kinds,
  options,
  onChange,
}: {
  resource: SubscriptionResource;
  kinds: SecurableKind[];
  options: SubscriptionOptions;
  onChange: (resource: SubscriptionResource) => void;
}) {
  const presets = (["READ", "BUILD", "VOLUME_READ_WRITE", "ADMIN"] as PrivilegePreset[])
    .filter((p) => options.presets[p] || p === resource.preset)
    .concat("CUSTOM");
  const available = Array.from(new Set(kinds.flatMap((k) => options.privileges[k] ?? []))).filter(
    (p) => options.isAdmin || !ADMIN_ONLY.has(p) || resource.privileges.includes(p),
  );
  const presetPrivileges = (preset: PrivilegePreset) =>
    Array.from(new Set(kinds.flatMap((k) => options.presets[preset]?.[k] ?? [])));
  const preview = resource.preset === "CUSTOM" ? resource.privileges : presetPrivileges(resource.preset);
  const groups = PRIVILEGE_GROUPS.map((group, index) => ({
    title: group.title,
    items: available.filter(
      (p) => group.match(p) && !PRIVILEGE_GROUPS.slice(0, index).some((earlier) => earlier.match(p)),
    ),
  })).filter((g) => g.items.length > 0);
  const toggle = (privilege: string) =>
    onChange({
      ...resource,
      privileges: resource.privileges.includes(privilege)
        ? resource.privileges.filter((p) => p !== privilege)
        : [...resource.privileges, privilege],
    });
  return (
    <div className={shared.stack}>
      <span className={shared.label}>Permissions</span>
      <div className={styles.choiceGrid}>
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-pressed={resource.preset === preset}
            className={`${styles.choice} ${resource.preset === preset ? styles.choiceActive : ""}`}
            onClick={() =>
              onChange({
                ...resource,
                preset,
                privileges: preset === "CUSTOM" ? presetPrivileges("READ") : [],
              })
            }
          >
            <span className={styles.choiceTitle}>{PRESET_LABELS[preset].label}</span>
            <span className={styles.choiceHint}>{PRESET_LABELS[preset].hint}</span>
          </button>
        ))}
      </div>
      {resource.preset === "CUSTOM" ? (
        <div className={shared.stack}>
          {groups.map((group) => (
            <fieldset key={group.title} className={styles.privilegeGroup}>
              <legend className={shared.label}>{group.title}</legend>
              <div className={styles.privilegeGrid}>
                {group.items.map((privilege) => (
                  <label key={privilege} className={shared.checkboxRow}>
                    <input
                      type="checkbox"
                      checked={resource.privileges.includes(privilege)}
                      onChange={() => toggle(privilege)}
                    />
                    {privilege}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
        </div>
      ) : (
        <p className={shared.muted}>
          Grants {preview.length ? preview.join(", ") : "nothing on this object type — choose another preset"}
          {kinds.some((k) => DATA_KINDS.includes(k)) ? ", plus USE CATALOG and USE SCHEMA to reach it." : "."}
        </p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ attributes

function AttributesStep({ draft, onChange }: { draft: WizardDraft; onChange: (patch: Partial<WizardDraft>) => void }) {
  const definitions = useAttributeDefinitions();
  const defs = definitions.data?.definitions ?? [];
  const setRow = (index: number, key: string, value: string) =>
    onChange({
      attributes: draft.attributes.map((a, i) => (i === index ? { attributeKey: key, attributeValue: value } : a)),
    });
  return (
    <Card title="Attributes while active (optional)">
      <div className={shared.stack}>
        <p className={shared.muted}>
          The subscription group holds these Identity attribute values until it ends: sensitivity values unmask
          columns, row-filter values widen row filters, and data-access values add the group to that access path.
        </p>
        {definitions.isPending && <Spinner label="Loading attributes" />}
        {definitions.error && <ErrorAlert error={definitions.error} title="Could not load attributes" />}
        {draft.attributes.map((attribute, index) => {
          const definition = defs.find((d) => d.key === attribute.attributeKey);
          return (
            <div key={index} className={styles.conditionRow}>
              <label className={shared.field}>
                <span className={shared.label}>Attribute</span>
                <select
                  className={shared.select}
                  value={attribute.attributeKey}
                  onChange={(e) => setRow(index, e.target.value, "")}
                >
                  <option value="">Choose…</option>
                  {defs.map((d) => (
                    <option key={d.key} value={d.key}>
                      {d.key}
                    </option>
                  ))}
                </select>
              </label>
              <div className={shared.fieldGrow}>
                <span className={shared.label}>Value</span>
                <SearchableSelect
                  aria-label="Attribute value"
                  value={attribute.attributeValue}
                  disabled={!definition}
                  options={(definition?.allowedValues ?? []).filter((v) => v !== "*")}
                  onChange={(value) => setRow(index, attribute.attributeKey, value)}
                />
              </div>
              <div className={shared.field}>
                <span className={shared.label} aria-hidden="true">
                  &nbsp;
                </span>
                <button
                  type="button"
                  className={shared.button}
                  onClick={() => onChange({ attributes: draft.attributes.filter((_, i) => i !== index) })}
                >
                  Remove
                </button>
              </div>
            </div>
          );
        })}
        <div>
          <button
            type="button"
            className={shared.button}
            onClick={() => onChange({ attributes: [...draft.attributes, { attributeKey: "", attributeValue: "" }] })}
          >
            + Add attribute
          </button>
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ review

function ReviewStep({
  draft,
  options,
  problems,
}: {
  draft: WizardDraft;
  options: SubscriptionOptions;
  problems: string[][];
}) {
  const preview = usePreviewSubscription();
  const blocking = problems.flatMap((p, i) => p.map((text) => `${STEPS[i]}: ${text}`));
  const body = useMemo(() => toBody(draft), [draft]);
  const { mutate } = preview;
  useEffect(() => {
    if (blocking.length === 0) mutate(body);
  }, [JSON.stringify(body)]);

  const people = draft.subjects.length === 1 ? draft.subjects[0]?.principalName : `${draft.subjects.length} principals`;
  return (
    <>
      <Card title="Read back">
        <div className={styles.summary}>
          <strong>{people}</strong> get the following through <code>{options.groupPrefix + (draft.name || "…")}</code>{" "}
          from <strong>{formatWhen(fromLocalInput(draft.startsAt))}</strong> until{" "}
          <strong>{formatWhen(fromLocalInput(draft.expiresAt))}</strong>:
          <ul>
            {draft.resources.map((r, i) => (
              <li key={i}>
                {describePrivileges(r)} on {describeResource(r).replace(/^./, (c) => c.toLowerCase())}
              </li>
            ))}
            {draft.attributes.map((a, i) => (
              <li key={`a${i}`}>
                {a.attributeKey} = {a.attributeValue}
              </li>
            ))}
          </ul>
          {draft.reviewExempt
            ? "The review gate is waived."
            : "Only objects marked ready by the review gate are reached."}
          {options.requiresApproval && " An app admin must approve it before it takes effect."}
        </div>
      </Card>
      {blocking.length > 0 && (
        <Alert tone="warning" title="Finish these first">
          <ul className={styles.errorList}>
            {blocking.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </Alert>
      )}
      <Card title="What it reaches right now">
        {preview.isPending && <Spinner label="Matching objects" />}
        {preview.error && <ErrorAlert error={preview.error} title="Could not preview" />}
        {preview.data && (
          <div className={shared.stack}>
            <div className={styles.stats}>
              <Stat label="Objects" value={preview.data.totalObjects} />
              <Stat label="Grants (with traversal)" value={preview.data.grantCount} />
              <Stat label="Held back by review gate" value={preview.data.skippedUnreviewed} />
            </div>
            {preview.data.warnings.map((w) => (
              <Alert key={w} tone="warning">
                {w}
              </Alert>
            ))}
            {preview.data.totalObjects === 0 ? (
              <p className={shared.empty}>Nothing matches yet. Tag matches pick up new objects on each sync.</p>
            ) : (
              <ObjectTable objects={preview.data.objects.slice(0, 100)} />
            )}
            {preview.data.totalObjects > 100 && (
              <p className={shared.muted}>Showing 100 of {preview.data.totalObjects}.</p>
            )}
          </div>
        )}
      </Card>
    </>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{value}</span>
      <span className={shared.label}>{label}</span>
    </div>
  );
}

/** Table of matched objects (shared with the detail page). */
export function ObjectTable({
  objects,
}: {
  objects: {
    securableKind: SecurableKind;
    fullName: string;
    label?: string | null;
    privileges: string[];
    accessPath?: string | null;
  }[];
}) {
  return (
    <div className={shared.tableWrap}>
      <table className={shared.table} aria-label="Matched objects">
        <thead>
          <tr>
            <th>Object</th>
            <th>Type</th>
            <th>Privileges</th>
            <th>Access path</th>
          </tr>
        </thead>
        <tbody>
          {objects.map((o) => (
            <tr key={`${o.securableKind}:${o.fullName}`}>
              <td>
                {o.label ? (
                  <>
                    {o.label} <code className={shared.muted}>{o.fullName}</code>
                  </>
                ) : (
                  <code>{o.fullName}</code>
                )}
              </td>
              <td>{KIND_LABELS[o.securableKind].one}</td>
              <td>{o.privileges.join(", ")}</td>
              <td className={shared.muted}>{o.accessPath ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

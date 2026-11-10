import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useCurrentUser,
  useDeleteMaskRule,
  useDraftMaskHandler,
  useEnsureMaskRulePack,
  useMaskRulePack,
  useMaskRules,
  useRotateMaskSalt,
  useSeedMaskRules,
  useUpsertMaskRule,
  useValidateMaskHandler,
} from "@/api/queries";
import type {
  MaskHandler,
  MaskHandlerSampleResult,
  MaskRuleDefinition,
  MaskRuleKind,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

const HANDLERS: { value: MaskHandler; label: string }[] = [
  { value: "full", label: "Full redact (***/NULL)" },
  { value: "joinable", label: "Joinable hash" },
  { value: "email", label: "Email (***@domain)" },
  { value: "phone", label: "Phone (***-***-last4)" },
  { value: "keep_last_n", label: "Keep last N characters" },
];

export function MaskRulesPage() {
  const { data: user } = useCurrentUser();
  const isAdmin = Boolean(user?.isAdmin);

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Mask rules"
        description={
          <>
            Presentation rules for denied columns. Tag a column with{" "}
            <code>mask_rule=&lt;name&gt;</code> alongside sensitivity tags. Authorization still uses{" "}
            <Link to="/identity">Identity</Link> sensitivity attributes. Custom handlers regenerate{" "}
            <code>apply_mask_rule</code> after a successful warehouse validation.
          </>
        }
      />
      {isAdmin && <PackCard />}
      <RulesCard isAdmin={isAdmin} />
      <Alert title="Applying a rule">
        On each sensitive column set <code>mask_rule</code> to one of the names below. Missing or
        unknown rules fall back to <code>full</code>.
      </Alert>
    </div>
  );
}

function PackCard() {
  const { data, error, isPending } = useMaskRulePack();
  const ensure = useEnsureMaskRulePack();
  const seed = useSeedMaskRules();
  const rotate = useRotateMaskSalt();
  const config = data?.config;

  return (
    <Card
      title="Policy pack"
      actions={config ? <Badge>{config.inSync ? "In sync" : "Needs sync"}</Badge> : null}
    >
      {isPending && <Spinner label="Loading pack status" />}
      {error && <ErrorAlert error={error} title="Could not load pack status" />}
      {config && (
        <div className={styles.stack}>
          <div className={styles.muted}>
            Policy <code>{config.policyName}</code> calls <code>{config.functionName}</code>. Tag key{" "}
            <code>{config.tagKey}</code>. Joinable salt:{" "}
            {config.joinableSaltConfigured ? "configured" : "missing"}. UDF:{" "}
            {config.udfInSync ? "in sync" : "out of sync"}.
          </div>
          {config.missingTagValues.length > 0 && (
            <Alert tone="warning" title="Tag values missing">
              {config.missingTagValues.join(", ")}
            </Alert>
          )}
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={ensure.isPending}
              onClick={() => void ensure.mutateAsync()}
            >
              {ensure.isPending ? "Syncing…" : "Sync registry, UDF and policy"}
            </button>
            <button
              type="button"
              className={styles.button}
              disabled={seed.isPending}
              onClick={() => void seed.mutateAsync()}
            >
              Seed defaults
            </button>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonDanger}`}
              disabled={rotate.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    "Rotate joinable salt? Existing hashed values will no longer match.",
                  )
                ) {
                  void rotate.mutateAsync();
                }
              }}
            >
              Rotate joinable salt
            </button>
          </div>
          {ensure.error && <ErrorAlert error={ensure.error} title="Sync failed" />}
          {seed.error && <ErrorAlert error={seed.error} title="Seed failed" />}
          {rotate.error && <ErrorAlert error={rotate.error} title="Rotate failed" />}
        </div>
      )}
    </Card>
  );
}

function RulesCard({ isAdmin }: { isAdmin: boolean }) {
  const { data, error, isPending } = useMaskRules();
  const remove = useDeleteMaskRule();
  const [editing, setEditing] = useState<MaskRuleDefinition | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  return (
    <Card
      title="Named rules"
      actions={
        isAdmin ? (
          <button
            type="button"
            className={styles.button}
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            New rule
          </button>
        ) : data ? (
          <Badge>{data.total}</Badge>
        ) : null
      }
    >
      {isPending && <Spinner label="Loading mask rules" />}
      {error && <ErrorAlert error={error} title="Could not load mask rules" />}
      {isAdmin && formOpen && (
        <RuleForm
          key={editing?.ruleKey ?? "new"}
          initial={editing}
          onDone={() => {
            setFormOpen(false);
            setEditing(null);
          }}
        />
      )}
      {data && data.rules.length === 0 && (
        <div className={styles.empty}>No mask rules yet. Seed defaults or create one.</div>
      )}
      {data && data.rules.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="Mask rules">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Kind</th>
                <th>Handler</th>
                <th>Expression / params</th>
                {isAdmin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.rules.map((rule) => (
                <tr key={rule.ruleKey}>
                  <td>
                    <code>{rule.ruleKey}</code>
                    {rule.description && <div className={styles.muted}>{rule.description}</div>}
                  </td>
                  <td>
                    <Badge>{rule.kind}</Badge>
                  </td>
                  <td>
                    <Badge>{rule.handler}</Badge>
                  </td>
                  <td>
                    <code>
                      {rule.kind === "custom"
                        ? rule.expression ?? ""
                        : JSON.stringify(rule.params ?? {})}
                    </code>
                  </td>
                  {isAdmin && (
                    <td>
                      <div className={styles.buttonRow}>
                        <button
                          type="button"
                          className={styles.button}
                          onClick={() => {
                            setEditing(rule);
                            setFormOpen(true);
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className={`${styles.button} ${styles.buttonDanger}`}
                          disabled={remove.isPending || rule.ruleKey === "full"}
                          onClick={() => void remove.mutateAsync(rule.ruleKey)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {remove.error && <ErrorAlert error={remove.error} title="Delete failed" />}
    </Card>
  );
}

function RuleForm({
  initial,
  onDone,
}: {
  initial: MaskRuleDefinition | null;
  onDone: () => void;
}) {
  const upsert = useUpsertMaskRule();
  const draft = useDraftMaskHandler();
  const validate = useValidateMaskHandler();
  const [ruleKey, setRuleKey] = useState(initial?.ruleKey ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [kind, setKind] = useState<MaskRuleKind>(initial?.kind ?? "builtin");
  const [handler, setHandler] = useState<MaskHandler>(
    initial?.kind === "builtin" &&
      ["full", "joinable", "email", "phone", "keep_last_n"].includes(initial.handler)
      ? (initial.handler as MaskHandler)
      : "full",
  );
  const [keepLastN, setKeepLastN] = useState(
    String(typeof initial?.params?.n === "number" ? initial.params.n : 4),
  );
  const [prompt, setPrompt] = useState("");
  const [expression, setExpression] = useState(initial?.expression ?? "");
  const [explanation, setExplanation] = useState("");
  const [samples, setSamples] = useState<MaskHandlerSampleResult[]>([]);
  const [validationToken, setValidationToken] = useState<string | null>(null);

  const keyOk = /^[a-z][a-z0-9_]{0,62}$/.test(ruleKey.trim().toLowerCase());
  const canSave =
    keyOk &&
    (kind === "builtin" || Boolean(validationToken && expression.trim())) &&
    !upsert.isPending;

  const clearValidation = () => {
    setValidationToken(null);
    setSamples([]);
  };

  const onSave = async () => {
    if (kind === "custom") {
      await upsert.mutateAsync({
        ruleKey: ruleKey.trim().toLowerCase(),
        description: description.trim() || null,
        kind: "custom",
        expression: expression.trim(),
        validationToken,
      });
    } else {
      await upsert.mutateAsync({
        ruleKey: ruleKey.trim().toLowerCase(),
        description: description.trim() || null,
        kind: "builtin",
        handler,
        params: handler === "keep_last_n" ? { n: Number(keepLastN) } : {},
      });
    }
    onDone();
  };

  return (
    <div className={styles.approvalCard}>
      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span className={styles.label}>Rule key</span>
          <input
            className={styles.input}
            value={ruleKey}
            disabled={Boolean(initial)}
            placeholder="employee_email"
            onChange={(event) => setRuleKey(event.target.value)}
          />
        </label>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Description</span>
          <input
            className={styles.input}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Kind</span>
          <select
            className={styles.select}
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as MaskRuleKind);
              clearValidation();
            }}
          >
            <option value="builtin">Builtin handler</option>
            <option value="custom">Custom SQL handler</option>
          </select>
        </label>
        {kind === "builtin" ? (
          <>
            <label className={styles.field}>
              <span className={styles.label}>Handler</span>
              <select
                className={styles.select}
                value={handler}
                onChange={(event) => setHandler(event.target.value as MaskHandler)}
              >
                {HANDLERS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {handler === "keep_last_n" && (
              <label className={styles.field}>
                <span className={styles.label}>Keep last N</span>
                <input
                  className={styles.input}
                  type="number"
                  min={1}
                  max={64}
                  value={keepLastN}
                  onChange={(event) => setKeepLastN(event.target.value)}
                />
              </label>
            )}
          </>
        ) : (
          <>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>Describe the mask (for AI draft)</span>
              <textarea
                className={styles.textarea}
                value={prompt}
                placeholder="Prefix a sha2 hash with ID- so joins still work"
                onChange={(event) => setPrompt(event.target.value)}
              />
            </label>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>SQL expression over v</span>
              <textarea
                className={styles.textarea}
                value={expression}
                placeholder="concat('ID-', sha2(lower(trim(v)), 256))"
                onChange={(event) => {
                  setExpression(event.target.value);
                  clearValidation();
                }}
              />
            </label>
          </>
        )}
      </div>
      {kind === "custom" && explanation && <div className={styles.muted}>{explanation}</div>}
      {kind === "custom" && samples.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="Validation samples">
            <thead>
              <tr>
                <th>Input</th>
                <th>Output</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {samples.map((sample) => (
                <tr key={`${sample.input}-${sample.output}-${sample.error}`}>
                  <td>
                    <code>{sample.input}</code>
                  </td>
                  <td>
                    <code>{sample.output ?? "—"}</code>
                  </td>
                  <td className={styles.muted}>{sample.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {kind === "custom" && validationToken && (
        <Alert tone="success" title="Validated">
          Expression passed warehouse dry-run. You can save now.
        </Alert>
      )}
      <div className={styles.buttonRow}>
        {kind === "custom" && (
          <>
            <button
              type="button"
              className={styles.button}
              disabled={!prompt.trim() || draft.isPending}
              onClick={() =>
                void draft.mutateAsync({ prompt: prompt.trim(), expression: expression || null }).then(
                  (result) => {
                    setExpression(result.expression);
                    setExplanation(result.explanation);
                    clearValidation();
                  },
                )
              }
            >
              {draft.isPending ? "Drafting…" : "Draft with AI"}
            </button>
            <button
              type="button"
              className={styles.button}
              disabled={!expression.trim() || validate.isPending}
              onClick={() =>
                void validate.mutateAsync({ expression: expression.trim() }).then((result) => {
                  setExpression(result.expression);
                  setSamples(result.samples);
                  setValidationToken(result.ok ? result.validationToken : null);
                })
              }
            >
              {validate.isPending ? "Validating…" : "Validate"}
            </button>
          </>
        )}
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          disabled={!canSave}
          onClick={() => void onSave()}
        >
          {upsert.isPending ? "Saving…" : initial ? "Save rule" : "Create rule"}
        </button>
        <button type="button" className={styles.button} onClick={onDone}>
          Cancel
        </button>
      </div>
      {draft.error && <ErrorAlert error={draft.error} title="Draft failed" />}
      {validate.error && <ErrorAlert error={validate.error} title="Validate failed" />}
      {upsert.error && <ErrorAlert error={upsert.error} title="Save failed" />}
    </div>
  );
}

/**
 * @file Attribute Explorer — who holds a path and which tables data-access unlocks.
 *
 * Admins see every attribute; path approvers only paths they can decide.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useAnalyzeAttribute,
  useAttributeDefinitions,
  useCurrentUser,
  useDataAccessSettings,
} from "@/api/queries";
import type { AttributeDefinition, AttributeExplorerResult } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "../identity/IdentityPage.module.css";

/**
 * Classify a definition for the attribute picker.
 */
function definitionKind(
  item: AttributeDefinition,
  accessTagKey: string,
): "sensitivity" | "data_access" | "row_filter" | "other" {
  if (item.key.toLowerCase() === "sensitivity") return "sensitivity";
  const access = accessTagKey.toLowerCase();
  if (
    item.key.toLowerCase() === access ||
    item.governedTagKeys.some((tag) => tag.toLowerCase() === access)
  ) {
    return "data_access";
  }
  if (item.valueSource !== "governed_tags") return "row_filter";
  return "other";
}

/**
 * Admin / approver reverse lookup: holders + unlocked tables for a path.
 */
export function AttributeExplorerPage() {
  const { data: user } = useCurrentUser();
  const definitions = useAttributeDefinitions();
  const dataAccessSettings = useDataAccessSettings();
  const analyze = useAnalyzeAttribute();
  const [searchParams, setSearchParams] = useSearchParams();

  const accessTagKey =
    dataAccessSettings.data?.settings.accessTagKey?.trim() || "data_access_control";

  const [attributeKey, setAttributeKey] = useState(searchParams.get("key") ?? "");
  const [attributeValue, setAttributeValue] = useState(searchParams.get("value") ?? "");
  const [result, setResult] = useState<AttributeExplorerResult | null>(null);

  const defs = definitions.data?.definitions ?? [];
  const selected = defs.find((d) => d.key === attributeKey);
  const values = useMemo(
    () => (selected?.allowedValues ?? []).filter((v) => v && v !== "*"),
    [selected],
  );

  useEffect(() => {
    const key = searchParams.get("key");
    const value = searchParams.get("value");
    if (key) setAttributeKey(key);
    if (value) setAttributeValue(value);
  }, [searchParams]);

  const runAnalyze = () => {
    if (!attributeKey.trim() || !attributeValue.trim()) return;
    const params = new URLSearchParams(searchParams);
    params.set("key", attributeKey.trim());
    params.set("value", attributeValue.trim());
    setSearchParams(params, { replace: true });
    void analyze
      .mutateAsync({ attributeKey: attributeKey.trim(), attributeValue: attributeValue.trim() })
      .then((body) => setResult(body));
  };

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Attribute explorer"
        description={
          user?.isAdmin
            ? "See who holds an attribute path and which reviewed tables a data-access value unlocks."
            : "Inspect attribute paths you approve — holders and, for data access, unlocked tables."
        }
      />

      <Card title="Look up">
        {definitions.isPending && <Spinner label="Loading definitions" />}
        {definitions.error && (
          <ErrorAlert error={definitions.error} title="Could not load definitions" />
        )}
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Attribute</span>
            <select
              className={styles.select}
              value={attributeKey}
              onChange={(event) => {
                setAttributeKey(event.target.value);
                setAttributeValue("");
                setResult(null);
              }}
              aria-label="Attribute"
            >
              <option value="">Select attribute</option>
              {defs.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.key} ({definitionKind(item, accessTagKey).replaceAll("_", " ")})
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Value / path</span>
            {values.length > 0 ? (
              <select
                className={styles.select}
                value={attributeValue}
                onChange={(event) => setAttributeValue(event.target.value)}
                aria-label="Value"
              >
                <option value="">Select value</option>
                {values.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={styles.input}
                value={attributeValue}
                onChange={(event) => setAttributeValue(event.target.value)}
                placeholder="e.g. finance"
                aria-label="Value"
              />
            )}
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!attributeKey.trim() || !attributeValue.trim() || analyze.isPending}
            onClick={runAnalyze}
          >
            {analyze.isPending ? "Analyzing…" : "Analyze"}
          </button>
          <Link className={styles.button} to="/access-explorer">
            Open Access explorer
          </Link>
        </div>
        {analyze.error && <ErrorAlert error={analyze.error} title="Analyze failed" />}
      </Card>

      {result && <ResultPanel result={result} />}
    </div>
  );
}

function ResultPanel({ result }: { result: AttributeExplorerResult }) {
  return (
    <div className={styles.stack}>
      <Card
        title={`${result.attributeKey} = ${result.attributeValue}`}
        actions={<Badge>{result.kind.replaceAll("_", " ")}</Badge>}
      >
        {result.groupName && (
          <p className={styles.muted}>
            Group <code>{result.groupName}</code>
            {result.parentGroup ? (
              <>
                {" "}
                (parent nested <code>{result.parentGroup}</code>)
              </>
            ) : null}
          </p>
        )}
        {result.note && <Alert title="Note">{result.note}</Alert>}
      </Card>

      <Card title="Holders" actions={<Badge>{result.holders.length}</Badge>}>
        {result.holders.length === 0 ? (
          <div className={styles.empty}>No active assignments for this path.</div>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="Holders">
              <thead>
                <tr>
                  <th>Principal</th>
                  <th>Type</th>
                  <th>Assigned value</th>
                  <th>Match</th>
                  <th>Expires</th>
                </tr>
              </thead>
              <tbody>
                {result.holders.map((item) => (
                  <tr key={item.assignmentId}>
                    <td>
                      <code>{item.principalName}</code>
                    </td>
                    <td>
                      <Badge>{item.principalType}</Badge>
                    </td>
                    <td>
                      <code>{item.attributeValue}</code>
                    </td>
                    <td>{item.match}</td>
                    <td className={styles.muted}>
                      {item.expiresAt ? new Date(item.expiresAt).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {result.kind === "data_access" && (
        <Card title="Tables unlocked" actions={<Badge>{result.tables.length}</Badge>}>
          {result.tables.length === 0 ? (
            <div className={styles.empty}>
              No reviewed tables tagged with this path (or a descendant).
            </div>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table} aria-label="Tables unlocked">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th>Tag value</th>
                    <th>Match</th>
                    <th>Group</th>
                  </tr>
                </thead>
                <tbody>
                  {result.tables.map((item) => (
                    <tr key={item.fullName}>
                      <td>
                        <Link to={`/access-explorer?table=${encodeURIComponent(item.fullName)}`}>
                          <code>{item.fullName}</code>
                        </Link>
                      </td>
                      <td>
                        <code>{item.tagValue}</code>
                      </td>
                      <td>{item.match}</td>
                      <td>
                        <code>{item.groupName ?? "—"}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

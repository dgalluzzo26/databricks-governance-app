/**
 * @file Searchable backend API reference generated from the live FastAPI route table.
 *
 * Endpoints come from `GET /help/api-reference`, grouped by router tag. Each row expands to the
 * handler docstring, parameters, request/response models, and the implementing Python function.
 */
import { useMemo, useState } from "react";
import { useHelpApiReference } from "@/api/queries";
import type { ApiEndpoint, EndpointAccess } from "@/api/types";
import { Badge, ErrorAlert, Spinner } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";
import styles from "./HelpPage.module.css";

const ACCESS_TONE: Record<EndpointAccess, BadgeTone> = {
  public: "neutral",
  user: "info",
  admin: "warning",
};

const METHOD_TONE: Record<string, BadgeTone> = {
  GET: "success",
  POST: "info",
  PUT: "warning",
  PATCH: "warning",
  DELETE: "danger",
};

/** API reference tab: search and access filters over every `/api` endpoint. */
export function ApiReferenceView() {
  const reference = useHelpApiReference();
  const [q, setQ] = useState("");
  const [access, setAccess] = useState<EndpointAccess | "">("");

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = (reference.data?.endpoints ?? []).filter((endpoint) => {
      if (access && endpoint.access !== access) return false;
      if (!needle) return true;
      return [endpoint.path, endpoint.summary, endpoint.tag, endpoint.handler, endpoint.method]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    const byTag = new Map<string, ApiEndpoint[]>();
    for (const endpoint of filtered) {
      byTag.set(endpoint.tag, [...(byTag.get(endpoint.tag) ?? []), endpoint]);
    }
    return [...byTag.entries()];
  }, [reference.data, q, access]);

  if (reference.isPending) return <Spinner label="Loading API reference" />;
  if (reference.error) return <ErrorAlert error={reference.error} title="Could not load API reference" />;

  const total = groups.reduce((sum, [, items]) => sum + items.length, 0);

  return (
    <div className={styles.apiRef}>
      <div className={styles.toolbar}>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Search</span>
          <input
            className={styles.input}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="Path, summary, tag, or handler"
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Access</span>
          <select
            className={styles.input}
            value={access}
            onChange={(event) => setAccess(event.target.value as EndpointAccess | "")}
          >
            <option value="">All</option>
            <option value="public">Public</option>
            <option value="user">Signed-in user</option>
            <option value="admin">App admin</option>
          </select>
        </label>
        <span className={styles.muted}>{total} endpoints</span>
      </div>

      {groups.map(([tag, endpoints]) => (
        <section key={tag} className={styles.apiGroup}>
          <h3>{tag}</h3>
          {endpoints.map((endpoint) => (
            <EndpointRow key={`${endpoint.method} ${endpoint.path}`} endpoint={endpoint} />
          ))}
        </section>
      ))}
    </div>
  );
}

/** One collapsible endpoint row. */
function EndpointRow({ endpoint }: { endpoint: ApiEndpoint }) {
  return (
    <details className={styles.endpoint}>
      <summary>
        <Badge tone={METHOD_TONE[endpoint.method] ?? "neutral"}>{endpoint.method}</Badge>
        <code className={styles.endpointPath}>{endpoint.path}</code>
        <Badge tone={ACCESS_TONE[endpoint.access]}>{endpoint.access}</Badge>
        <span className={styles.endpointSummary}>{endpoint.summary}</span>
      </summary>
      <div className={styles.endpointBody}>
        <dl className={styles.endpointMeta}>
          <dt>Handler</dt>
          <dd>
            <code>{endpoint.handler}</code>
          </dd>
          <dt>Request body</dt>
          <dd>{endpoint.requestBody ? <code>{endpoint.requestBody}</code> : "None"}</dd>
          <dt>Response</dt>
          <dd>{endpoint.responseModel ? <code>{endpoint.responseModel}</code> : "Untyped / no content"}</dd>
        </dl>
        {endpoint.parameters.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Parameter</th>
                <th>In</th>
                <th>Type</th>
                <th>Required</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {endpoint.parameters.map((param) => (
                <tr key={`${param.location}-${param.name}`}>
                  <td>
                    <code>{param.name}</code>
                  </td>
                  <td>{param.location}</td>
                  <td>
                    <code>{param.type}</code>
                  </td>
                  <td>{param.required ? "yes" : "no"}</td>
                  <td>{param.description ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {endpoint.description && <pre className={styles.docstring}>{endpoint.description}</pre>}
      </div>
    </details>
  );
}

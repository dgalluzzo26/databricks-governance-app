/**
 * @file Developer guide page: one tab per Markdown guide in `docs/` plus a generated API reference.
 *
 * Guides come from `useHelpDocs` / `useHelpDoc` and render with `MarkdownDoc` (Mermaid blocks
 * become diagrams). The active tab is stored in the `?tab=` search parameter: a guide slug or
 * `api` for the API reference.
 */
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useHelpDoc, useHelpDocs } from "@/api/queries";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { ApiReferenceView } from "./ApiReferenceView";
import { headingId, MarkdownDoc } from "./MarkdownDoc";
import styles from "./HelpPage.module.css";

/** `?tab=` value of the generated API reference tab. */
const API_TAB = "api";

/**
 * Help page with a tab per developer guide and an API reference tab.
 *
 * Unknown `?tab=` values fall back to the first guide.
 */
export function HelpPage() {
  const docs = useHelpDocs();
  const [searchParams, setSearchParams] = useSearchParams();
  const items = docs.data?.docs ?? [];
  const requested = searchParams.get("tab");
  const active =
    requested === API_TAB || items.some((doc) => doc.slug === requested)
      ? (requested as string)
      : (items[0]?.slug ?? null);
  const activeDoc = items.find((doc) => doc.slug === active) ?? null;

  /** Switches tabs by updating `?tab=` (replacing the history entry). */
  const setTab = (next: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Developer guide"
        description="Decision flows for masking, row filters, access requests and grants sync, plus a live reference of every backend API."
        actions={<Badge tone="info">Help</Badge>}
      />

      {docs.isPending && <Spinner label="Loading guides" />}
      {docs.error && <ErrorAlert error={docs.error} title="Could not load help" />}

      {docs.data && (
        <>
          <div className={styles.tabs} role="tablist" aria-label="Help guides">
            {items.map((doc) => (
              <button
                key={doc.slug}
                type="button"
                role="tab"
                aria-selected={active === doc.slug}
                className={`${styles.tab}${active === doc.slug ? ` ${styles.tabActive}` : ""}`}
                onClick={() => setTab(doc.slug)}
              >
                {doc.title}
              </button>
            ))}
            <button
              type="button"
              role="tab"
              aria-selected={active === API_TAB}
              className={`${styles.tab}${active === API_TAB ? ` ${styles.tabActive}` : ""}`}
              onClick={() => setTab(API_TAB)}
            >
              API reference
            </button>
          </div>

          {active === API_TAB ? (
            <Card title="API reference">
              <Alert title="Generated from the running backend">
                Every <code>/api</code> route with its access level, models, and handler docstring. See the{" "}
                <strong>Backend &amp; APIs</strong> guide for how routes, services, and Databricks APIs fit together.
              </Alert>
              <ApiReferenceView />
            </Card>
          ) : (
            activeDoc && <GuideView slug={activeDoc.slug} title={activeDoc.title} source={activeDoc.sourcePath} />
          )}
        </>
      )}
    </>
  );
}

/** One Markdown guide with a jump-to list built from its `##` headings. */
function GuideView({ slug, title, source }: { slug: string; title: string; source: string }) {
  const doc = useHelpDoc(slug);
  const sections = useMemo(
    () =>
      (doc.data ?? "")
        .split("\n")
        .filter((line) => line.startsWith("## "))
        .map((line) => line.slice(3))
        .map((text) => ({ id: headingId(text), label: text.replace(/[`*]/g, "") })),
    [doc.data],
  );

  return (
    <>
      {sections.length > 0 && (
        <Card title="Jump to">
          <div className={styles.toc}>
            {sections.map((section) => (
              <a key={section.id} href={`#${section.id}`}>
                <Badge>{section.label}</Badge>
              </a>
            ))}
          </div>
          <Alert title="Source of truth">
            This tab renders <code>{source}</code> from the deployed app.
          </Alert>
        </Card>
      )}
      <Card title={title}>
        {doc.isPending && <Spinner label="Loading guide" />}
        {doc.error && <ErrorAlert error={doc.error} title="Could not load guide" />}
        {doc.data && <MarkdownDoc source={doc.data} />}
      </Card>
    </>
  );
}

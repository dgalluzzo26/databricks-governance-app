import { useHelpFlows } from "@/api/queries";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { MarkdownDoc } from "./MarkdownDoc";
import styles from "./HelpPage.module.css";

const SECTIONS = [
  { id: "mental-model", label: "Mental model" },
  { id: "column-mask-flow", label: "Column masks" },
  { id: "row-filter-flow", label: "Row filters" },
  { id: "access-explorer-read-request-path", label: "Access explorer" },
  { id: "identity-attributes-that-power-both-flows", label: "Identity attributes" },
  { id: "operational-checklist", label: "Checklist" },
];

export function HelpPage() {
  const doc = useHelpFlows();

  return (
    <>
      <PageHeader
        title="Developer guide"
        description="How column masks, row filters, identity attributes, and Access Explorer fit together — and how access requests are routed."
        actions={<Badge tone="info">Help</Badge>}
      />

      <Card title="Jump to">
        <div className={styles.toc}>
          {SECTIONS.map((section) => (
            <a key={section.id} href={`#${section.id}`}>
              <Badge>{section.label}</Badge>
            </a>
          ))}
        </div>
        <Alert title="Source of truth">
          This page renders <code>docs/mask-and-row-filter-flows.md</code> from the deployed app. Prefer
          Access Explorer to discover which attributes to request for a given table.
        </Alert>
      </Card>

      <Card title="Mask and row-filter flows">
        {doc.isPending && <Spinner label="Loading guide" />}
        {doc.error && <ErrorAlert error={doc.error} title="Could not load help" />}
        {doc.data && <MarkdownDoc source={doc.data} />}
      </Card>
    </>
  );
}

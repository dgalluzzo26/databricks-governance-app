/**
 * @file Lazy catalog → schema → table browser for Access Explorer.
 *
 * Each tree level fetches only when expanded. Catalog and schema rows expand only;
 * table leaves trigger analysis.
 */
import { useState } from "react";
import { useTreeCatalogs, useTreeSchemas, useTreeTables } from "@/api/queries";
import { Badge, ErrorAlert, Spinner } from "@/components/ui";
import styles from "./AccessExplorerTree.module.css";

/** Props for the Access Explorer catalog tree. */
interface TreeProps {
  /** Currently analyzed three-part table name, or `null` when none is selected. */
  selected: string | null;
  /** Called with `catalog.schema.table` when a table leaf is clicked. */
  onSelectTable: (fullName: string) => void;
}

/**
 * Lazy catalog → schema → table browser. Each level loads only when expanded.
 */
export function AccessExplorerTree({ selected, onSelectTable }: TreeProps) {
  const catalogs = useTreeCatalogs();

  if (catalogs.isPending) return <Spinner label="Loading catalogs" />;
  if (catalogs.error) return <ErrorAlert error={catalogs.error} title="Could not load catalogs" />;
  if (!catalogs.data || catalogs.data.catalogs.length === 0) {
    return <div className={styles.notice}>No catalogs visible to the app.</div>;
  }

  return (
    <div className={styles.tree} role="tree" aria-label="Data catalogs">
      {catalogs.data.catalogs.map((c) => (
        <CatalogNode
          key={c.catalogName}
          catalog={c.catalogName}
          selected={selected}
          onSelectTable={onSelectTable}
        />
      ))}
      {catalogs.data.truncated && (
        <div className={styles.notice}>Showing the first {catalogs.data.total} catalogs.</div>
      )}
    </div>
  );
}

function CatalogNode({
  catalog,
  selected,
  onSelectTable,
}: {
  catalog: string;
  selected: string | null;
  onSelectTable: (n: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const schemas = useTreeSchemas(catalog, open);

  return (
    <div role="none">
      <button
        type="button"
        role="treeitem"
        aria-expanded={open}
        className={styles.row}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.chevron}>{open ? "▾" : "▸"}</span>
        <span>{catalog}</span>
      </button>
      {open && (
        <div role="group">
          {schemas.isPending && <Spinner label={`Loading ${catalog} schemas`} />}
          {schemas.error && <ErrorAlert error={schemas.error} title="Could not load schemas" />}
          {schemas.data?.schemas.map((s) => (
            <SchemaNode
              key={s.schemaName}
              catalog={catalog}
              schema={s.schemaName}
              selected={selected}
              onSelectTable={onSelectTable}
            />
          ))}
          {schemas.data?.schemas.length === 0 && (
            <div className={`${styles.notice} ${styles.indent1}`}>No schemas.</div>
          )}
        </div>
      )}
    </div>
  );
}

function SchemaNode({
  catalog,
  schema,
  selected,
  onSelectTable,
}: {
  catalog: string;
  schema: string;
  selected: string | null;
  onSelectTable: (n: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const tables = useTreeTables(catalog, schema, open);

  return (
    <div role="none">
      <button
        type="button"
        role="treeitem"
        aria-expanded={open}
        className={`${styles.row} ${styles.indent1}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.chevron}>{open ? "▾" : "▸"}</span>
        <span>{schema}</span>
      </button>
      {open && (
        <div role="group">
          {tables.isPending && <Spinner label={`Loading ${schema} tables`} />}
          {tables.error && <ErrorAlert error={tables.error} title="Could not load tables" />}
          {tables.data?.tables.map((t) => {
            const full = `${t.catalogName}.${t.schemaName}.${t.tableName}`;
            return (
              <button
                key={full}
                type="button"
                role="treeitem"
                aria-selected={selected === full}
                className={`${styles.row} ${styles.indent2} ${selected === full ? styles.selected : ""}`}
                onClick={() => onSelectTable(full)}
                title={full}
              >
                <span className={styles.muted}>▫</span>
                <span>{t.tableName}</span>
                <Badge>{t.tableType.toLowerCase().replace(/_/g, " ")}</Badge>
              </button>
            );
          })}
          {tables.data?.tables.length === 0 && (
            <div className={`${styles.notice} ${styles.indent2}`}>No tables.</div>
          )}
          {tables.data?.truncated && (
            <div className={`${styles.notice} ${styles.indent2}`}>
              Showing the first {tables.data.total} tables; use search for the rest.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

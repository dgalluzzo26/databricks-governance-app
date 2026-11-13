/**
 * @file Table header with sortable column labels and a per-column filter row.
 */
import type { TableView } from "@/hooks/useTableView";
import { isFilterable, isSortable } from "@/hooks/useTableView";
import styles from "./DataTableHead.module.css";

/** Props for {@link DataTableHead}. */
export interface DataTableHeadProps<T> {
  /** The table state from `useTableView`; its `columns` define the header order. */
  view: Pick<
    TableView<T>,
    "columns" | "sortKey" | "sortDirection" | "toggleSort" | "filters" | "setFilter" | "hasFilters"
  >;
}

/**
 * Renders a `<thead>` with one sortable header per sortable column and, below it, a
 * text filter per filterable column.
 *
 * @remarks
 * Place this inside a `<table>` in place of a hand-written `<thead>`. The header cells come
 * from `view.columns`, so body cells must follow the same column order.
 *
 * @param props - See {@link DataTableHeadProps}.
 */
export function DataTableHead<T>({ view }: DataTableHeadProps<T>) {
  const showFilters = view.columns.some((column) => isFilterable(column));
  return (
    <thead>
      <tr>
        {view.columns.map((column) => {
          if (!isSortable(column)) return <th key={column.key}>{column.label}</th>;
          const active = view.sortKey === column.key;
          const ariaSort = active ? (view.sortDirection === "asc" ? "ascending" : "descending") : "none";
          return (
            <th key={column.key} aria-sort={ariaSort}>
              <button
                type="button"
                className={styles.sortButton}
                onClick={() => view.toggleSort(column.key)}
                title={`Sort by ${column.label}`}
              >
                {column.label}
                <span className={styles.sortIndicator} aria-hidden="true">
                  {active ? (view.sortDirection === "asc" ? "▲" : "▼") : "↕"}
                </span>
              </button>
            </th>
          );
        })}
      </tr>
      {showFilters && (
        <tr className={styles.filterRow}>
          {view.columns.map((column) => {
            if (!isFilterable(column)) return <th key={column.key} aria-hidden="true" />;
            return (
              <th key={column.key}>
                <input
                  className={styles.filterInput}
                  aria-label={`Filter ${column.label}`}
                  placeholder="Filter"
                  value={view.filters[column.key] ?? ""}
                  onChange={(event) => view.setFilter(column.key, event.target.value)}
                />
              </th>
            );
          })}
        </tr>
      )}
    </thead>
  );
}

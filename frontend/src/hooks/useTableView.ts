/**
 * @file Client-side table view state: per-column text filters, one sort column, and pagination.
 */
import { useMemo, useState } from "react";
import { useClientPagination } from "@/hooks/useClientPagination";
import type { TablePageSize } from "@/components/ui/TablePager";

/** A value a column can sort by. */
export type TableSortValue = string | number | boolean | null | undefined;

/**
 * One table column. Column order must match the body cells rendered for each row.
 *
 * @remarks
 * Columns without `value` or `filterText` (for example an action column) are neither
 * sortable nor filterable.
 */
export interface TableColumn<T> {
  /** Stable key, unique within the table. */
  key: string;
  /** Header label. */
  label: string;
  /** Sort key. Sorting is enabled when this is set (unless `sortable` is false). */
  value?: (row: T) => TableSortValue;
  /** Text matched by the column filter. Defaults to `value`. */
  filterText?: (row: T) => string | null | undefined;
  /** Override whether the column can be sorted. */
  sortable?: boolean;
  /** Override whether the column can be filtered. */
  filterable?: boolean;
}

/** Sort direction for a column. */
export type SortDirection = "asc" | "desc";

/** Options for {@link useTableView}. */
export interface UseTableViewOptions {
  /** Initial page size (default 10). */
  defaultPageSize?: TablePageSize;
  /** Set to false to show every matching row without pagination (default true). */
  paginate?: boolean;
  /** Extra reset key (for example a parent filter); the page resets to 0 when it changes. */
  resetKey?: string | number;
}

/** Everything a table needs for filtering, sorting, and paging. */
export interface TableView<T> {
  /** Rows on the current page (all matching rows when pagination is off). */
  rows: readonly T[];
  /** Rows after filters, before paging. */
  total: number;
  /** Zero-based page index. */
  page: number;
  pageSize: TablePageSize;
  pageCount: number;
  setPage: (page: number) => void;
  setPageSize: (pageSize: TablePageSize) => void;
  /** Whether pagination is on for this table. */
  paginate: boolean;
  columns: readonly TableColumn<T>[];
  /** Key of the column currently sorted, or null. */
  sortKey: string | null;
  sortDirection: SortDirection;
  /** Cycles a column through ascending, descending, and unsorted. */
  toggleSort: (key: string) => void;
  /** Current text per column key; empty string means no filter. */
  filters: Readonly<Record<string, string>>;
  setFilter: (key: string, text: string) => void;
  /** Whether any column filter is set. */
  hasFilters: boolean;
}

/** Whether a column can be sorted. */
export function isSortable<T>(column: TableColumn<T>): boolean {
  return column.sortable ?? column.value !== undefined;
}

/** Whether a column can be filtered. */
export function isFilterable<T>(column: TableColumn<T>): boolean {
  return column.filterable ?? (column.value !== undefined || column.filterText !== undefined);
}

function filterTextOf<T>(column: TableColumn<T>, row: T): string {
  if (column.filterText) return column.filterText(row) ?? "";
  const value = column.value?.(row);
  return value === null || value === undefined ? "" : String(value);
}

function isEmptyValue(value: TableSortValue): boolean {
  return value === null || value === undefined || value === "";
}

/** Compares two non-empty values; empty values are handled by the caller. */
function compareValues(a: TableSortValue, b: TableSortValue): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Filter, sort, and paginate an in-memory list for a table.
 *
 * @remarks
 * Filters are case-insensitive "contains" matches per column, combined with AND. Sorting
 * cycles ascending, descending, then unsorted (original order). Empty values sort last.
 * Pass a stable `columns` array (module-level or memoized); a new array each render is
 * correct but recomputes the view each time.
 *
 * @param items - Full list of rows.
 * @param columns - Column definitions in display order.
 * @param options - Pagination and reset options.
 * @returns The current page plus sort, filter, and pager state.
 */
export function useTableView<T>(
  items: readonly T[],
  columns: readonly TableColumn<T>[],
  options: UseTableViewOptions = {},
): TableView<T> {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});

  const filtered = useMemo(() => {
    const active = columns
      .filter((column) => isFilterable(column) && (filters[column.key] ?? "").trim() !== "")
      .map((column) => ({ column, needle: (filters[column.key] ?? "").trim().toLowerCase() }));
    if (active.length === 0) return items;
    return items.filter((row) =>
      active.every(({ column, needle }) => filterTextOf(column, row).toLowerCase().includes(needle)),
    );
  }, [items, columns, filters]);

  const sorted = useMemo(() => {
    const column = sort ? columns.find((candidate) => candidate.key === sort.key) : undefined;
    if (!sort || !column || !isSortable(column) || !column.value) return filtered;
    const getValue = column.value;
    const sign = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const aValue = getValue(a);
      const bValue = getValue(b);
      const aEmpty = isEmptyValue(aValue);
      const bEmpty = isEmptyValue(bValue);
      // Empty values always sort last, regardless of direction.
      if (aEmpty || bEmpty) return aEmpty === bEmpty ? 0 : aEmpty ? 1 : -1;
      return sign * compareValues(aValue, bValue);
    });
  }, [filtered, columns, sort]);

  const hasFilters = Object.values(filters).some((text) => text.trim() !== "");
  const paginate = options.paginate ?? true;
  const pager = useClientPagination(sorted, {
    defaultPageSize: options.defaultPageSize,
    resetKey: [
      sort ? `${sort.key}:${sort.direction}` : "",
      JSON.stringify(filters),
      options.resetKey ?? "",
    ].join("|"),
  });

  return {
    rows: paginate ? pager.rows : sorted,
    total: sorted.length,
    page: pager.page,
    pageSize: pager.pageSize,
    pageCount: paginate ? pager.pageCount : 1,
    setPage: pager.setPage,
    setPageSize: pager.setPageSize,
    paginate,
    columns,
    sortKey: sort?.key ?? null,
    sortDirection: sort?.direction ?? "asc",
    toggleSort: (key: string) =>
      setSort((current) => {
        const column = columns.find((candidate) => candidate.key === key);
        if (!column || !isSortable(column)) return current;
        if (!current || current.key !== key) return { key, direction: "asc" };
        if (current.direction === "asc") return { key, direction: "desc" };
        return null;
      }),
    filters,
    setFilter: (key: string, text: string) => setFilters((current) => ({ ...current, [key]: text })),
    hasFilters,
  };
}

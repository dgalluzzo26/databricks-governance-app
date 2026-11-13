/**
 * @file Column sort and filter state for server-paged tables.
 */
import { useMemo, useState } from "react";
import type { TableQueryParams } from "@/api/types";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { SortDirection, TableColumn } from "@/hooks/useTableView";

/** Options for {@link useServerTableView}. */
export interface UseServerTableViewOptions {
  /** Called when the sort or any filter changes (use it to return to the first page). */
  onChange?: () => void;
}

/** Sort and filter state for a server-paged table. */
export interface ServerTableView {
  columns: readonly TableColumn<unknown>[];
  sortKey: string | null;
  sortDirection: SortDirection;
  toggleSort: (key: string) => void;
  filters: Readonly<Record<string, string>>;
  setFilter: (key: string, text: string) => void;
  hasFilters: boolean;
  /** Sort and filters to send to the endpoint. Filter text is debounced; the inputs are not. */
  query: TableQueryParams;
}

/**
 * Sort and filter state for a table whose rows come from the server one page at a time.
 *
 * @remarks
 * The columns must be declared as sortable or filterable explicitly (`sortable` /
 * `filterable`), since there are no client-side value readers. Pass `query` to the paged
 * hook. The endpoint applies sort and filters before paging, so results are correct across
 * all pages.
 *
 * @param columns - Column definitions in display order.
 * @param options - Optional change callback (for example, reset to page 1).
 * @returns Header state plus the debounced `query` for the request.
 */
export function useServerTableView(
  columns: readonly TableColumn<unknown>[],
  options: UseServerTableViewOptions = {},
): ServerTableView {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const onChange = options.onChange;

  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filters).filter(([, text]) => text.trim() !== "")),
    [filters],
  );
  // Debounce the text, so typing does not send one request per keystroke.
  const debouncedFilters = useDebouncedValue(JSON.stringify(activeFilters), 300);
  const query = useMemo<TableQueryParams>(
    () => ({
      sort: sort?.key,
      direction: sort?.direction ?? "asc",
      filters: JSON.parse(debouncedFilters) as Record<string, string>,
    }),
    [sort, debouncedFilters],
  );

  return {
    columns,
    sortKey: sort?.key ?? null,
    sortDirection: sort?.direction ?? "asc",
    toggleSort: (key: string) => {
      const column = columns.find((candidate) => candidate.key === key);
      if (!column?.sortable) return;
      setSort((current) => {
        if (!current || current.key !== key) return { key, direction: "asc" };
        if (current.direction === "asc") return { key, direction: "desc" };
        return null;
      });
      onChange?.();
    },
    filters,
    setFilter: (key: string, text: string) => {
      setFilters((current) => ({ ...current, [key]: text }));
      onChange?.();
    },
    hasFilters: Object.keys(activeFilters).length > 0,
    query,
  };
}

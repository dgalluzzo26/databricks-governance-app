/**
 * @file Client-side list pagination: page, page size, and the current slice of rows.
 */
import { useEffect, useMemo, useState } from "react";
import { TABLE_PAGE_SIZES, type TablePageSize } from "@/components/ui/TablePager";

/** Options for {@link useClientPagination}. */
export interface UseClientPaginationOptions {
  /** Initial page size (default 10). */
  defaultPageSize?: TablePageSize;
  /**
   * When this value changes, the page resets to 0. Use filter strings so searching or
   * switching tabs does not leave the user on an empty later page.
   */
  resetKey?: string | number;
}

/**
 * Paginate an in-memory list for table UIs.
 *
 * @param items - Full list to page.
 * @param options - Default size and optional reset key.
 * @returns The current page of rows plus pager state and setters.
 */
export function useClientPagination<T>(items: readonly T[], options: UseClientPaginationOptions = {}) {
  const defaultPageSize = options.defaultPageSize ?? TABLE_PAGE_SIZES[0];
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(defaultPageSize);

  useEffect(() => {
    setPage(0);
  }, [options.resetKey, pageSize]);

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const rows = useMemo(() => {
    const start = safePage * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, pageSize, safePage]);

  return {
    rows,
    total: items.length,
    page: safePage,
    pageSize,
    pageCount,
    setPage,
    setPageSize,
  };
}

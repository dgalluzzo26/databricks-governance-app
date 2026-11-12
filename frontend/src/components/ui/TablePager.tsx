/**
 * @file Client-side table pagination controls: rows-per-page and previous/next.
 */
import styles from "./TablePager.module.css";

/** Allowed page sizes for list tables. */
export const TABLE_PAGE_SIZES = [10, 25, 50, 100] as const;

/** One of {@link TABLE_PAGE_SIZES}. */
export type TablePageSize = (typeof TABLE_PAGE_SIZES)[number];

/** Props for {@link TablePager}. */
export interface TablePagerProps {
  /** Total rows in the unpaged list. */
  total: number;
  /** Zero-based page index. */
  page: number;
  /** Rows shown per page. */
  pageSize: TablePageSize;
  /** Called when the user changes page or page size. */
  onPageChange: (page: number) => void;
  /** Called when the user picks a new page size (resets to page 0). */
  onPageSizeChange: (pageSize: TablePageSize) => void;
}

/**
 * Footer under a table: "1–10 of N", rows-per-page select, and previous/next.
 *
 * Hidden when ``total`` is 0. Always shows the pager when there is at least one row so
 * the user can raise the page size before the list grows past 10.
 *
 * @param props - See {@link TablePagerProps}.
 */
export function TablePager({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: TablePagerProps) {
  if (total === 0) return null;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const from = safePage * pageSize + 1;
  const to = Math.min(total, (safePage + 1) * pageSize);

  return (
    <div className={styles.pager}>
      <div className={styles.meta}>
        {from}–{to} of {total}
      </div>
      <div className={styles.controls}>
        <label className={styles.rows}>
          <span className={styles.muted}>Rows</span>
          <select
            className={styles.select}
            aria-label="Rows per page"
            value={pageSize}
            onChange={(event) => {
              onPageSizeChange(Number(event.target.value) as TablePageSize);
              onPageChange(0);
            }}
          >
            {TABLE_PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className={styles.button}
          disabled={safePage === 0}
          onClick={() => onPageChange(Math.max(0, safePage - 1))}
        >
          Previous
        </button>
        <span className={styles.meta}>
          Page {safePage + 1} of {pageCount}
        </span>
        <button
          type="button"
          className={styles.button}
          disabled={safePage >= pageCount - 1}
          onClick={() => onPageChange(Math.min(pageCount - 1, safePage + 1))}
        >
          Next
        </button>
      </div>
    </div>
  );
}

/**
 * @file Definition-list layout for labeled key/value metadata rows.
 */
import type { ReactNode } from "react";
import styles from "./DescriptionList.module.css";

/** One label/value pair rendered as a `dt`/`dd` row. */
export interface DescriptionItem {
  /** Row label (`dt`); also used as the React key. */
  label: string;
  /** Row value (`dd`); `null`/`undefined` display as an em dash. */
  value: ReactNode;
}

/**
 * Renders a vertical list of labeled values.
 * @param props.items - Ordered description rows.
 */
export function DescriptionList({ items }: { items: DescriptionItem[] }) {
  return (
    <dl className={styles.list}>
      {items.map((item) => (
        <div key={item.label} className={styles.row}>
          <dt className={styles.label}>{item.label}</dt>
          <dd className={styles.value}>{item.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

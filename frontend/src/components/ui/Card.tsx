/**
 * @file Section card with optional header title/actions and a padded body.
 */
import type { ReactNode } from "react";
import styles from "./Card.module.css";

/** Props for {@link Card}. */
interface CardProps {
  /** Optional heading rendered in the card header. */
  title?: ReactNode;
  /** Optional controls aligned with the title in the header. */
  actions?: ReactNode;
  /** Main card content. */
  children: ReactNode;
}

/**
 * Bordered content section used to group related page content.
 * @param props - See {@link CardProps}.
 * @remarks The header renders only when `title` or `actions` is provided.
 */
export function Card({ title, actions, children }: CardProps) {
  return (
    <section className={styles.card}>
      {(title || actions) && (
        <header className={styles.header}>
          <h2 className={styles.title}>{title}</h2>
          {actions}
        </header>
      )}
      <div className={styles.body}>{children}</div>
    </section>
  );
}

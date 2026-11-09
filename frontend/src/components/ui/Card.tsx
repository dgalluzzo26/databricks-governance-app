import type { ReactNode } from "react";
import styles from "./Card.module.css";

interface CardProps {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

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

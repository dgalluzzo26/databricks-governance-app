/**
 * @file Decorative initials avatar derived from a display name or email local-part.
 */
import styles from "./Avatar.module.css";

/**
 * Derives one or two uppercase initials from a name or email.
 * @param name - Display name or email; the `@domain` portion is stripped when present.
 * @returns Initials string, or `"?"` when no usable characters remain.
 */
export function initials(name: string): string {
  const parts = name
    .replace(/@.*$/, "")
    .split(/[\s._-]+/)
    .filter(Boolean);
  const letters = parts.length > 1 ? [parts[0]![0], parts[parts.length - 1]![0]] : [parts[0]?.[0]];
  return letters.filter(Boolean).join("").toUpperCase() || "?";
}

/**
 * Circular initials badge; decorative and marked `aria-hidden`.
 * @param props.name - Source string for {@link initials}.
 * @param props.size - Diameter in CSS pixels (default 28).
 */
export function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  return (
    <span className={styles.avatar} style={{ width: size, height: size, fontSize: size * 0.4 }} aria-hidden>
      {initials(name)}
    </span>
  );
}

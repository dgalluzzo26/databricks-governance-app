/**
 * @file Searchable user/group principal picker for Identity assignment and approver forms.
 */
import { useEffect, useId, useRef, useState } from "react";
import { usePrincipalSearch } from "@/api/queries";
import type { DirectoryPrincipal, PrincipalKind } from "@/api/types";
import styles from "../pages/identity/IdentityPage.module.css";

/** Props for {@link PrincipalPicker}. */
interface PrincipalPickerProps {
  /** Current principal name (email or group display name). */
  value: string;
  /** Called when the user picks a hit or finishes typing a custom value. */
  onChange: (name: string, kind?: PrincipalKind) => void;
  /** Restrict directory search to one kind; omit to search both. */
  kind?: PrincipalKind;
  /** Input placeholder. */
  placeholder?: string;
  /** Optional accessible label id. */
  "aria-label"?: string;
}

/**
 * Combobox that searches the workspace directory as the admin types.
 *
 * Falls back to free-text entry so known principals can still be entered when search
 * returns nothing. Selecting a hit also reports the principal kind so the parent can
 * sync the type dropdown.
 */
export function PrincipalPicker({
  value,
  onChange,
  kind,
  placeholder = "Search users or groups…",
  "aria-label": ariaLabel = "Principal",
}: PrincipalPickerProps) {
  const listId = useId();
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const search = usePrincipalSearch(query, kind);

  useEffect(() => {
    setQuery(value);
  }, [value]);

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const hits = search.data?.principals ?? [];

  const pick = (item: DirectoryPrincipal) => {
    setQuery(item.name);
    onChange(item.name, item.principalType);
    setOpen(false);
  };

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <input
        className={styles.input}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        value={query}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(event) => {
          const next = event.target.value;
          setQuery(next);
          onChange(next);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          if (query.trim() !== value.trim()) onChange(query.trim());
        }}
      />
      {open && query.trim().length >= 2 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Matching principals"
          style={{
            position: "absolute",
            zIndex: 20,
            left: 0,
            right: 0,
            margin: "4px 0 0",
            padding: 0,
            listStyle: "none",
            maxHeight: 220,
            overflow: "auto",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--color-surface)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
          }}
        >
          {search.isFetching && (
            <li className={styles.muted} style={{ padding: "8px 12px" }}>
              Searching…
            </li>
          )}
          {!search.isFetching && hits.length === 0 && (
            <li className={styles.muted} style={{ padding: "8px 12px" }}>
              No matches — keep typing to use a custom principal name
            </li>
          )}
          {hits.map((item) => (
            <li key={`${item.principalType}:${item.name}`} role="option">
              <button
                type="button"
                className={styles.button}
                style={{
                  width: "100%",
                  height: "auto",
                  justifyContent: "flex-start",
                  border: 0,
                  borderRadius: 0,
                  textAlign: "left",
                  padding: "8px 12px",
                }}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(item)}
              >
                <strong>{item.displayName}</strong>
                <span className={styles.muted} style={{ marginLeft: 8 }}>
                  {item.principalType === "USER" ? item.name : "group"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

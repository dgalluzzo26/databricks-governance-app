/**
 * @file Combobox for long option lists: type to filter, options sorted A–Z.
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import styles from "./SearchableSelect.module.css";

/** One selectable row. */
export interface SearchableSelectOption {
  value: string;
  label?: string;
}

interface SearchableSelectProps {
  /** Selected value (empty string means none). */
  value: string;
  /** Called when the user picks a value (or clears to `""`). */
  onChange: (value: string) => void;
  /** Available choices; sorted A–Z by label unless `sort` is false. */
  options: readonly (string | SearchableSelectOption)[];
  /** Shown when nothing is selected. */
  placeholder?: string;
  /** Accessible name (also used when the control is not wrapped in a `<label>`). */
  "aria-label": string;
  /** Disable interaction. */
  disabled?: boolean;
  /** Sort options alphabetically (default true). */
  sort?: boolean;
  /** Allow clearing the selection with an empty row at the top (default true). */
  allowEmpty?: boolean;
}

function normalize(options: readonly (string | SearchableSelectOption)[]): SearchableSelectOption[] {
  return options.map((item) =>
    typeof item === "string" ? { value: item, label: item } : { value: item.value, label: item.label ?? item.value },
  );
}

/**
 * Dropdown that filters as you type. Prefer this over a native `<select>` when the list is long.
 */
export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  "aria-label": ariaLabel,
  disabled = false,
  sort = true,
  allowEmpty = true,
}: SearchableSelectProps) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const normalized = useMemo(() => {
    const rows = normalize(options);
    if (!sort) return rows;
    return [...rows].sort((a, b) => (a.label ?? a.value).localeCompare(b.label ?? b.value, undefined, { sensitivity: "base" }));
  }, [options, sort]);

  const selected = normalized.find((item) => item.value === value) ?? null;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return normalized;
    return normalized.filter((item) => {
      const hay = `${item.label ?? ""} ${item.value}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [normalized, query]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, open]);

  const pick = (next: string) => {
    onChange(next);
    setOpen(false);
    setQuery("");
    inputRef.current?.blur();
  };

  const openList = () => {
    if (disabled) return;
    setOpen(true);
    setQuery("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setQuery("");
      return;
    }
    if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
      event.preventDefault();
      openList();
      return;
    }
    if (!open) return;
    const rows = allowEmpty && !query.trim() ? filtered.length + 1 : filtered.length;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(rows - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (allowEmpty && !query.trim() && activeIndex === 0) {
        pick("");
        return;
      }
      const offset = allowEmpty && !query.trim() ? 1 : 0;
      const item = filtered[activeIndex - offset];
      if (item) pick(item.value);
    }
  };

  const showEmptyRow = allowEmpty && open && !query.trim();
  const emptyActive = showEmptyRow && activeIndex === 0;

  return (
    <div ref={rootRef} className={styles.root}>
      <input
        ref={inputRef}
        className={`${styles.trigger}${!selected && !open ? ` ${styles.triggerEmpty}` : ""}`}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open ? `${listId}-opt-${activeIndex}` : undefined}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        value={open ? query : (selected?.label ?? selected?.value ?? "")}
        onChange={(event) => {
          setQuery(event.target.value);
          if (!open) setOpen(true);
        }}
        onFocus={openList}
        onClick={openList}
        onKeyDown={onKeyDown}
      />
      {open ? (
        <ul id={listId} role="listbox" aria-label={ariaLabel} className={styles.list}>
          {showEmptyRow ? (
            <li
              role="option"
              id={`${listId}-opt-0`}
              aria-selected={value === ""}
              className={`${styles.option}${emptyActive ? ` ${styles.optionActive}` : ""}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(0)}
              onClick={() => pick("")}
            >
              {placeholder}
            </li>
          ) : null}
          {filtered.length === 0 ? (
            <li className={styles.empty} role="presentation">
              No matches for “{query.trim()}”
            </li>
          ) : (
            filtered.map((item, index) => {
              const optionIndex = showEmptyRow ? index + 1 : index;
              const active = optionIndex === activeIndex;
              const isSelected = item.value === value;
              return (
                <li
                  key={item.value}
                  role="option"
                  id={`${listId}-opt-${optionIndex}`}
                  aria-selected={isSelected}
                  className={`${styles.option}${active ? ` ${styles.optionActive}` : ""}${
                    isSelected ? ` ${styles.optionSelected}` : ""
                  }`}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveIndex(optionIndex)}
                  onClick={() => pick(item.value)}
                >
                  {item.label ?? item.value}
                </li>
              );
            })
          )}
        </ul>
      ) : null}
    </div>
  );
}

import { useEffect, useState } from "react";

/**
 * Returns `value` after it has stopped changing for `delayMs` milliseconds.
 *
 * @remarks Used so search inputs filter as you type without a request per keystroke.
 * @param value - The live value (for example, the text in a search box).
 * @param delayMs - How long to wait after the last change. Defaults to 300.
 * @returns The debounced value.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

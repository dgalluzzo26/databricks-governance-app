import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useServerTableView } from "@/hooks/useServerTableView";
import type { TableColumn } from "@/hooks/useTableView";

const columns: TableColumn<unknown>[] = [
  { key: "name", label: "Name", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "actions", label: "" },
];

describe("useServerTableView", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with no sort or filters", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    expect(result.current.sortKey).toBeNull();
    expect(result.current.hasFilters).toBe(false);
    expect(result.current.query).toEqual({ sort: undefined, direction: "asc", filters: {} });
  });

  it("cycles a sortable column through asc, desc, then unsorted", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    act(() => result.current.toggleSort("name"));
    expect(result.current.query).toMatchObject({ sort: "name", direction: "asc" });
    act(() => result.current.toggleSort("name"));
    expect(result.current.query).toMatchObject({ sort: "name", direction: "desc" });
    act(() => result.current.toggleSort("name"));
    expect(result.current.sortKey).toBeNull();
  });

  it("ignores sort requests for columns that are not sortable", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    act(() => result.current.toggleSort("actions"));
    expect(result.current.sortKey).toBeNull();
  });

  it("calls onChange when the sort changes, so the page can reset", () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => useServerTableView(columns, { onChange }));
    act(() => result.current.toggleSort("status"));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("debounces filter text in the query but updates the inputs immediately", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    act(() => result.current.setFilter("name", "ro"));
    act(() => result.current.setFilter("name", "row-1"));
    // Inputs reflect the live text right away.
    expect(result.current.filters).toEqual({ name: "row-1" });
    expect(result.current.hasFilters).toBe(true);
    // The request has not been sent yet.
    expect(result.current.query.filters).toEqual({});
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current.query.filters).toEqual({ name: "row-1" });
  });

  it("drops blank filters from the query", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    act(() => result.current.setFilter("name", "   "));
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current.hasFilters).toBe(false);
    expect(result.current.query.filters).toEqual({});
  });

  it("sends sort and filters together", () => {
    const { result } = renderHook(() => useServerTableView(columns));
    act(() => result.current.toggleSort("status"));
    act(() => result.current.toggleSort("status"));
    act(() => result.current.setFilter("status", "fail"));
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current.query).toEqual({
      sort: "status",
      direction: "desc",
      filters: { status: "fail" },
    });
  });
});

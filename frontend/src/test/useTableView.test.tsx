import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useTableView, type TableColumn } from "@/hooks/useTableView";

interface Row {
  name: string;
  count: number;
  owner: string | null;
  active: boolean;
}

const rows: Row[] = [
  { name: "beta", count: 2, owner: "zed", active: true },
  { name: "Alpha", count: 10, owner: null, active: false },
  { name: "gamma", count: 1, owner: "amy", active: true },
];

const columns: TableColumn<Row>[] = [
  { key: "name", label: "Name", value: (row) => row.name },
  { key: "count", label: "Count", value: (row) => row.count },
  { key: "owner", label: "Owner", value: (row) => row.owner },
  { key: "active", label: "Active", value: (row) => row.active },
  { key: "actions", label: "" },
];

function names(view: { rows: readonly Row[] }) {
  return view.rows.map((row) => row.name);
}

describe("useTableView", () => {
  it("keeps original order until a column is sorted", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    expect(names(result.current)).toEqual(["beta", "Alpha", "gamma"]);
    expect(result.current.sortKey).toBeNull();
  });

  it("cycles a column through ascending, descending, then unsorted", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    act(() => result.current.toggleSort("name"));
    expect(names(result.current)).toEqual(["Alpha", "beta", "gamma"]);
    expect(result.current.sortDirection).toBe("asc");

    act(() => result.current.toggleSort("name"));
    expect(names(result.current)).toEqual(["gamma", "beta", "Alpha"]);
    expect(result.current.sortDirection).toBe("desc");

    act(() => result.current.toggleSort("name"));
    expect(result.current.sortKey).toBeNull();
    expect(names(result.current)).toEqual(["beta", "Alpha", "gamma"]);
  });

  it("sorts numbers numerically and booleans false-first", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    act(() => result.current.toggleSort("count"));
    expect(names(result.current)).toEqual(["gamma", "beta", "Alpha"]);

    act(() => result.current.toggleSort("active"));
    expect(result.current.sortKey).toBe("active");
    expect(result.current.rows[0]?.name).toBe("Alpha");
  });

  it("sorts empty values last in both directions", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    act(() => result.current.toggleSort("owner"));
    expect(result.current.rows[2]?.name).toBe("Alpha");
    act(() => result.current.toggleSort("owner"));
    expect(result.current.rows[2]?.name).toBe("Alpha");
  });

  it("filters each column case-insensitively and combines filters", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    act(() => result.current.setFilter("name", "ALP"));
    expect(names(result.current)).toEqual(["Alpha"]);
    expect(result.current.total).toBe(1);
    expect(result.current.hasFilters).toBe(true);

    act(() => result.current.setFilter("name", ""));
    act(() => result.current.setFilter("active", "true"));
    expect(names(result.current)).toEqual(["beta", "gamma"]);
  });

  it("does not filter or sort columns that opt out", () => {
    const { result } = renderHook(() => useTableView(rows, columns));
    act(() => result.current.setFilter("actions", "anything"));
    expect(result.current.total).toBe(3);
    act(() => result.current.toggleSort("actions"));
    expect(result.current.sortKey).toBeNull();
  });

  it("uses filterText when provided and pages the filtered result", () => {
    const many = Array.from({ length: 25 }, (_, index) => ({
      name: `row-${index}`,
      count: index,
      owner: null,
      active: index % 2 === 0,
    }));
    const withText: TableColumn<Row>[] = [
      { key: "label", label: "Label", value: (row) => row.count, filterText: (row) => `item ${row.name}` },
    ];
    const { result } = renderHook(() => useTableView(many, withText));
    expect(result.current.rows).toHaveLength(10);
    // "row-1" matches row-1 and row-10 through row-19: 11 rows, so two pages.
    act(() => result.current.setFilter("label", "item row-1"));
    expect(result.current.total).toBe(11);
    expect(result.current.rows).toHaveLength(10);
    expect(result.current.pageCount).toBe(2);
  });

  it("shows every row when pagination is off", () => {
    const many = Array.from({ length: 25 }, (_, index) => ({
      name: `row-${index}`,
      count: index,
      owner: null,
      active: true,
    }));
    const { result } = renderHook(() => useTableView(many, columns, { paginate: false }));
    expect(result.current.rows).toHaveLength(25);
  });
});

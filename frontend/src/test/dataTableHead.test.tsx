import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";

interface Row {
  name: string;
  owner: string;
}

const rows: Row[] = [
  { name: "beta", owner: "zed" },
  { name: "alpha", owner: "amy" },
];

const columns: TableColumn<Row>[] = [
  { key: "name", label: "Name", value: (row) => row.name },
  { key: "owner", label: "Owner", value: (row) => row.owner },
  { key: "actions", label: "Actions" },
];

function Table() {
  const view = useTableView(rows, columns, { paginate: false });
  return (
    <table aria-label="Test table">
      <DataTableHead view={view} />
      <tbody>
        {view.rows.map((row) => (
          <tr key={row.name}>
            <td>{row.name}</td>
            <td>{row.owner}</td>
            <td />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

describe("DataTableHead", () => {
  it("renders sort buttons for sortable columns and plain headers otherwise", () => {
    render(<Table />);
    const header = screen.getByRole("table", { name: "Test table" }).querySelector("thead");
    expect(header).not.toBeNull();
    const headerRow = within(header as HTMLElement).getAllByRole("row")[0] as HTMLElement;
    expect(within(headerRow).getByRole("button", { name: /Name/ })).toBeInTheDocument();
    expect(within(headerRow).getByRole("button", { name: /Owner/ })).toBeInTheDocument();
    // The actions column is neither sortable nor filterable: a plain header cell.
    expect(within(headerRow).getByText("Actions").closest("button")).toBeNull();
  });

  it("toggles aria-sort and reorders rows when a sort button is clicked", async () => {
    const user = userEvent.setup();
    render(<Table />);
    const nameHeader = screen.getByRole("button", { name: /Name/ }).closest("th");
    expect(nameHeader).toHaveAttribute("aria-sort", "none");

    await user.click(screen.getByRole("button", { name: /Name/ }));
    expect(nameHeader).toHaveAttribute("aria-sort", "ascending");
    const body = screen.getByRole("table", { name: "Test table" }).querySelector("tbody") as HTMLElement;
    expect(within(body).getAllByRole("row")[0]).toHaveTextContent("alpha");

    await user.click(screen.getByRole("button", { name: /Name/ }));
    expect(nameHeader).toHaveAttribute("aria-sort", "descending");
    expect(within(body).getAllByRole("row")[0]).toHaveTextContent("beta");
  });

  it("filters rows through the per-column filter inputs", async () => {
    const user = userEvent.setup();
    render(<Table />);
    const body = screen.getByRole("table", { name: "Test table" }).querySelector("tbody") as HTMLElement;

    await user.type(screen.getByLabelText("Filter Owner"), "AM");
    expect(within(body).getAllByRole("row")).toHaveLength(1);
    expect(within(body).getByText("alpha")).toBeInTheDocument();
    // The actions column has no filter input.
    expect(screen.queryByLabelText("Filter Actions")).toBeNull();
  });
});

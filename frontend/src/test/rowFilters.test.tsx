import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Row filters", () => {
  it("shows the policy pack, dimensions and filtered columns", async () => {
    renderApp("/row-filters");

    expect(await screen.findByRole("heading", { name: "Row filters" })).toBeInTheDocument();
    expect(await screen.findByText("In sync")).toBeInTheDocument();
    const dims = await screen.findByRole("table", { name: "Row filter dimensions" });
    expect(within(dims).getByText("state", { selector: "code" })).toBeInTheDocument();
    expect(within(dims).getByText("SQL seed query")).toBeInTheDocument();
    expect(within(dims).getByText("*")).toBeInTheDocument();
    const columns = await screen.findByRole("table", { name: "Filtered columns" });
    expect(within(columns).getByText("cat.sch.us_states")).toBeInTheDocument();
  });

  it("previews a SQL seed and creates a dimension", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const upsert = vi.spyOn(services.rowFilters, "upsertDimension");
    renderApp("/row-filters", services);

    await user.click(await screen.findByRole("button", { name: "New dimension" }));
    await user.type(screen.getByLabelText("Dimension key"), "region");
    const create = screen.getByRole("button", { name: "Create dimension" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText(/Seed query/), "SELECT DISTINCT region FROM t");
    await user.click(screen.getByRole("button", { name: "Preview values" }));
    expect(await screen.findByText("3 values:")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create dimension" }));

    expect(upsert).toHaveBeenCalledWith(
      expect.objectContaining({ key: "region", valueSource: "sql", valueSql: "SELECT DISTINCT region FROM t" }),
    );
  });

  it("attaches a dimension to a column", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const attach = vi.spyOn(services.rowFilters, "attach");
    renderApp("/row-filters", services);

    await screen.findByRole("option", { name: "state" });
    await user.type(screen.getByLabelText("Table"), "cat.sch.customers");
    await user.type(screen.getByLabelText("Column"), "state_code");
    await user.click(screen.getByRole("button", { name: "Attach row filter" }));

    expect(attach).toHaveBeenCalledWith({
      tableFullName: "cat.sch.customers",
      columnName: "state_code",
      dimension: "state",
    });
  });

  it("is read-only for non-admins", async () => {
    renderApp("/row-filters", fakeServices({ user: { ...testUser, isAdmin: false, groups: ["users"] } }));

    expect(await screen.findByRole("table", { name: "Row filter dimensions" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New dimension" })).not.toBeInTheDocument();
    expect(screen.queryByText("Policy pack")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Detach" })).not.toBeInTheDocument();
  });

  it("links SQL definitions to row filters on the identity page", async () => {
    renderApp("/identity?tab=definitions");

    const table = await screen.findByRole("table", { name: "Attribute definitions" });
    expect(within(table).getByRole("link", { name: "Edit in Row filters" })).toHaveAttribute(
      "href",
      "/row-filters",
    );
  });
});

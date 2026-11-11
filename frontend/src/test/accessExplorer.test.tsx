import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp } from "./renderApp";

describe("Access explorer", () => {
  it("analyzes a table and shows masks, row filter, and suggestions", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const analyze = vi.spyOn(services.accessExplorer, "analyze");
    renderApp("/access-explorer", services);

    expect(await screen.findByRole("heading", { name: "Access explorer" })).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("catalog.schema.table"), "demo.default.customers");
    await user.click(screen.getByRole("button", { name: "Analyze" }));

    expect(analyze).toHaveBeenCalledWith({ tableFullName: "demo.default.customers" });
    expect(await screen.findByText("demo.default.customers")).toBeInTheDocument();

    const sensitivity = screen.getByRole("table", { name: "Sensitivity suggestions" });
    expect(within(sensitivity).getAllByText("finance.budget.PII.Name").length).toBeGreaterThan(0);
    expect(
      within(sensitivity).getByRole("checkbox", {
        name: "Select sensitivity=finance.budget.PII.Name",
      }),
    ).toBeChecked();

    const wildcard = screen.getByRole("table", { name: "Row filter wildcard" });
    expect(
      within(wildcard).getByRole("checkbox", {
        name: "Select state=*",
      }),
    ).toBeChecked();
    // Concrete values stay hidden until the user searches.
    expect(screen.queryByRole("checkbox", { name: "Select state=CA" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Request/ })).toBeDisabled();
    expect(screen.getByRole("link", { name: "My requests" })).toHaveAttribute(
      "href",
      "/identity?tab=my-requests",
    );

    const columns = screen.getByRole("table", { name: "Column access" });
    expect(within(columns).getByText("Masked")).toBeInTheDocument();
    expect(within(columns).getByText("Not governed")).toBeInTheDocument();
    expect(screen.getByText("No rows")).toBeInTheDocument();
  });

  it("searches tables and analyzes a hit", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const search = vi.spyOn(services.accessExplorer, "search");
    const analyze = vi.spyOn(services.accessExplorer, "analyze");
    renderApp("/access-explorer", services);

    expect(await screen.findByRole("heading", { name: "Access explorer" })).toBeInTheDocument();
    await user.type(
      await screen.findByPlaceholderText("Type at least 2 characters"),
      "cust",
    );
    expect(await screen.findByRole("table", { name: "Table search results" })).toBeInTheDocument();
    expect(search).toHaveBeenCalled();
    const results = screen.getByRole("table", { name: "Table search results" });
    await user.click(within(results).getAllByRole("button", { name: "Analyze" })[0]!);
    expect(analyze).toHaveBeenCalledWith({ tableFullName: "demo.default.customers" });
  });

  it("browses the tree lazily: catalog, then schema, then table click analyzes", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const schemas = vi.spyOn(services.accessExplorer, "schemas");
    const tables = vi.spyOn(services.accessExplorer, "tables");
    const analyze = vi.spyOn(services.accessExplorer, "analyze");
    renderApp("/access-explorer", services);

    const demo = await screen.findByRole("treeitem", { name: /demo$/ });
    expect(schemas).not.toHaveBeenCalled();
    await user.click(demo);
    expect(schemas).toHaveBeenCalledWith("demo", expect.anything());

    await user.click(await screen.findByRole("treeitem", { name: /default$/ }));
    expect(tables).toHaveBeenCalledWith("demo", "default", expect.anything());

    await user.click(await screen.findByRole("treeitem", { name: /customers/ }));
    expect(analyze).toHaveBeenCalledWith({ tableFullName: "demo.default.customers" });
  });
});

describe("Access explorer row-filter selection", () => {
  it("searches values, selects matches, and clears without listing every value", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    renderApp("/access-explorer", services);

    await user.type(await screen.findByPlaceholderText("catalog.schema.table"), "demo.default.customers");
    await user.click(screen.getByRole("button", { name: "Analyze" }));

    const wildcard = await screen.findByRole("table", { name: "Row filter wildcard" });
    const star = within(wildcard).getByRole("checkbox", { name: "Select state=*" });
    expect(star).toBeChecked();

    await user.type(screen.getByLabelText("Find row-filter values"), "CA");
    const matches = await screen.findByRole("table", { name: "Row filter value matches" });
    const ca = within(matches).getByRole("checkbox", { name: "Select state=CA" });
    expect(ca).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Select matches (1)" }));
    expect(ca).toBeChecked();
    expect(star).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Remove state=CA" })).toBeInTheDocument();

    await user.click(star);
    expect(star).toBeChecked();
    expect(ca).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Clear all row-filter values" }));
    expect(star).not.toBeChecked();
  });
});

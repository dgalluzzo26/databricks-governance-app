import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderApp } from "./renderApp";

describe("Data access analytics access reports", () => {
  it("renders Access reports tab with governed tables and unused / no-access lists", async () => {
    renderApp("/data-access/analytics?tab=access");

    expect(await screen.findByRole("tab", { name: "Access reports" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByRole("heading", { name: "Governed tables" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "data_access_control.finance.budget" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Unused access" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reads without assigned access" })).toBeInTheDocument();
    expect(screen.getAllByText("bo@example.com").length).toBeGreaterThan(0);
    expect(screen.getAllByText("eve@example.com").length).toBeGreaterThan(0);
  });

  it("opens table access detail when clicking a governed table", async () => {
    const user = userEvent.setup();
    renderApp("/data-access/analytics?tab=access");

    const tableBtn = await screen.findByRole("button", {
      name: "data_access_control.finance.budget",
    });
    await user.click(tableBtn);

    expect(
      await screen.findByRole("heading", {
        name: "Access detail: data_access_control.finance.budget",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("ana@example.com")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Table holders" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Readers without access" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close access detail" }));
    expect(
      screen.queryByRole("heading", {
        name: "Access detail: data_access_control.finance.budget",
      }),
    ).not.toBeInTheDocument();
  });

  it("switches between usage, access, and sync tabs", async () => {
    const user = userEvent.setup();
    renderApp("/data-access/analytics");

    const accessTab = await screen.findByRole("tab", { name: "Access reports" });
    await user.click(accessTab);
    expect(accessTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Governed tables" })).toBeInTheDocument();

    const syncTab = screen.getByRole("tab", { name: "Sync health" });
    await user.click(syncTab);
    expect(syncTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Run outcomes per day")).toBeInTheDocument();

    const usageTab = screen.getByRole("tab", { name: "Data usage" });
    await user.click(usageTab);
    expect(usageTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Sensitive column usage")).toBeInTheDocument();
  });
});

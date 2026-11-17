import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp } from "./renderApp";

describe("Data access analytics usage", () => {
  it("renders Data usage tab with KPIs, tables, and sensitive breakdown", async () => {
    renderApp("/data-access/analytics");
    expect(await screen.findByRole("heading", { name: "Data access analytics" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Data usage" })).toHaveAttribute("aria-selected", "true");

    // KPIs from testUsageOverview
    expect(await screen.findByText("467")).toBeInTheDocument(); // Total table reads
    expect(screen.getByText("12")).toBeInTheDocument(); // Active users
    expect(screen.getByText("8")).toBeInTheDocument(); // Tables accessed
    expect(screen.getAllByText("80").length).toBeGreaterThan(0); // Sensitive column reads
    expect(screen.getAllByText("10").length).toBeGreaterThan(0); // Clear reads
    expect(screen.getAllByText("65").length).toBeGreaterThan(0); // Masked reads

    // Tables and sensitive usage cards
    expect(screen.getByText("Sensitive column usage")).toBeInTheDocument();
    expect(screen.getByText("finance.budget.PII.Name")).toBeInTheDocument();
    expect(screen.getByText("Top tables accessed")).toBeInTheDocument();
    expect(
      await screen.findByText("data_access_control.people.employees_pending_hires"),
    ).toBeInTheDocument();
    expect(screen.getByText("Top users")).toBeInTheDocument();
    expect(await screen.findByText("david.galluzzo@databricks.com")).toBeInTheDocument();
  });

  it("switches to Sync health tab and back", async () => {
    const user = userEvent.setup();
    renderApp("/data-access/analytics");

    const syncTab = await screen.findByRole("tab", { name: "Sync health" });
    await user.click(syncTab);
    expect(syncTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Run outcomes per day")).toBeInTheDocument();

    const usageTab = screen.getByRole("tab", { name: "Data usage" });
    await user.click(usageTab);
    expect(usageTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Sensitive column usage")).toBeInTheDocument();
  });

  it("inspects table drilldown when clicking table name", async () => {
    const user = userEvent.setup();
    renderApp("/data-access/analytics");

    const tableBtn = await screen.findByRole("button", {
      name: "data_access_control.people.employees_pending_hires",
    });
    await user.click(tableBtn);

    expect(
      await screen.findByRole("heading", {
        name: "Table detail: data_access_control.people.employees_pending_hires",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("first_name")).toBeInTheDocument();
    expect(screen.getByText("Columns accessed")).toBeInTheDocument();
    expect(screen.getByText("Users who accessed this table")).toBeInTheDocument();

    // Close drilldown
    await user.click(screen.getByRole("button", { name: "Close table detail" }));
    expect(
      screen.queryByRole("heading", {
        name: "Table detail: data_access_control.people.employees_pending_hires",
      }),
    ).not.toBeInTheDocument();
  });

  it("triggers refresh from the Refresh usage button", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const refreshSpy = vi.spyOn(services.usageAnalytics, "refresh");

    renderApp("/data-access/analytics", services);
    const refreshBtn = await screen.findByRole("button", { name: "Refresh usage" });
    await user.click(refreshBtn);

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Rollup started")).toBeInTheDocument();
  });
});

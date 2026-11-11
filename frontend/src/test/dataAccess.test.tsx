import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testDataAccessRun } from "./renderApp";

describe("Data access dry-run", () => {
  it("shows settings, readiness, and the latest plan on Dry-run", async () => {
    renderApp("/data-access");
    expect(await screen.findByRole("heading", { name: "Data access" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Dry-run" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Dry-run ready")).toBeInTheDocument();
    expect(screen.getByDisplayValue("data_access_control")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
    expect(screen.getByRole("table", { name: "Would grant" })).toBeInTheDocument();
    expect(screen.getByText("sandbox.finance.budget")).toBeInTheDocument();
    expect(screen.getByText("Dry-run logs")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Membership" })).toBeInTheDocument();
    expect(screen.getByLabelText("Schedule mode")).toHaveValue("dry_run");
  });

  it("shows an empty state when no run exists", async () => {
    renderApp("/data-access", fakeServices({ dataAccessRun: null }));
    expect(await screen.findByText("No dry-run has been stored yet.")).toBeInTheDocument();
    expect(await screen.findByText("No dry-run logs in the last 30 days.")).toBeInTheDocument();
  });

  it("runs a dry-run from the button", async () => {
    const user = userEvent.setup();
    renderApp("/data-access", fakeServices({ dataAccessRun: { ...testDataAccessRun, trigger: "user" } }));
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Run now" })).toBeEnabled();
    expect(screen.getByRole("table", { name: "Would grant" })).toBeInTheDocument();
  });

  it("shows Live tab with apply ready, Run apply, and apply-only logs", async () => {
    const user = userEvent.setup();
    renderApp("/data-access");
    await user.click(await screen.findByRole("tab", { name: "Live" }));
    expect(await screen.findByText("Apply ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run apply" })).toBeEnabled();
    expect(screen.getByText("Live apply logs")).toBeInTheDocument();
    expect(screen.getByText("No apply logs in the last 30 days.")).toBeInTheDocument();
  });
});

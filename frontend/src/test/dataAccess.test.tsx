import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testDataAccessRun } from "./renderApp";

describe("Data access dry-run", () => {
  it("shows settings, readiness, and the latest plan", async () => {
    renderApp("/data-access");
    expect(await screen.findByRole("heading", { name: "Data access" })).toBeInTheDocument();
    expect(await screen.findByText("Dry-run ready")).toBeInTheDocument();
    expect(screen.getByDisplayValue("data_access_control")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
    expect(screen.getByRole("table", { name: "Would grant" })).toBeInTheDocument();
    expect(screen.getByText("sandbox.finance.budget")).toBeInTheDocument();
  });

  it("shows an empty state when no run exists", async () => {
    renderApp("/data-access", fakeServices({ dataAccessRun: null }));
    expect(await screen.findByText("No dry-run has been stored yet.")).toBeInTheDocument();
  });

  it("runs a dry-run from the button", async () => {
    const user = userEvent.setup();
    renderApp("/data-access", fakeServices({ dataAccessRun: { ...testDataAccessRun, trigger: "user" } }));
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Run now" })).toBeEnabled();
    expect(screen.getByRole("table", { name: "Would grant" })).toBeInTheDocument();
  });
});

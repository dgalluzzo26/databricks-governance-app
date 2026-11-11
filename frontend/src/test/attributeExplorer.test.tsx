import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Attribute explorer", () => {
  it("analyzes a data-access path for admins", async () => {
    const user = userEvent.setup();
    renderApp("/attribute-explorer");
    expect(await screen.findByRole("heading", { name: "Attribute explorer" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /sensitivity/i })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Attribute"), "sensitivity");
    expect(await screen.findByRole("option", { name: "finance" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Value"), "finance");
    await user.click(screen.getByRole("button", { name: "Analyze" }));
    expect(await screen.findByRole("table", { name: "Holders" })).toBeInTheDocument();
    expect(screen.getByText("ana.lopez@example.com")).toBeInTheDocument();
  });

  it("is available to category approvers", async () => {
    renderApp(
      "/attribute-explorer",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: true, groups: ["finance-owners"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Attribute explorer" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Attribute explorer/i })).toBeInTheDocument();
  });

  it("redirects non-approvers", async () => {
    renderApp(
      "/attribute-explorer",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: /Access explorer/i })).toBeInTheDocument();
  });
});

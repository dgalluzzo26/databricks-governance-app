import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Approvals page", () => {
  it("shows inbox items for admins", async () => {
    renderApp("/approvals");
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(await screen.findByText("Inbox")).toBeInTheDocument();
    expect((await screen.findAllByText(/finance\.budget\.PII\.Name/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny" })).toBeInTheDocument();
  });

  it("shows Approvals nav for category approvers who are not admins", async () => {
    renderApp(
      "/approvals",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: true, groups: ["finance-owners"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Approvals/ })).toBeInTheDocument();
  });

  it("redirects non-approvers away from Approvals", async () => {
    renderApp(
      "/approvals",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: /Access explorer/i })).toBeInTheDocument();
  });

  it("shows inbox count badge in the nav", async () => {
    renderApp("/approvals");
    expect(await screen.findByLabelText("1 pending")).toBeInTheDocument();
  });
});

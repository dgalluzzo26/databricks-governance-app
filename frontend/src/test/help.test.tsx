import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  helpPageTitle,
  helpRoleFor,
  showHelpApiReference,
  visibleHelpDocs,
} from "@/pages/help/helpAudience";
import type { HelpDoc } from "@/api/types";
import { fakeServices, renderApp, testUser } from "./renderApp";

const catalog: HelpDoc[] = [
  {
    slug: "user",
    title: "User guide",
    description: "u",
    sourcePath: "docs/user-guide.md",
    audiences: ["user"],
  },
  {
    slug: "approver",
    title: "Approver guide",
    description: "a",
    sourcePath: "docs/approver-guide.md",
    audiences: ["approver"],
  },
  {
    slug: "masking-row-filters",
    title: "Masking & row filters",
    description: "m",
    sourcePath: "docs/mask-and-row-filter-flows.md",
    audiences: ["admin"],
  },
];

describe("helpAudience", () => {
  it("maps effective roles", () => {
    expect(helpRoleFor({ isAdmin: true, isApprover: true })).toBe("admin");
    expect(helpRoleFor({ isAdmin: false, isApprover: true })).toBe("approver");
    expect(helpRoleFor({ isAdmin: false, isApprover: false })).toBe("user");
  });

  it("shows only admin guides for admins", () => {
    expect(visibleHelpDocs(catalog, "admin").map((d) => d.slug)).toEqual(["masking-row-filters"]);
    expect(showHelpApiReference("admin")).toBe(true);
    expect(helpPageTitle("admin")).toBe("Admin guide");
  });

  it("shows user and approver guides for approvers", () => {
    expect(visibleHelpDocs(catalog, "approver").map((d) => d.slug)).toEqual(["user", "approver"]);
    expect(showHelpApiReference("approver")).toBe(false);
  });

  it("shows only the user guide for users", () => {
    expect(visibleHelpDocs(catalog, "user").map((d) => d.slug)).toEqual(["user"]);
    expect(showHelpApiReference("user")).toBe(false);
    expect(helpPageTitle("user")).toBe("Help");
  });
});

describe("Help", () => {
  it("shows Admin guide tabs for admins by default", async () => {
    renderApp("/help");
    expect(await screen.findByRole("heading", { name: "Admin guide" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Mental model" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Masking & row filters" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "User guide" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "API reference" })).toBeInTheDocument();
  });

  it("switches to the grants sync guide and renders its mermaid diagram", async () => {
    renderApp("/help");
    await userEvent.click(await screen.findByRole("tab", { name: "Grants sync" }));
    expect(await screen.findByRole("heading", { name: "Apply order and APIs" })).toBeInTheDocument();
    expect(await screen.findByTestId("mermaid-svg")).toBeInTheDocument();
  });

  it("filters the API reference by access level", async () => {
    renderApp("/help?tab=api");
    expect(await screen.findByText("/api/data-access/runs")).toBeInTheDocument();
    expect(screen.getByText("/api/me")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "admin");
    expect(screen.queryByText("/api/me")).not.toBeInTheDocument();
    expect(screen.getByText("/api/data-access/runs")).toBeInTheDocument();
  });

  it("shows only the user guide for ordinary users", async () => {
    renderApp(
      "/help",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Help" })).toBeInTheDocument();
    const userTab = await screen.findByRole("tab", { name: "User guide" });
    expect(userTab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Approver guide" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Masking & row filters" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "API reference" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Access explorer" })).toBeInTheDocument();
  });

  it("shows user and approver guides for category approvers", async () => {
    renderApp(
      "/help",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: true, groups: ["finance-owners"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Approver guide" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "User guide" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approver guide" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "API reference" })).not.toBeInTheDocument();
  });

  it("switches Help content when View as changes the effective role", async () => {
    const user = userEvent.setup();
    renderApp("/help");
    expect(await screen.findByRole("tab", { name: "Masking & row filters" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Ana Lopez/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "User" }));

    expect(await screen.findByRole("heading", { name: "Help" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "User guide" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Masking & row filters" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "API reference" })).not.toBeInTheDocument();
  });
});

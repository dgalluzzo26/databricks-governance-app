import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { applyViewAs, isAdminOnlyPath, viewAsLabel } from "@/app/viewAs";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("applyViewAs", () => {
  it("leaves roles unchanged in admin mode", () => {
    const user = { ...testUser, isAdmin: true, isApprover: true };
    expect(applyViewAs(user, "admin")).toEqual(user);
  });

  it("clears admin and approver for user mode", () => {
    const next = applyViewAs(testUser, "user");
    expect(next.isAdmin).toBe(false);
    expect(next.isApprover).toBe(false);
    expect(next.userName).toBe(testUser.userName);
  });

  it("clears admin and forces approver for approver mode", () => {
    const next = applyViewAs({ ...testUser, isApprover: false }, "approver");
    expect(next.isAdmin).toBe(false);
    expect(next.isApprover).toBe(true);
  });
});

describe("viewAs helpers", () => {
  it("labels modes for the banner", () => {
    expect(viewAsLabel("admin")).toBe("Admin");
    expect(viewAsLabel("user")).toBe("User");
    expect(viewAsLabel("approver")).toBe("Approver");
  });

  it("detects admin-only paths", () => {
    expect(isAdminOnlyPath("/policies")).toBe(true);
    expect(isAdminOnlyPath("/policies/abc")).toBe(true);
    expect(isAdminOnlyPath("/data-access/analytics")).toBe(true);
    expect(isAdminOnlyPath("/status")).toBe(true);
    expect(isAdminOnlyPath("/access-explorer")).toBe(false);
    expect(isAdminOnlyPath("/approvals")).toBe(false);
    expect(isAdminOnlyPath("/")).toBe(false);
  });
});

describe("view-as UI", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("shows View as in the user menu for real admins", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /Ana Lopez/ }));
    expect(screen.getByRole("group", { name: "View as" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "User" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "Approver" })).toBeInTheDocument();
  });

  it("hides View as when the real user is not an admin", async () => {
    const user = userEvent.setup();
    renderApp(
      "/",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    await user.click(await screen.findByRole("button", { name: /Ana Lopez/ }));
    expect(screen.queryByRole("group", { name: "View as" })).not.toBeInTheDocument();
  });

  it("hides admin nav when viewing as user", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /Ana Lopez/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "User" }));

    expect(await screen.findByText(/Viewing as User/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ana Lopez/ })).toHaveTextContent("User");
    expect(screen.getByRole("link", { name: "Access explorer" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Identity" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Policies" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Data access" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Approvals" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Attribute explorer" })).not.toBeInTheDocument();
  });

  it("shows Approvals and hides admin nav when viewing as approver", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /Ana Lopez/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Approver" }));

    expect(await screen.findByText(/Viewing as Approver/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ana Lopez/ })).toHaveTextContent("Approver");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: /Approvals/ })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Attribute explorer" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Data access" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Policies" })).not.toBeInTheDocument();
  });

  it("redirects off admin routes when entering user preview", async () => {
    const user = userEvent.setup();
    renderApp("/policies");
    expect(await screen.findByRole("heading", { name: /Policies/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Ana Lopez/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "User" }));

    expect(await screen.findByText(/Viewing as User/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Policies/i })).not.toBeInTheDocument();
  });

  it("restores admin nav when exiting preview from the banner", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /Ana Lopez/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "User" }));

    const banner = await screen.findByRole("status");
    expect(within(banner).getByText(/Viewing as User/)).toBeInTheDocument();
    await user.click(within(banner).getByRole("button", { name: "Exit preview" }));

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Policies" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Data access" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ana Lopez/ })).toHaveTextContent("Admin");
  });
});

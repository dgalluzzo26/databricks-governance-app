import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("role-based navigation", () => {
  it("shows Admin badge next to the user name for admins", async () => {
    renderApp("/");
    const menu = await screen.findByRole("button", { name: /Ana Lopez/ });
    expect(menu).toHaveTextContent("Admin");
    expect(screen.getByRole("link", { name: "Policies" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "App status" })).toBeInTheDocument();
  });

  it("limits nav for ordinary users to explorer and identity", async () => {
    renderApp(
      "/",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Welcome, Ana" })).toBeInTheDocument();
    const menu = screen.getByRole("button", { name: /Ana Lopez/ });
    expect(menu).toHaveTextContent("User");
    expect(screen.getByRole("link", { name: "Access explorer" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Identity" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Policies" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Row filters" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Mask rules" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "App status" })).not.toBeInTheDocument();
  });
});

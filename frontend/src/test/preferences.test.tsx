import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { renderApp } from "./renderApp";

describe("shell preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("collapses the side nav to icons with accessible labels", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Welcome, Ana" });

    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).not.toHaveAttribute("data-collapsed");
    expect(screen.getByRole("link", { name: "App status" })).toHaveTextContent("App status");

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));

    expect(nav).toHaveAttribute("data-collapsed");
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "App status" })).not.toHaveTextContent("App status");
    expect(localStorage.getItem("dga.navCollapsed")).toBe("true");

    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    expect(nav).not.toHaveAttribute("data-collapsed");
    expect(screen.getByRole("link", { name: "App status" })).toHaveTextContent("App status");
  });

  it("cycles light, dark, and system themes", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Welcome, Ana" });

    const toggle = screen.getByRole("button", { name: /theme/i });
    expect(toggle).toHaveAttribute("aria-label", "System theme (click for light)");

    await user.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("dga.theme")).toBe("light");
    expect(toggle).toHaveAttribute("aria-label", "Light theme (click for dark)");

    await user.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("dga.theme")).toBe("dark");
    expect(toggle).toHaveAttribute("aria-label", "Dark theme (click for system)");
  });
});

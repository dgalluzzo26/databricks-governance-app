import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testAppInfo } from "./renderApp";

describe("App shell", () => {
  it("shows the signed-in user and their groups on the home page", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Welcome, Ana" })).toBeInTheDocument();
    expect(screen.getAllByText("Ana Lopez").length).toBeGreaterThan(0);
    expect(screen.getByText("admins")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Home/ })).toHaveAttribute("aria-current", "page");
  });

  it("shows an error when the identity cannot be loaded", async () => {
    renderApp("/", fakeServices({ user: new Error("proxy refused") }));
    expect(await screen.findByText("Could not load your identity")).toBeInTheDocument();
    expect(screen.getByText("proxy refused")).toBeInTheDocument();
  });

  it("warns on the status page when storage is unhealthy", async () => {
    const appInfo = { ...testAppInfo, storage: { backend: "delta", healthy: false, detail: "table missing" } };
    renderApp("/status", fakeServices({ appInfo }));
    expect(await screen.findByText('Storage backend "delta" is unavailable')).toBeInTheDocument();
  });

  it("renders a not found page for unknown routes", async () => {
    renderApp("/nope");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderApp } from "./renderApp";

describe("Help", () => {
  it("renders the first guide by default", async () => {
    renderApp("/help");
    expect(await screen.findByRole("heading", { name: "Mental model" })).toBeInTheDocument();
    expect(screen.getByText(/Masks and row filters/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Masking & row filters" })).toHaveAttribute("aria-selected", "true");
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
});

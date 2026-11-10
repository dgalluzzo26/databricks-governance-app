import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderApp } from "./renderApp";

describe("Help", () => {
  it("renders the developer guide from the help API", async () => {
    renderApp("/help");
    expect(await screen.findByRole("heading", { name: "Developer guide" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Mental model" })).toBeInTheDocument();
    expect(screen.getByText(/Masks and row filters/)).toBeInTheDocument();
  });
});

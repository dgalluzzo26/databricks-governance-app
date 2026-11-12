import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderApp } from "./renderApp";

describe("Recovery export preview", () => {
  it("shows Git target settings and previews the export without writing", async () => {
    const user = userEvent.setup();
    renderApp("/recovery");
    expect(await screen.findByRole("heading", { name: "Recovery" })).toBeInTheDocument();
    expect(await screen.findByLabelText(/Repository URL/)).toBeInTheDocument();
    expect(await screen.findByDisplayValue("governance-export")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Preview export" }));
    expect(await screen.findByText("manifest.yaml")).toBeInTheDocument();
    expect(screen.getByText("masking/config.yaml")).toBeInTheDocument();
    expect(screen.getByText(/joinable salt is not exported/i)).toBeInTheDocument();
    expect(screen.getByLabelText("YAML for masking/config.yaml")).toHaveTextContent("<redacted>");
  });
});

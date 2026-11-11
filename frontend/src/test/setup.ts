import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no SVG layout, so real Mermaid cannot render; stub it with a marker SVG.
vi.mock("mermaid", () => ({
  default: {
    initialize: () => undefined,
    render: async (id: string) => ({ svg: `<svg data-testid="mermaid-svg" id="${id}"></svg>` }),
  },
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

afterEach(() => cleanup());

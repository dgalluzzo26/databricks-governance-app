import { describe, expect, it } from "vitest";
import { ApiError, HttpClient } from "@/api/httpClient";
import { initials } from "@/components/ui/Avatar";

const respond = (status: number, body?: unknown): typeof fetch =>
  (() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    )) as typeof fetch;

describe("HttpClient", () => {
  it("returns parsed JSON", async () => {
    const client = new HttpClient("/api", respond(200, { ok: true }));
    await expect(client.get("/health")).resolves.toEqual({ ok: true });
  });

  it("maps API error bodies to ApiError", async () => {
    const client = new HttpClient("/api", respond(403, { error: { code: "forbidden", message: "Admins only" } }));
    const error = await client.get("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "forbidden", message: "Admins only", isForbidden: true });
  });

  it("falls back when the error body is not JSON", async () => {
    const fetchFn = (() => Promise.resolve(new Response("<html>", { status: 502 }))) as typeof fetch;
    const error = await new HttpClient("/api", fetchFn).get("/x").catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 502, code: "http_error" });
  });
});

describe("initials", () => {
  it.each([
    ["Ana Lopez", "AL"],
    ["david.galluzzo@databricks.com", "DG"],
    ["admin", "A"],
    ["", "?"],
  ])("%s -> %s", (name, expected) => {
    expect(initials(name)).toBe(expected);
  });
});

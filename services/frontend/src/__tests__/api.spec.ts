import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("api response contract", () => {
  it("rejects a successful HTML fallback instead of treating it as API data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>frontend fallback</html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    })));
    await expect(api("/api/v1/auth/me")).rejects.toMatchObject({
      status: 502,
      message: "平台接口返回格式错误，请检查前端代理与 API 服务",
    });
  });

  it("accepts an empty successful response for delete operations", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api("/api/v1/items/1", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

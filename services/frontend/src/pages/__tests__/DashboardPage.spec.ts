import { cleanup, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../DashboardPage.vue";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({ api: apiMock }));

describe("DashboardPage statistics", () => {
  const requestedPaths: string[] = [];

  beforeEach(() => {
    requestedPaths.length = 0;
    apiMock.mockImplementation(async (path: string) => {
      requestedPaths.push(path);
      if (path === "/api/v1/dashboard/stats") {
        return {
          knowledge_bases: 3,
          documents: 25,
          agents: 2,
          processing: 4,
          succeeded: 19,
          failed: 2,
        };
      }
      throw new Error(`Unexpected API call: GET ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders accurate aggregate counts from one dashboard request", async () => {
    render(DashboardPage);

    expect(await screen.findByText("25")).toBeInTheDocument();
    expect(screen.getByText("19 份已入库")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    await waitFor(() => {
      expect(requestedPaths).toEqual(["/api/v1/dashboard/stats"]);
    });
    expect(requestedPaths.some((path) => path.startsWith("/api/v1/documents"))).toBe(false);
  });
});

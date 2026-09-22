import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../DashboardPage.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../api", () => ({ api: apiMock }));

describe("enterprise assistant workbench", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/assistant/capabilities") {
        return { knowledge_bases: [{ id: 1, code: "POLICY", name: "制度知识库", description: "公司制度" }], tools: [], agents: [], skills: [] };
      }
      if (path === "/api/v1/assistant/sessions") return [{ id: "s1", title: "新对话", message_count: 0, latest_task: null }];
      if (path === "/api/v1/assistant/sessions/s1/messages") return { id: "s1", title: "新对话", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("renders the three-column assistant workspace and quick questions", async () => {
    render(DashboardPage);
    expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "会话列表" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "可用能力" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "年假怎么申请？" })).toBeInTheDocument();
    expect(screen.getByText("制度知识库")).toBeInTheDocument();
    expect(screen.queryByText("知识处理链路")).not.toBeInTheDocument();
  });

  it("shows an explicit no-capability state", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [{ id: "s1", title: "新对话", message_count: 0, latest_task: null }];
      if (path.endsWith("/messages")) return { id: "s1", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(DashboardPage);
    expect(await screen.findByText("暂无已授权的企业能力")).toBeInTheDocument();
  });

  it("surfaces API errors without replacing the entire workspace", async () => {
    apiMock.mockRejectedValue(new Error("服务连接失败"));
    render(DashboardPage);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务连接失败");
    expect(screen.getByRole("button", { name: "新建会话" })).toBeInTheDocument();
  });

  it("opens independent conversation and capability drawers on narrow screens", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 760 });
    render(DashboardPage);
    await waitFor(() => expect(screen.getByRole("button", { name: "打开会话列表" })).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: "打开会话列表" }));
    expect(screen.getByRole("dialog", { name: "会话列表" })).toHaveClass("is-open");
    expect(document.querySelector(".assistant-capability-panel")).toHaveAttribute("aria-hidden", "true");
    expect(document.querySelector(".assistant-chat-panel")).toHaveAttribute("inert");
    await fireEvent.click(screen.getByRole("button", { name: "打开能力面板" }));
    expect(screen.getByRole("dialog", { name: "可用能力" })).toHaveClass("is-open");
    expect(document.querySelector(".assistant-session-panel")).toHaveAttribute("aria-hidden", "true");
  });
});

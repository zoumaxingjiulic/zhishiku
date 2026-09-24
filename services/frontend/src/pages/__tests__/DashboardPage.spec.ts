import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/vue";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../DashboardPage.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../api", () => ({ api: apiMock }));

describe("enterprise assistant workbench", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
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

  it("keeps the composer outside the scrollable message region and opens capabilities on demand", async () => {
    const view = render(DashboardPage);
    expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "会话列表" })).toBeInTheDocument();
    expect(view.container.querySelector(".assistant-workspace")).toHaveAttribute("data-viewport-bound", "true");
    const messages = screen.getByRole("region", { name: "对话消息" });
    expect(messages).toHaveAttribute("data-scroll-region", "true");
    expect(within(messages).queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "向企业总助手提问" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "年假怎么申请？" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "可用能力" })).not.toBeInTheDocument();
    const capabilityTrigger = screen.getByRole("button", { name: "查看可用能力" });
    await fireEvent.click(capabilityTrigger);
    const capabilityDialog = screen.getByRole("complementary", { name: "可用能力" });
    expect(capabilityDialog).toHaveClass("is-open");
    expect(screen.getByRole("complementary", { name: "会话列表" })).not.toHaveAttribute("inert");
    expect(screen.getByText("制度知识库")).toBeInTheDocument();
    expect(screen.queryByText("知识处理链路")).not.toBeInTheDocument();
    await fireEvent.keyDown(capabilityDialog, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "可用能力" })).not.toBeInTheDocument();
    expect(capabilityTrigger).toHaveFocus();
  });

  it("shows an explicit no-capability state", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [{ id: "s1", title: "新对话", message_count: 0, latest_task: null }];
      if (path.endsWith("/messages")) return { id: "s1", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(DashboardPage);
    await fireEvent.click(await screen.findByRole("button", { name: "查看可用能力" }));
    expect(await screen.findByText("暂无已授权的企业能力")).toBeInTheDocument();
  });

  it("surfaces API errors without replacing the entire workspace", async () => {
    apiMock.mockRejectedValue(new Error("服务连接失败"));
    render(DashboardPage);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务连接失败");
    expect(screen.getByRole("button", { name: "新建会话" })).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "查看可用能力" }));
    expect(screen.queryByText("系统服务正常")).not.toBeInTheDocument();
    expect(screen.getByText("服务状态未知")).toBeInTheDocument();
  });

  it("opens independent conversation and capability drawers on narrow screens", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 760 });
    render(DashboardPage);
    await waitFor(() => expect(screen.getByRole("button", { name: "打开会话列表" })).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: "打开会话列表" }));
    expect(screen.getByRole("dialog", { name: "会话列表" })).toHaveClass("is-open");
    expect(document.querySelector(".assistant-capability-panel")).toHaveAttribute("aria-hidden", "true");
    expect(document.querySelector(".assistant-chat-panel")).toHaveAttribute("inert");
    await fireEvent.click(screen.getByRole("button", { name: "关闭会话列表" }));
    await fireEvent.click(screen.getByRole("button", { name: "查看可用能力" }));
    expect(screen.getByRole("dialog", { name: "可用能力" })).toHaveClass("is-open");
    expect(document.querySelector(".assistant-session-panel")).toHaveAttribute("aria-hidden", "true");
  });

  it("uses drawer layout at 1100px so the application sidebar cannot crop capabilities", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1100 });
    render(DashboardPage);
    await waitFor(() => expect(screen.getByRole("button", { name: "查看可用能力" })).toBeInTheDocument());
    expect(document.querySelector(".assistant-workspace")).toHaveAttribute("data-layout", "compact");
    await fireEvent.click(screen.getByRole("button", { name: "查看可用能力" }));
    expect(document.querySelector(".assistant-capability-panel")).toHaveClass("is-drawer");
  });

  it("traps drawer focus, closes with Escape, and restores the opening trigger", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 760 });
    render(DashboardPage);
    const trigger = await screen.findByRole("button", { name: "打开会话列表" });
    await fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "会话列表" });
    const buttons = within(dialog).getAllByRole("button");
    await waitFor(() => expect(buttons[0]).toHaveFocus());
    buttons.at(-1)!.focus();
    await fireEvent.keyDown(buttons.at(-1)!, { key: "Tab" });
    expect(buttons[0]).toHaveFocus();
    expect(document.querySelector(".assistant-chat-panel")).toHaveAttribute("inert");
    expect(document.querySelector(".assistant-mobile-tools")).toHaveAttribute("inert");

    await fireEvent.keyDown(buttons[0], { key: "Escape" });
    expect(document.querySelector(".assistant-session-panel")).toHaveAttribute("aria-hidden", "true");
    expect(trigger).toHaveFocus();
  });

  it("does not trap Tab, Shift+Tab, or Escape in desktop complementary panels", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1360 });
    render(DashboardPage);
    const panel = await screen.findByRole("complementary", { name: "会话列表" });
    const items = within(panel).getAllByRole("button");
    const first = items[0];
    const last = items.at(-1)!;

    last.focus();
    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    last.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(false);
    expect(last).toHaveFocus();

    first.focus();
    const shiftTab = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true });
    first.dispatchEvent(shiftTab);
    expect(shiftTab.defaultPrevented).toBe(false);
    const escape = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    first.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(false);
  });

  it("keeps a failed submission in the session draft", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [{ id: "s1", title: "新对话", message_count: 0, latest_task: null }];
      if (path.endsWith("/s1/messages") && options?.method === "POST") throw new Error("提交失败");
      if (path.endsWith("/s1/messages")) return { id: "s1", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(DashboardPage);
    const input = await screen.findByRole("textbox", { name: "向企业总助手提问" });
    await waitFor(() => expect(input).not.toBeDisabled());
    await fireEvent.update(input, "不要丢失的问题");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("提交失败"));
    expect(input).toHaveValue("不要丢失的问题");
  });

  it("does not clear the active s2 draft when an s1 submission resolves", async () => {
    let resolveSubmit!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [
        { id: "s1", title: "会话 s1", message_count: 0, latest_task: null },
        { id: "s2", title: "会话 s2", message_count: 0, latest_task: null },
      ];
      if (path.endsWith("/messages") && !options) return { id: path.includes("s2") ? "s2" : "s1", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") return await new Promise(resolve => { resolveSubmit = resolve; });
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(DashboardPage);
    const input = await screen.findByRole("textbox", { name: "向企业总助手提问" });
    await waitFor(() => expect(input).not.toBeDisabled());
    await fireEvent.update(input, "s1 问题");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await fireEvent.click(screen.getByRole("button", { name: /^会话 s2/ }));
    await waitFor(() => expect(input).toHaveValue(""));
    await fireEvent.update(input, "s2 草稿");

    resolveSubmit({ id: "t1", session_id: "s1", status: "queued" });
    await Promise.resolve();
    await Promise.resolve();
    await nextTick();

    expect(input).toHaveValue("s2 草稿");
  });

  it("does not clear a newer same-session edit when the older submission resolves", async () => {
    let resolveSubmit!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [{ id: "s1", title: "会话 s1", message_count: 0, latest_task: null }];
      if (path.endsWith("/s1/messages") && !options) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") return await new Promise(resolve => { resolveSubmit = resolve; });
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(DashboardPage);
    const input = await screen.findByRole("textbox", { name: "向企业总助手提问" });
    await waitFor(() => expect(input).not.toBeDisabled());
    await fireEvent.update(input, "已提交问题");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await fireEvent.update(input, "新编辑的草稿");

    resolveSubmit({ id: "t1", session_id: "s1", status: "queued" });
    await Promise.resolve();
    await Promise.resolve();
    await nextTick();

    expect(input).toHaveValue("新编辑的草稿");
  });
});

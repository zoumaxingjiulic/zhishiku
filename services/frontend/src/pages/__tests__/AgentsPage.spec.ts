import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/vue";
import { reactive } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "../AgentsPage.vue";
import { isActiveChatTaskStatus } from "../../utils";

const apiMock = vi.hoisted(() => vi.fn());
const routeMock = reactive({
  name: "agent-chat",
  fullPath: "/agents/7/chat/running-session",
  params: { agentId: "7", sessionId: "running-session" } as Record<string, string>,
});
const routerMock = { push: vi.fn(), replace: vi.fn() };

vi.mock("../../api", () => ({ api: apiMock }));
vi.mock("vue-router", () => ({
  useRoute: () => routeMock,
  useRouter: () => routerMock,
}));

const sessions = [
  { id: "running-session", title: "运行任务", message_count: 1, last_role: "user", latest_task_status: "running" },
  { id: "failed-session", title: "失败任务", message_count: 1, last_role: "user", latest_task_status: "failed" },
  { id: "cancelled-session", title: "取消任务", message_count: 1, last_role: "user", latest_task_status: "cancelled" },
  { id: "sync-session", title: "同步失败", message_count: 1, last_role: "user", latest_task_status: null },
];

describe("AgentsPage chat task status", () => {
  beforeEach(() => {
    sessionStorage.clear();
    Object.assign(routeMock, {
      name: "agent-chat",
      fullPath: "/agents/7/chat/running-session",
      params: { agentId: "7", sessionId: "running-session" },
    });
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/agents") return [{ id: 7, name: "问答助手", launch_mode: "chat" }];
      if (path === "/api/v1/agents/7/chat/sessions") return sessions;
      if (path === "/api/v1/agents/7/chat/sessions/running-session") {
        return { id: "running-session", title: "运行任务", latest_task_status: "running", messages: [] };
      }
      if (path === "/api/v1/agents/7/chat/sessions/running-session/task") {
        return { id: "task-1", session_id: "running-session", status: "running", stage: "检索中" };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("treats only active task states as awaiting an answer", () => {
    expect(isActiveChatTaskStatus("running")).toBe(true);
    expect(isActiveChatTaskStatus("queued")).toBe(true);
    expect(isActiveChatTaskStatus("cancel_requested")).toBe(true);
    expect(isActiveChatTaskStatus("failed")).toBe(false);
    expect(isActiveChatTaskStatus("cancelled")).toBe(false);
    expect(isActiveChatTaskStatus("succeeded")).toBe(false);
    expect(isActiveChatTaskStatus("WORKER_RESTARTED")).toBe(false);
    expect(isActiveChatTaskStatus(null)).toBe(false);
  });

  it("shows and protects only the running conversation after a page reload", async () => {
    vi.useFakeTimers();
    render(AgentsPage);

    const running = await screen.findByRole("button", { name: /运行任务/ });
    const failed = screen.getByRole("button", { name: /失败任务/ });
    const cancelled = screen.getByRole("button", { name: /取消任务/ });
    const syncFailed = screen.getByRole("button", { name: /同步失败/ });
    const runningRow = running.closest(".conversation-item") as HTMLElement;
    const failedRow = failed.closest(".conversation-item") as HTMLElement;
    const cancelledRow = cancelled.closest(".conversation-item") as HTMLElement;
    const syncFailedRow = syncFailed.closest(".conversation-item") as HTMLElement;

    expect(within(runningRow).getByText("回答中…")).toBeInTheDocument();
    expect(within(failedRow).getByText("1 条消息")).toBeInTheDocument();
    expect(within(cancelledRow).getByText("1 条消息")).toBeInTheDocument();
    expect(within(syncFailedRow).getByText("1 条消息")).toBeInTheDocument();
    expect(within(runningRow).getByTitle("删除对话")).toBeDisabled();
    expect(within(failedRow).getByTitle("删除对话")).toBeEnabled();
    expect(within(cancelledRow).getByTitle("删除对话")).toBeEnabled();
    expect(within(syncFailedRow).getByTitle("删除对话")).toBeEnabled();
  });

  it("keeps list loading, empty, and failed states distinct and retries a failed load", async () => {
    Object.assign(routeMock, { name: "agents", fullPath: "/agents", params: {} });
    let requests = 0;
    apiMock.mockImplementation(async (path: string) => {
      if (path !== "/api/v1/agents") throw new Error(`Unexpected API call: ${path}`);
      requests += 1;
      if (requests === 1) throw new Error("智能体服务暂不可用");
      return [];
    });

    render(AgentsPage);

    expect(await screen.findByRole("alert")).toHaveTextContent("智能体服务暂不可用");
    expect(screen.queryByText("暂无可用智能体")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(requests).toBe(2));
    expect(await screen.findByText("暂无可用智能体")).toBeInTheDocument();
  });

  it("renders the agent list as the responsive enterprise card grid", async () => {
    Object.assign(routeMock, { name: "agents", fullPath: "/agents", params: {} });
    apiMock.mockResolvedValueOnce([
      { id: 7, name: "问答助手", launch_mode: "chat", description: "查制度" },
    ]);

    const view = render(AgentsPage);

    await screen.findByText("问答助手");
    expect(view.container.querySelector(".agents-grid")).toHaveClass("enterprise-card-grid");
  });
});

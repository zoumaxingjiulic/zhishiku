import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { defineComponent, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantChat } from "../useAssistantChat";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../api", () => ({ api: apiMock }));

const Harness = defineComponent({
  setup: () => useAssistantChat({ pollInterval: 25 }),
  template: `<div>
    <button v-for="session in sessions" :key="session.id" @click="selectSession(session.id)">打开 {{ session.id }}</button>
    <span v-for="session in sessions" :key="'title-' + session.id">{{ session.title }}</span>
    <input aria-label="会话草稿" v-model="draft"><button @click="sendMessage(draft || '查询库存')">发送</button><button @click="stopAnswer">停止</button>
    <button @click="createSession">新建测试会话</button>
    <button @click="renameSession('s1', '库存查询')">重命名</button><button @click="deleteSession('s2')">删除 s2</button>
    <p data-testid="active">{{ activeSessionId }}</p><p data-testid="waiting">{{ isAwaitingAnswer }}</p>
    <p data-testid="task-id">{{ activeTask?.id }}</p><p data-testid="ready">{{ activeSessionReady }}</p>
    <p data-testid="background-task">{{ tasksBySession.s2?.status }}</p>
    <p data-testid="summary">{{ activeTask?.execution_summary?.intent_type }}</p>
    <p v-for="message in messages" :key="message.id">{{ message.content }}</p><p role="alert">{{ error }}</p>
  </div>`,
});

function session(id: string, status: string | null = null, taskId?: string) {
  return { id, title: `会话 ${id}`, message_count: 0, latest_task_status: status,
    latest_task: taskId ? { id: taskId, session_id: id, status, stage: "处理中" } : null };
}

describe("useAssistantChat", () => {
  it.each([[409, false], [422, false], [0, false], [409, true]])("does not accept a rejected or unrelated task after submit failure %s (same key %s)", async (status, sameKey) => {
    const keys: string[] = [];
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [session("s1")];
      if (options?.method === "POST") {
        keys.push(JSON.parse(String(options.body)).request_key);
        throw Object.assign(new Error("submit failed"), { status });
      }
      if (path.endsWith("/s1/messages")) return { messages: [], latest_task: keys.length
        ? { id: "other-task", session_id: "s1", status: "running", request_key: sameKey ? keys[0] : "other-key" } : null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await fireEvent.update(screen.getByLabelText("会话草稿"), "我的请求");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("false"));
    expect(screen.getByTestId("task-id")).not.toHaveTextContent("other-task");
    expect(screen.getByLabelText("会话草稿")).toHaveValue("我的请求");
    expect(screen.queryByText("我的请求")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[1]).toBe(keys[0]);
  });

  it("recovers an already completed submission only by its own request key", async () => {
    let acceptedKey = "";
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [session("s1")];
      if (options?.method === "POST") {
        acceptedKey = JSON.parse(String(options.body)).request_key;
        throw new Error("response lost");
      }
      if (path.endsWith("/s1/messages")) return { messages: acceptedKey ? [
        { id: 1, role: "user", content: "我的请求" }, { id: 2, role: "assistant", content: "完成回答" },
      ] : [], latest_task: acceptedKey
        ? { id: "accepted", session_id: "s1", status: "succeeded", request_key: acceptedKey } : null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await fireEvent.update(screen.getByLabelText("会话草稿"), "我的请求");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("完成回答");
    expect(screen.getByTestId("task-id")).toHaveTextContent("accepted");
    expect(screen.getByTestId("waiting")).toHaveTextContent("false");
    expect(screen.getAllByText("我的请求")).toHaveLength(1);
    expect(screen.getByLabelText("会话草稿")).toHaveValue("");
  });

  beforeEach(() => { vi.useRealTimers(); apiMock.mockReset(); });
  afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); });

  it("creates and selects the first conversation when the account has none", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [];
      if (path.endsWith("/sessions") && options?.method === "POST") return session("created");
      if (path.endsWith("/created/messages")) return { ...session("created"), messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("created"));
    expect(apiMock).toHaveBeenCalledWith("/api/v1/assistant/sessions", { method: "POST" });
  });

  it("shows the user message immediately and isolates pending work by session", async () => {
    let resolveSubmit!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1"), session("s2")];
      if (path.endsWith("/messages") && !options) return { id: path.includes("s1") ? "s1" : "s2", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") return await new Promise(resolve => { resolveSubmit = resolve; });
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("s1"));
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(screen.getByText("查询库存")).toBeInTheDocument();
    expect(screen.getByTestId("waiting")).toHaveTextContent("true");
    await fireEvent.click(screen.getByRole("button", { name: "打开 s2" }));
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("s2"));
    expect(screen.getByTestId("waiting")).toHaveTextContent("false");
    resolveSubmit({ id: "t1", session_id: "s1", status: "queued", stage: "排队中" });
  });

  it("restores a running task after remount and refreshes server truth at completion", async () => {
    vi.useFakeTimers();
    let taskReads = 0;
    const active = session("s1", "running", "t1");
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [active];
      if (path.endsWith("/s1/messages")) return { ...active, messages: taskReads > 3 ? [{ id: 9, role: "assistant", content: "库存 12 件", citations: [], tool_calls: [] }] : [] };
      if (path.endsWith("/tasks/t1")) {
        taskReads += 1;
        return taskReads > 3
          ? { id: "t1", session_id: "s1", status: "succeeded", stage: "完成", execution_summary: { intent_type: "system_query", selection: { tool_ids: [3] } } }
          : { id: "t1", session_id: "s1", status: "running", stage: "查询 ERP" };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    const first = render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("true"));
    first.unmount();
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("true"));
    expect(apiMock).toHaveBeenCalledWith("/api/v1/assistant/tasks/t1");
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(screen.getByText("库存 12 件")).toBeInTheDocument());
    expect(screen.getByTestId("waiting")).toHaveTextContent("false");
    expect(screen.getByTestId("summary")).toHaveTextContent("system_query");
  });

  it("keeps a conversation when delete fails and surfaces the server message", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages")) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/sessions/s1") && options?.method === "DELETE") throw new Error("请先停止正在运行的任务再删除对话");
      throw new Error(`Unexpected API call: ${path}`);
    });
    const DeleteHarness = defineComponent({
      setup: () => useAssistantChat({ pollInterval: 25 }),
      template: `<button v-if="sessions[0]" @click="deleteSession(sessions[0].id)">删除</button><p>{{ sessions.length }}</p><p role="alert">{{ error }}</p>`,
    });
    render(DeleteHarness);
    await waitFor(() => expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请先停止正在运行的任务再删除对话");
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("renames and deletes sessions through the assistant endpoints", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1"), session("s2")];
      if (path.endsWith("/messages") && !options) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/sessions/s1") && options?.method === "PATCH") return { id: "s1", title: "库存查询" };
      if (path.endsWith("/sessions/s2") && options?.method === "DELETE") return { status: "deleted" };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByText("会话 s1")).toBeInTheDocument());

    await fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    expect(await screen.findByText("库存查询")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "删除 s2" }));
    await waitFor(() => expect(screen.queryByText("会话 s2")).not.toBeInTheDocument());
  });

  it("requests cancellation and refreshes the task and messages", async () => {
    const active = session("s1", "running", "t1");
    let cancelled = false;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [active];
      if (path.endsWith("/s1/messages")) return { ...active, messages: [], latest_task: cancelled ? { id: "t1", session_id: "s1", status: "cancelled" } : active.latest_task };
      if (path.endsWith("/tasks/t1/cancel") && options?.method === "POST") { cancelled = true; return { status: "cancel_requested" }; }
      if (path.endsWith("/tasks/t1")) return { id: "t1", session_id: "s1", status: cancelled ? "cancelled" : "running", stage: cancelled ? "已停止" : "执行中" };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("true"));

    await fireEvent.click(screen.getByRole("button", { name: "停止" }));

    await waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("false"));
    expect(apiMock).toHaveBeenCalledWith("/api/v1/assistant/tasks/t1/cancel", { method: "POST" });
    expect(apiMock).toHaveBeenCalledWith("/api/v1/assistant/tasks/t1");
  });

  it("removes an optimistic message when submission fails and reloads server truth", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages") && !options) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") throw new Error("提交失败");
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("s1"));
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));

    await fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("提交失败");
    await waitFor(() => expect(screen.queryByText("查询库存")).not.toBeInTheDocument());
  });

  it("recovers a queued task when POST succeeded but its response was lost and keeps polling", async () => {
    vi.useFakeTimers();
    let submitted = false;
    let acceptedKey = "";
    let taskReads = 0;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages") && options?.method === "POST") {
        submitted = true;
        acceptedKey = JSON.parse(String(options.body)).request_key;
        throw new Error("响应连接中断");
      }
      if (path.endsWith("/s1/messages")) return {
        id: "s1",
        messages: taskReads > 1 ? [{ id: 2, role: "assistant", content: "已恢复的回答" }] : [],
        latest_task: submitted ? { id: "t-lost", session_id: "s1", request_key: acceptedKey, status: taskReads > 1 ? "succeeded" : "queued" } : null,
      };
      if (path.endsWith("/tasks/t-lost")) {
        taskReads += 1;
        return { id: "t-lost", session_id: "s1", status: taskReads > 1 ? "succeeded" : "running" };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));

    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await vi.waitFor(() => expect(screen.getByTestId("task-id")).toHaveTextContent("t-lost"));
    await vi.advanceTimersByTimeAsync(100);

    await vi.waitFor(() => expect(screen.getByText("已恢复的回答")).toBeInTheDocument());
    expect(screen.getByTestId("waiting")).toHaveTextContent("false");
  });

  it("retries a transient poll failure instead of abandoning the active task", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const active = session("s1", "running", "t1");
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [active];
      if (path.endsWith("/s1/messages")) return { ...active, messages: reads > 2 ? [{ id: 2, role: "assistant", content: "重试成功" }] : [] };
      if (path.endsWith("/tasks/t1")) {
        reads += 1;
        if (reads === 2) throw new Error("网络短暂中断");
        return { id: "t1", session_id: "s1", status: reads > 2 ? "succeeded" : "running" };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("true"));
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("正在重试"));
    await vi.advanceTimersByTimeAsync(100);
    await vi.waitFor(() => expect(screen.getByText("重试成功")).toBeInTheDocument());
  });

  it("does not let a stale terminal history response overwrite a newer pending submission", async () => {
    vi.useFakeTimers();
    let messageReads = 0;
    let resolveOldHistory!: (value: unknown) => void;
    let resolveSubmit!: (value: unknown) => void;
    const active = session("s1", "running", "t-old");
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [active];
      if (path.endsWith("/tasks/t-old")) return { id: "t-old", session_id: "s1", status: messageReads ? "succeeded" : "running" };
      if (path.endsWith("/s1/messages") && options?.method === "POST") {
        return await new Promise(resolve => { resolveSubmit = resolve; });
      }
      if (path.endsWith("/s1/messages")) {
        messageReads += 1;
        if (messageReads === 1) return { ...active, messages: [], latest_task: active.latest_task };
        return await new Promise(resolve => { resolveOldHistory = resolve; });
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(screen.getByTestId("waiting")).toHaveTextContent("false"));

    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(screen.getByTestId("task-id")).toHaveTextContent("pending-");
    resolveOldHistory({ id: "s1", messages: [{ id: 8, role: "assistant", content: "旧回答" }], latest_task: { id: "t-old", session_id: "s1", status: "succeeded" } });
    await Promise.resolve();

    expect(screen.getByTestId("waiting")).toHaveTextContent("true");
    expect(screen.getByTestId("task-id")).toHaveTextContent("pending-");
    resolveSubmit({ id: "t-new", session_id: "s1", status: "queued" });
  });

  it("reuses the idempotency key when a failed question is retried", async () => {
    const requestKeys: string[] = [];
    let attempts = 0;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages") && options?.method === "POST") {
        requestKeys.push(JSON.parse(String(options.body)).request_key);
        attempts += 1;
        if (attempts === 1) throw new Error("提交失败");
        return { id: "t1", session_id: "s1", status: "queued" };
      }
      if (path.endsWith("/s1/messages")) return { id: "s1", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));

    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("提交失败"));
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(requestKeys).toHaveLength(2);
    expect(requestKeys[1]).toBe(requestKeys[0]);
  });

  it("blocks submission until the selected session history is initialized", async () => {
    let resolveHistory!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages") && !options) return await new Promise(resolve => { resolveHistory = resolve; });
      if (options?.method === "POST") throw new Error("submission must remain blocked");
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("s1"));

    await fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.queryByText("查询库存")).not.toBeInTheDocument();
    expect(apiMock.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
    resolveHistory({ id: "s1", messages: [], latest_task: null });
  });

  it("retries when terminal message refresh fails and then synchronizes session truth", async () => {
    vi.useFakeTimers();
    let taskReads = 0;
    let messageReads = 0;
    const active = session("s1", "running", "t1");
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return taskReads > 1
        ? [{ ...session("s1", "succeeded", "t1"), title: "服务端标题", message_count: 2 }]
        : [active];
      if (path.endsWith("/tasks/t1")) {
        taskReads += 1;
        return { id: "t1", session_id: "s1", status: taskReads > 1 ? "succeeded" : "running" };
      }
      if (path.endsWith("/s1/messages")) {
        messageReads += 1;
        if (messageReads === 2) throw new Error("消息暂时不可用");
        return { id: "s1", messages: messageReads > 2 ? [{ id: 2, role: "assistant", content: "终态回答" }] : [], latest_task: { id: "t1", session_id: "s1", status: taskReads > 1 ? "succeeded" : "running" } };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("同步回答失败，正在重试"));
    await vi.advanceTimersByTimeAsync(100);

    await vi.waitFor(() => expect(screen.getByText("终态回答")).toBeInTheDocument());
    expect(screen.getByText("服务端标题")).toBeInTheDocument();
  });

  it("retries a failed restore for a running background session", async () => {
    vi.useFakeTimers();
    let backgroundReads = 0;
    const s2 = session("s2", "running", "t2");
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions")) return [session("s1"), s2];
      if (path.endsWith("/tasks/t2")) {
        backgroundReads += 1;
        if (backgroundReads === 1) throw new Error("恢复连接中断");
        return { id: "t2", session_id: "s2", status: "succeeded", stage: "后台完成" };
      }
      if (path.endsWith("/s1/messages")) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/s2/messages")) return { id: "s2", messages: [], latest_task: { id: "t2", session_id: "s2", status: "succeeded" } };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("s1"));

    await vi.advanceTimersByTimeAsync(100);

    await vi.waitFor(() => expect(backgroundReads).toBeGreaterThanOrEqual(2));
    expect(screen.getByTestId("background-task")).toHaveTextContent("succeeded");
  });

  it("clears only the submitted session draft when another session becomes active", async () => {
    let resolveSubmit!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1"), session("s2")];
      if (path.endsWith("/messages") && !options) return { id: path.includes("s2") ? "s2" : "s1", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") return await new Promise(resolve => { resolveSubmit = resolve; });
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    const input = screen.getByRole("textbox", { name: "会话草稿" });
    await fireEvent.update(input, "s1 问题");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await fireEvent.click(screen.getByRole("button", { name: "打开 s2" }));
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await fireEvent.update(input, "s2 草稿");

    resolveSubmit({ id: "t1", session_id: "s1", status: "queued" });
    await Promise.resolve();

    expect(input).toHaveValue("s2 草稿");
    await fireEvent.click(screen.getByRole("button", { name: "打开 s1" }));
    expect(input).toHaveValue("");
  });

  it("does not clear a same-session draft edited while its submission is pending", async () => {
    let resolveSubmit!: (value: unknown) => void;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) return [session("s1")];
      if (path.endsWith("/s1/messages") && !options) return { id: "s1", messages: [], latest_task: null };
      if (path.endsWith("/s1/messages") && options?.method === "POST") return await new Promise(resolve => { resolveSubmit = resolve; });
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    const input = screen.getByRole("textbox", { name: "会话草稿" });
    await fireEvent.update(input, "已提交问题");
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await fireEvent.update(input, "继续编辑的草稿");

    resolveSubmit({ id: "t1", session_id: "s1", status: "queued" });
    await Promise.resolve();

    expect(input).toHaveValue("继续编辑的草稿");
  });

  it("discards an old terminal session list after create and rename mutations", async () => {
    vi.useFakeTimers();
    let sessionReads = 0;
    let taskReads = 0;
    let refreshStarted = false;
    let resolveOldList!: (value: unknown) => void;
    const initial = session("s1", "running", "t1");
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/capabilities")) return { knowledge_bases: [], tools: [], agents: [], skills: [] };
      if (path.endsWith("/sessions") && !options) {
        sessionReads += 1;
        if (sessionReads === 1) return [initial];
        refreshStarted = true;
        return await new Promise(resolve => { resolveOldList = resolve; });
      }
      if (path.endsWith("/sessions") && options?.method === "POST") return session("s2");
      if (path.endsWith("/sessions/s1") && options?.method === "PATCH") return { id: "s1", title: "库存查询" };
      if (path.endsWith("/tasks/t1")) {
        taskReads += 1;
        return { id: "t1", session_id: "s1", status: taskReads > 1 ? "succeeded" : "running" };
      }
      if (path.endsWith("/s1/messages")) return { id: "s1", messages: [], latest_task: { id: "t1", session_id: "s1", status: taskReads > 1 ? "succeeded" : "running" } };
      if (path.endsWith("/s2/messages")) return { id: "s2", messages: [], latest_task: null };
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(Harness);
    await vi.waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("true"));
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(refreshStarted).toBe(true));

    await fireEvent.click(screen.getByRole("button", { name: "新建测试会话" }));
    await fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    await vi.waitFor(() => expect(screen.getByText("库存查询")).toBeInTheDocument());
    resolveOldList([session("s1", "running", "t1")]);
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    await nextTick();

    expect(screen.getByText("会话 s2")).toBeInTheDocument();
    expect(screen.getByText("库存查询")).toBeInTheDocument();
    expect(screen.getByTestId("active")).toHaveTextContent("s2");
  });
});

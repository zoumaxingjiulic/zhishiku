import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import StudioPage from "../StudioPage.vue";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({ api: apiMock }));

const legacyWorkflow = {
  id: 7,
  code: "LEGACY_WORKFLOW",
  name: "历史工作流",
  launch_mode: "workflow",
  config_version: 3,
  status: "active",
  steps: [],
};

describe("StudioPage launch modes", () => {
  beforeEach(() => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/studio/agents") return [legacyWorkflow];
      if (path === "/api/v1/users") return [];
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/model-gateway/profiles") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      if (path === "/api/v1/studio/agents/7/revisions") {
        return [{
          version: 2,
          created_at: "2026-09-20T09:00:00Z",
          snapshot: { ...legacyWorkflow, name: "历史问答版本", launch_mode: "chat" },
        }];
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("only offers chat mode when creating an agent", async () => {
    render(StudioPage);

    await screen.findByText("历史工作流");
    await fireEvent.click(screen.getByRole("button", { name: "新建智能体" }));

    const launchMode = screen.getByLabelText("运行方式") as HTMLSelectElement;
    expect(launchMode.value).toBe("chat");
    expect(screen.queryByRole("option", { name: "步骤式工作流" })).not.toBeInTheDocument();
  });

  it("shows existing workflow agents as read-only compatibility records", async () => {
    render(StudioPage);

    expect(await screen.findByText("历史兼容模式，不能新建或扩展")).toBeInTheDocument();
    const launchMode = screen.getByLabelText("运行方式") as HTMLSelectElement;
    expect(launchMode.value).toBe("workflow");
    expect(launchMode).toBeDisabled();
    expect(screen.getByRole("option", { name: "兼容模式" })).toBeInTheDocument();
  });

  it("keeps a workflow record read-only after loading a chat history snapshot", async () => {
    render(StudioPage);

    await screen.findByText("历史兼容模式，不能新建或扩展");
    await fireEvent.click(screen.getByRole("button", { name: "历史版本" }));
    await fireEvent.click(await screen.findByRole("button", { name: "载入此版本" }));

    const publish = screen.getByRole("button", { name: "发布配置" });
    const form = publish.closest("form");
    expect(form).not.toBeNull();
    await fireEvent.submit(form!);

    expect(publish).toBeDisabled();
    expect(apiMock).not.toHaveBeenCalledWith(
      "/api/v1/studio/agents/7",
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it("shows an explicit empty state while keeping chat creation available", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/studio/agents") return [];
      if (path === "/api/v1/users") return [];
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/model-gateway/profiles") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(StudioPage);

    expect(await screen.findByText("暂无智能体")).toBeInTheDocument();
    await fireEvent.click(screen.getAllByRole("button", { name: "新建智能体" })[1]);
    expect(screen.getByLabelText("运行方式")).toHaveValue("chat");
    expect(screen.queryByRole("option", { name: /工作流|兼容模式/ })).not.toBeInTheDocument();
  });
  it("distributes an agent to selected users instead of departments", async () => {
    let savedBody: any = null;
    apiMock.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path === "/api/v1/studio/agents" && !options.method) return [];
      if (path === "/api/v1/users") return [{ id: 8, username: "employee", display_name: "员工" }];
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/model-gateway/profiles") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      if (path === "/api/v1/studio/agents" && options.method === "POST") {
        savedBody = JSON.parse(String(options.body));
        return { ...savedBody, id: 9, config_version: 1 };
      }
      throw new Error(`Unexpected API call: ${options.method ?? "GET"} ${path}`);
    });

    render(StudioPage);
    expect(await screen.findByText("暂无智能体")).toBeInTheDocument();
    await fireEvent.click(screen.getAllByRole("button", { name: "新建智能体" })[1]);
    await fireEvent.update(screen.getByLabelText("名称"), "人资助手");
    await fireEvent.update(screen.getByLabelText("唯一编码"), "HR_AGENT");
    await fireEvent.click(screen.getByLabelText("员工（employee）"));
    await fireEvent.submit(screen.getByRole("button", { name: "发布配置" }).closest("form")!);

    await waitFor(() => expect(savedBody?.user_ids).toEqual([8]));
    expect(savedBody).not.toHaveProperty("department_ids");
  });

  it("sanitizes legacy revision fields before publishing", async () => {
    let savedBody: any = null;
    const chatAgent = { ...legacyWorkflow, launch_mode: "chat", user_ids: [8], knowledge_base_ids: [], tool_ids: [] };
    apiMock.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path === "/api/v1/studio/agents" && !options.method) return [chatAgent];
      if (path === "/api/v1/users") return [{ id: 8, username: "employee", display_name: "员工" }];
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/model-gateway/profiles") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      if (path === "/api/v1/studio/agents/7/revisions") return [{
        version: 1,
        created_at: "2026-09-20T09:00:00Z",
        snapshot: { ...chatAgent, user_ids: undefined, department_ids: [2], name: "旧版" },
      }];
      if (path === "/api/v1/studio/agents/7" && options.method === "PUT") {
        savedBody = JSON.parse(String(options.body));
        return { ...savedBody, id: 7 };
      }
      throw new Error(`Unexpected API call: ${options.method ?? "GET"} ${path}`);
    });

    render(StudioPage);
    await screen.findByDisplayValue("历史工作流");
    await fireEvent.click(screen.getByRole("button", { name: "历史版本" }));
    await fireEvent.click(await screen.findByRole("button", { name: "载入此版本" }));
    await fireEvent.submit(screen.getByRole("button", { name: "发布配置" }).closest("form")!);

    await waitFor(() => expect(savedBody?.user_ids).toEqual([8]));
    expect(savedBody).not.toHaveProperty("department_ids");
  });
});

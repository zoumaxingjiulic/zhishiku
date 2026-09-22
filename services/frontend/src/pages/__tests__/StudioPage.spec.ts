import { cleanup, fireEvent, render, screen } from "@testing-library/vue";
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
      if (path === "/api/v1/departments") return [];
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
      if (path === "/api/v1/departments") return [];
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
});

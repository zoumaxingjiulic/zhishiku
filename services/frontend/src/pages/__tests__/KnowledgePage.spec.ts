import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import KnowledgePage from "../KnowledgePage.vue";
import ConnectionsPage from "../ConnectionsPage.vue";
import ModelGatewayPage from "../ModelGatewayPage.vue";
import PromptTemplatesPage from "../PromptTemplatesPage.vue";
import AgentRequestsPage from "../AgentRequestsPage.vue";
import AuditPage from "../AuditPage.vue";
import ObservabilityPage from "../ObservabilityPage.vue";
import StudioPage from "../StudioPage.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../api", () => ({ api: apiMock }));

const user = { is_platform_admin: true, departments: [{ id: 7, name: "运营部" }], department_ids: [7] };
const knowledgeBase = {
  id: 3,
  name: "制度库",
  code: "POLICY",
  permission: "manage",
  owner_department_id: 7,
  owner_department_name: "运营部",
  document_count: 0,
};

describe("KnowledgePage enterprise layout", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("places the knowledge selector before the directory, upload, and document workspace", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/knowledge-bases") return [knowledgeBase];
      if (path === "/api/v1/folders?knowledge_base_id=3") return [];
      if (path === "/api/v1/documents?knowledge_base_id=3&folder_id=0") return [];
      throw new Error(`Unexpected API call: ${path}`);
    });

    const view = render(KnowledgePage, { props: { user } });

    expect(await screen.findAllByText("制度库")).toHaveLength(2);
    const selector = view.container.querySelector("[data-section='knowledge-selector']");
    const workspace = view.container.querySelector("[data-section='knowledge-workspace']");
    expect(selector).not.toBeNull();
    expect(workspace).not.toBeNull();
    expect(selector!.compareDocumentPosition(workspace!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "上传并入库" })).toHaveClass("no-wrap");
  });

  it("keeps knowledge loading, empty, and failed states distinct and retryable", async () => {
    let reads = 0;
    apiMock.mockImplementation(async (path: string) => {
      if (path !== "/api/v1/knowledge-bases") throw new Error(`Unexpected API call: ${path}`);
      reads += 1;
      if (reads === 1) throw new Error("知识库加载失败");
      return [];
    });

    render(KnowledgePage, { props: { user } });

    expect(await screen.findByRole("alert")).toHaveTextContent("知识库加载失败");
    expect(screen.queryByText("暂无可访问知识库")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(reads).toBe(2));
    expect(await screen.findByText("暂无可访问知识库")).toBeInTheDocument();
  });
});

describe("management page async states", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it.each([
    ["系统连接", ConnectionsPage, { user }],
    ["大模型网关", ModelGatewayPage, {}],
    ["提示词模板", PromptTemplatesPage, {}],
    ["智能体申请", AgentRequestsPage, { user }],
    ["审计日志", AuditPage, {}],
    ["运行监控", ObservabilityPage, {}],
    ["智能体工作室", StudioPage, {}],
  ])("%s does not disguise a load error as an empty state", async (_name, component, props) => {
    apiMock.mockRejectedValue(new Error("平台数据加载失败"));
    render(component as any, { props });

    expect(await screen.findByRole("alert")).toHaveTextContent("平台数据加载失败");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

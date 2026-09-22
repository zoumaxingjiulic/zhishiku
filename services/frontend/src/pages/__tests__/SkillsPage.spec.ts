import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App.vue";
import { setAuthUser } from "../../auth";
import SkillsPage from "../SkillsPage.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../api", () => ({ api: apiMock }));

const skill = {
  id: 7,
  code: "INVENTORY_LOOKUP",
  name: "库存查询",
  description: "查询库存",
  instruction: "仅使用绑定能力",
  input_schema: { type: "object", properties: {}, additionalProperties: false },
  trigger_examples: ["查库存"],
  knowledge_base_ids: [],
  tool_ids: [],
  agent_ids: [],
  department_ids: [],
  status: "active",
  version: 1,
};

function appRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "workbench", component: { template: "<div>工作台</div>" } },
      { path: "/skills", name: "skills", component: { template: "<div>能力配置页</div>" } },
    ],
  });
}

describe("SkillsPage", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows 能力配置 navigation only to platform administrators", async () => {
    const router = appRouter();
    setAuthUser({ id: 1, display_name: "管理员", departments: [], is_platform_admin: true });
    await router.push("/");
    await router.isReady();
    const view = render(App, { global: { plugins: [router] } });
    expect(screen.getByRole("link", { name: /能力配置/ })).toBeInTheDocument();

    view.unmount();
    setAuthUser({ id: 8, display_name: "员工", departments: [], is_platform_admin: false });
    render(App, { global: { plugins: [router] } });
    expect(screen.queryByRole("link", { name: /能力配置/ })).not.toBeInTheDocument();
  });

  it("refreshes the skill list after a successful save", async () => {
    let listReads = 0;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/api/v1/admin/skills" && !options) {
        listReads += 1;
        return listReads === 1 ? [] : [skill];
      }
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      if (path === "/api/v1/studio/agents") return [];
      if (path === "/api/v1/departments") return [];
      if (path === "/api/v1/admin/skills" && options?.method === "POST") return skill;
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(SkillsPage);
    await screen.findByText("暂无 Skill");
    await fireEvent.click(screen.getByRole("button", { name: "新建 Skill" }));
    await fireEvent.update(screen.getByLabelText("编码"), "INVENTORY_LOOKUP");
    await fireEvent.update(screen.getByLabelText("名称"), "库存查询");
    await fireEvent.update(screen.getByLabelText("执行说明"), "仅使用绑定能力");
    await fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(listReads).toBe(2));
    expect(await screen.findByText("库存查询")).toBeInTheDocument();
  });

  it("shows a disabled historical tool binding and lets the administrator remove it", async () => {
    const historicalSkill = { ...skill, tool_ids: [3] };
    let savedBody: any = null;
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/api/v1/admin/skills" && !options) return [historicalSkill];
      if (path === "/api/v1/admin/skills/7" && !options) return historicalSkill;
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/connectors") return {
        items: [{
          id: 1,
          name: "旧 ERP",
          status: "disabled",
          tools: [{ id: 3, title: "库存工具", status: "active", annotations: { readOnlyHint: true } }],
        }],
      };
      if (path === "/api/v1/studio/agents") return [];
      if (path === "/api/v1/departments") return [];
      if (path === "/api/v1/admin/skills/7" && options?.method === "PUT") {
        savedBody = JSON.parse(String(options.body));
        return { ...historicalSkill, tool_ids: [], version: 2 };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(SkillsPage);
    await fireEvent.click(await screen.findByRole("button", { name: /库存查询/ }));
    const historicalBinding = await screen.findByRole("checkbox", {
      name: "旧 ERP / 库存工具（已停用/不可用）",
    });
    expect(historicalBinding).toBeChecked();

    await fireEvent.click(historicalBinding);
    await fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(savedBody?.tool_ids).toEqual([]));
    expect(screen.queryByText("旧 ERP / 库存工具（已停用/不可用）")).not.toBeInTheDocument();
  });
});

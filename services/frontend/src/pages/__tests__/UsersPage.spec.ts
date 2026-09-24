import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UsersPage from "../UsersPage.vue";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({ api: apiMock }));

const department = {
  id: 7,
  code: "OPERATIONS",
  name: "运营部",
  parent_id: 1,
  status: 1,
  created_at: "2026-09-18T08:00:00Z",
};

const existingUser = {
  id: 21,
  username: "existing.user",
  display_name: "现有账号",
  email: "existing@example.com",
  status: 1,
  last_login_at: "2026-09-18T09:00:00Z",
  created_at: "2026-09-17T08:00:00Z",
  department_id: department.id,
  department_code: department.code,
  department_name: department.name,
};

const createdUserResponse = {
  id: 22,
  username: "new.user",
  display_name: "新账号",
  status: 1,
  temporary_password: "FIRST-TEMP-PASSWORD",
};

describe("UsersPage temporary password notice", () => {
  beforeEach(() => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    apiMock.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path === "/api/v1/departments" && !options.method) return [department];
      if (path === "/api/v1/users" && !options.method) return [existingUser];
      if (path === "/api/v1/knowledge-bases" && !options.method) return [];
      if (path === "/api/v1/connectors" && !options.method) return { items: [] };
      if (path === `/api/v1/departments/${department.id}/knowledge-base-grants` && !options.method) return [];
      if (path === `/api/v1/users/${existingUser.id}/permissions` && !options.method) return { ...existingUser, department_inherited_knowledge_base_grants: [], direct_knowledge_base_grants: [], effective_knowledge_base_grants: [], direct_tools: [] };
      if (path === "/api/v1/users" && options.method === "POST") return createdUserResponse;
      if (path === `/api/v1/users/${existingUser.id}` && options.method === "PUT") return { status: "ok" };
      if (path === `/api/v1/users/${existingUser.id}/reset-password` && options.method === "POST") {
        return { status: "ok", temporary_password: "SECOND-TEMP-PASSWORD" };
      }
      throw new Error(`Unexpected API call: ${options.method ?? "GET"} ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("discards a created user's password before editing and only shows a newly reset password", async () => {
    render(UsersPage);

    await screen.findByText(existingUser.display_name);
    await fireEvent.click(screen.getByRole("button", { name: "创建账号" }));
    await fireEvent.update(screen.getByLabelText("用户名"), "new.user");
    await fireEvent.update(screen.getByLabelText("姓名"), "新账号");
    await fireEvent.update(screen.getByLabelText("邮箱（可选）"), "new@example.com");
    await fireEvent.submit(screen.getByRole("button", { name: "保存" }).closest("form")!);

    expect(await screen.findByText("FIRST-TEMP-PASSWORD")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "我已保存，关闭" }));

    await fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    await fireEvent.submit(screen.getByRole("button", { name: "保存" }).closest("form")!);

    await waitFor(() => expect(screen.queryByText("编辑企业账号")).not.toBeInTheDocument());
    expect(screen.queryByText("临时密码（仅显示一次）")).not.toBeInTheDocument();
    expect(screen.queryByText("FIRST-TEMP-PASSWORD")).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    expect(await screen.findByText("SECOND-TEMP-PASSWORD")).toBeInTheDocument();
    expect(screen.queryByText("FIRST-TEMP-PASSWORD")).not.toBeInTheDocument();
  });

  it("keeps the password selectable and explains manual copying when Clipboard is unavailable", async () => {
    render(UsersPage);
    await screen.findByText(existingUser.display_name);
    await fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    const password = await screen.findByText("SECOND-TEMP-PASSWORD");
    expect(password).toHaveAttribute("tabindex", "0");
    await fireEvent.click(screen.getByRole("button", { name: "复制密码" }));

    expect(await screen.findByRole("status")).toHaveTextContent("请手工选中临时密码并复制");
  });

  it("shows a recoverable error instead of an empty user table", async () => {
    apiMock.mockRejectedValueOnce(new Error("用户目录加载失败"));
    render(UsersPage);

    expect(await screen.findByRole("alert")).toHaveTextContent("用户目录加载失败");
    expect(screen.queryByText("暂无账号")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
  it("loads inherited permissions and submits direct knowledge-base and MCP-tool grants", async () => {
    const kb = { id: 3, code: "TECH", name: "技术知识库" };
    const tool = { id: 11, tool_name: "stock", title: "库存查询", annotations: { readOnlyHint: true } };
    apiMock.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path === "/api/v1/departments") return [department];
      if (path === "/api/v1/users" && !options.method) return [existingUser];
      if (path === "/api/v1/knowledge-bases") return [kb];
      if (path === "/api/v1/connectors") return { items: [{ id: 4, name: "ERP", tools: [tool] }] };
      if (path === `/api/v1/users/${existingUser.id}/permissions`) return {
        ...existingUser,
        department_inherited_knowledge_base_grants: [{ knowledge_base_id: 1, code: "HR", name: "人资知识库", permission: "manage", sources: ["department"] }],
        direct_knowledge_base_grants: [{ knowledge_base_id: 3, code: "TECH", name: "技术知识库", permission: "read", sources: ["direct"] }],
        effective_knowledge_base_grants: [],
        direct_tools: [{ id: 11, connector_id: 4, connector_code: "ERP", connector_name: "ERP", tool_name: "stock", title: "库存查询" }],
      };
      if (path === `/api/v1/users/${existingUser.id}` && options.method === "PUT") return { status: "ok" };
      throw new Error(`Unexpected API call: ${options.method ?? "GET"} ${path}`);
    });

    render(UsersPage);
    await screen.findByText(existingUser.display_name);
    await fireEvent.click(screen.getByRole("button", { name: "编辑" }));

    expect(await screen.findByText("人资知识库 · 可管理")).toBeInTheDocument();
    expect(screen.getByLabelText("技术知识库权限")).toHaveValue("read");
    expect(screen.getByText("ERP / 库存查询")).toBeInTheDocument();
    await fireEvent.submit(screen.getByRole("button", { name: "保存" }).closest("form")!);

    await waitFor(() => {
      const call = apiMock.mock.calls.find(([path, options]) => path === `/api/v1/users/${existingUser.id}` && options?.method === "PUT");
      expect(call).toBeTruthy();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.knowledge_base_grants).toEqual([{ knowledge_base_id: 3, permission: "read" }]);
      expect(body.tool_ids).toEqual([11]);
    });
  });

  it("previews inherited knowledge bases when the selected department changes", async () => {
    const otherDepartment = { ...department, id: 8, code: "TECH", name: "技术部" };
    apiMock.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path === "/api/v1/departments") return [department, otherDepartment];
      if (path === "/api/v1/users" && !options.method) return [existingUser];
      if (path === "/api/v1/knowledge-bases") return [];
      if (path === "/api/v1/connectors") return { items: [] };
      if (path === `/api/v1/users/${existingUser.id}/permissions`) return {
        ...existingUser,
        department_inherited_knowledge_base_grants: [], direct_knowledge_base_grants: [],
        effective_knowledge_base_grants: [], direct_tools: [],
      };
      if (path === `/api/v1/departments/${department.id}/knowledge-base-grants`) return [];
      if (path === `/api/v1/departments/${otherDepartment.id}/knowledge-base-grants`) return [{
        knowledge_base_id: 3, code: "TECH", name: "技术知识库", permission: "manage", sources: ["department"],
      }];
      throw new Error(`Unexpected API call: ${options.method ?? "GET"} ${path}`);
    });

    render(UsersPage);
    await screen.findByText(existingUser.display_name);
    await fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    await fireEvent.update(screen.getByLabelText("所属部门"), String(otherDepartment.id));

    expect(await screen.findByText("技术知识库 · 可管理")).toBeInTheDocument();
  });
});

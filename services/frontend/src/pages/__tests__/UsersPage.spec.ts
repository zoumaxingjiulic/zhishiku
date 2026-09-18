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
    await fireEvent.click(screen.getByRole("button", { name: "＋ 创建账号" }));
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
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";
import App from "../../../App.vue";
import { setAuthUser } from "../../../auth";
import AppModal from "../../AppModal.vue";
import BaseBadge from "../BaseBadge.vue";
import BaseButton from "../BaseButton.vue";
import BaseCard from "../BaseCard.vue";
import BaseEmptyState from "../BaseEmptyState.vue";
import BaseIcon from "../BaseIcon.vue";
import BaseSkeleton from "../BaseSkeleton.vue";

vi.mock("../../../api", () => ({ api: vi.fn() }));

const originalViewportWidth = window.innerWidth;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: originalViewportWidth });
});

describe("base components", () => {
  it("prevents repeated actions while a button is loading or disabled", async () => {
    const view = render(BaseButton, {
      props: { loading: true, loadingText: "正在保存" },
      slots: { default: "保存" },
    });

    const button = screen.getByRole("button", { name: "正在保存" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await fireEvent.click(button);
    expect(view.emitted("click")).toBeUndefined();

    await view.rerender({ loading: false, disabled: true, variant: "danger" });
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存" })).toHaveClass("base-button--danger");
  });

  it("defaults to a non-submitting button and preserves an explicit submit type", () => {
    const view = render(BaseButton, { slots: { default: "普通操作" } });
    expect(screen.getByRole("button", { name: "普通操作" })).toHaveAttribute("type", "button");
    view.unmount();

    render(BaseButton, { props: { type: "submit" }, slots: { default: "保存表单" } });
    expect(screen.getByRole("button", { name: "保存表单" })).toHaveAttribute("type", "submit");
  });

  it.each(["add", "arrow-left", "chevron-up", "chevron-down", "download", "folder-open", "refresh", "send"])(
    "renders the %s icon from the local SVG map",
    (name) => {
      const view = render(BaseIcon, { props: { name, title: name } });
      const path = view.container.querySelector("path");
      expect(path?.getAttribute("d")).not.toContain("m12 3 1.3 3.7");
    },
  );

  it("renders card, badge, empty-state and icon content through stable slots", () => {
    render(defineComponent({
      components: { BaseBadge, BaseCard, BaseEmptyState, BaseIcon },
      template: `
        <BaseCard><h2>项目资料</h2></BaseCard>
        <BaseBadge tone="success">已启用</BaseBadge>
        <BaseEmptyState title="暂无资料" description="上传第一份资料开始使用">
          <template #icon><BaseIcon name="folder" title="资料夹" /></template>
          <template #action><button>上传资料</button></template>
        </BaseEmptyState>
      `,
    }));

    expect(screen.getByText("项目资料")).toBeInTheDocument();
    expect(screen.getByText("已启用")).toHaveClass("base-badge--success");
    expect(screen.getByRole("heading", { name: "暂无资料" })).toBeInTheDocument();
    expect(screen.getByText("上传第一份资料开始使用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传资料" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "资料夹" })).toBeInTheDocument();
  });

  it("gives skeleton loading content an accessible label", () => {
    render(BaseSkeleton, { props: { label: "正在加载知识库" } });

    expect(screen.getByRole("status", { name: "正在加载知识库" })).toBeInTheDocument();
  });
});

describe("AppModal", () => {
  it("moves focus into the dialog, closes on Escape and restores trigger focus", async () => {
    const Harness = defineComponent({
      components: { AppModal },
      setup() {
        const open = ref(false);
        return { open };
      },
      template: `
        <button @click="open = true">打开设置</button>
        <AppModal v-if="open" title="连接设置" @close="open = false">
          <button>确认连接</button>
        </AppModal>
      `,
    });
    render(Harness);

    const trigger = screen.getByRole("button", { name: "打开设置" });
    trigger.focus();
    await fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "连接设置" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭连接设置" })).toHaveFocus();

    await fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});

function shellRouter() {
  const page = { template: "<div>页面内容</div>" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/workbench", name: "workbench", component: page, meta: { title: "工作台", section: "workbench" } },
      { path: "/knowledge-bases", name: "knowledge", component: page, meta: { title: "知识库", section: "knowledge" } },
      { path: "/agents", name: "agents", component: page, meta: { title: "智能体", section: "agents" } },
      { path: "/prompt-templates", name: "prompts", component: page, meta: { title: "提示词模板", section: "prompts" } },
      { path: "/agent-requests", name: "agent-requests", component: page, meta: { title: "智能体申请", section: "agent-requests" } },
      { path: "/connections", name: "connections", component: page, meta: { title: "系统连接", section: "connections" } },
      { path: "/studio", name: "studio", component: page, meta: { title: "智能体工作室", section: "studio" } },
      { path: "/skills", name: "skills", component: page, meta: { title: "能力配置", section: "skills" } },
      { path: "/model-gateway", name: "model-gateway", component: page, meta: { title: "大模型网关", section: "model-gateway" } },
      { path: "/users", name: "users", component: page, meta: { title: "用户与部门", section: "users" } },
      { path: "/observability", name: "observability", component: page, meta: { title: "运行监控", section: "observability" } },
      { path: "/audit", name: "audit", component: page, meta: { title: "审计日志", section: "audit" } },
    ],
  });
}

describe("application shell", () => {
  it("moves focus into the narrow drawer, traps Tab and restores focus after explicit close", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 800 });
    setAuthUser({ id: 1, display_name: "管理员", departments: [], is_platform_admin: true });
    const router = shellRouter();
    await router.push("/workbench");
    await router.isReady();
    render(App, { global: { plugins: [router] } });

    const toggle = screen.getByRole("button", { name: "打开导航菜单" });
    const sidebar = document.getElementById("app-sidebar");
    const main = toggle.closest("main");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    toggle.focus();
    await fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(sidebar).toHaveAttribute("aria-hidden", "false");
    expect(main).toHaveAttribute("inert");
    expect(screen.getByRole("link", { name: /工作台/ })).toHaveFocus();
    expect(screen.getByRole("navigation", { name: "平台导航" })).toBeInTheDocument();
    expect(screen.getByText("工作")).toBeInTheDocument();
    expect(screen.getByText("资源")).toBeInTheDocument();
    expect(screen.getByText("管理")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /能力配置/ })).toBeInTheDocument();

    const close = screen.getByRole("button", { name: "关闭导航侧栏" });
    const lastLink = screen.getByRole("link", { name: /审计日志/ });
    lastLink.focus();
    await fireEvent.keyDown(sidebar!, { key: "Tab" });
    expect(close).toHaveFocus();
    await fireEvent.keyDown(sidebar!, { key: "Tab", shiftKey: true });
    expect(lastLink).toHaveFocus();

    await fireEvent.keyDown(sidebar!, { key: "Escape" });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "false"));
    expect(toggle).toHaveFocus();
    expect(main).not.toHaveAttribute("inert");

    await fireEvent.click(toggle);
    await fireEvent.click(screen.getByRole("button", { name: "关闭导航侧栏" }));
    await waitFor(() => expect(toggle).toHaveFocus());
  });

  it("does not trap boundary Tab navigation in the desktop sidebar", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    setAuthUser({ id: 1, display_name: "管理员", departments: [], is_platform_admin: true });
    const router = shellRouter();
    await router.push("/workbench");
    await router.isReady();
    render(App, { global: { plugins: [router] } });

    const sidebar = document.getElementById("app-sidebar")!;
    const firstLink = screen.getByRole("link", { name: "工作台" });
    const lastLink = screen.getByRole("link", { name: "审计日志" });

    lastLink.focus();
    const forwardTab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    sidebar.dispatchEvent(forwardTab);
    expect(forwardTab.defaultPrevented).toBe(false);
    expect(lastLink).toHaveFocus();

    firstLink.focus();
    const backwardTab = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true });
    sidebar.dispatchEvent(backwardTab);
    expect(backwardTab.defaultPrevented).toBe(false);
    expect(firstLink).toHaveFocus();
  });

  it("closes the drawer after routing and focuses the new page heading", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 800 });
    setAuthUser({ id: 1, display_name: "管理员", departments: [], is_platform_admin: true });
    const router = shellRouter();
    await router.push("/workbench");
    await router.isReady();
    render(App, { global: { plugins: [router] } });

    const toggle = screen.getByRole("button", { name: "打开导航菜单" });
    await fireEvent.click(toggle);
    await fireEvent.click(screen.getByRole("link", { name: /知识库/ }));
    await waitFor(() => expect(router.currentRoute.value.name).toBe("knowledge"));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById("app-sidebar")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("heading", { level: 1, name: "知识库" })).toHaveFocus();
    expect(toggle).not.toHaveFocus();
  });

  it("keeps management navigation hidden from regular employees", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    setAuthUser({ id: 8, display_name: "员工", departments: [], is_platform_admin: false });
    const router = shellRouter();
    await router.push("/workbench");
    await router.isReady();
    render(App, { global: { plugins: [router] } });

    for (const label of ["工作台", "智能体", "智能体申请", "知识库", "提示词模板", "系统连接"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByText("管理")).not.toBeInTheDocument();
    for (const label of ["智能体工作室", "能力配置", "大模型网关", "用户与部门", "运行监控", "审计日志"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
  });
});

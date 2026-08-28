import { createRouter, createWebHistory } from "vue-router";
import { authUser, ensureAuth } from "./auth";
import DashboardPage from "./pages/DashboardPage.vue";
import KnowledgePage from "./pages/KnowledgePage.vue";
import AgentsPage from "./pages/AgentsPage.vue";
import ConnectionsPage from "./pages/ConnectionsPage.vue";
import ModelGatewayPage from "./pages/ModelGatewayPage.vue";
import PromptTemplatesPage from "./pages/PromptTemplatesPage.vue";
import AgentRequestsPage from "./pages/AgentRequestsPage.vue";
import UsersPage from "./pages/UsersPage.vue";
import AuditPage from "./pages/AuditPage.vue";
import LoginRoute from "./pages/LoginRoute.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/workbench" },
    { path: "/login", name: "login", component: LoginRoute, meta: { public: true, title: "登录", section: "login" } },
    { path: "/workbench", name: "workbench", component: DashboardPage, meta: { title: "工作台", subtitle: "企业知识服务运行概览", section: "workbench", navigate: true } },
    { path: "/knowledge-bases", name: "knowledge", component: KnowledgePage, meta: { title: "知识库", subtitle: "资料、文件夹与索引生命周期管理", section: "knowledge", passUser: true, toast: true } },
    { path: "/agents", name: "agents", component: AgentsPage, meta: { title: "智能体", subtitle: "统一使用问答、流程与数据处理智能体", section: "agents", toast: true } },
    { path: "/agents/:agentId(\\d+)", name: "agent", component: AgentsPage, meta: { title: "智能体", subtitle: "统一使用问答、流程与数据处理智能体", section: "agents", toast: true } },
    { path: "/agents/:agentId(\\d+)/chat/:sessionId", name: "agent-chat", component: AgentsPage, meta: { title: "智能体对话", subtitle: "对话与企业知识检索", section: "agents", toast: true } },
    { path: "/prompt-templates", name: "prompts", component: PromptTemplatesPage, meta: { title: "提示词模板", subtitle: "保存和复用我的常用提示词", section: "prompts", toast: true } },
    { path: "/agent-requests", name: "agent-requests", component: AgentRequestsPage, meta: { title: "智能体申请", subtitle: "提交业务需求并跟踪评审进度", section: "agent-requests", passUser: true, toast: true } },
    { path: "/connections", name: "connections", component: ConnectionsPage, meta: { title: "系统连接", subtitle: "ERP、PLM 与 MOM 系统接入预留", section: "connections" } },
    { path: "/model-gateway", name: "model-gateway", component: ModelGatewayPage, meta: { title: "大模型网关", subtitle: "统一管理模型厂商、凭据与智能体模型路由", section: "model-gateway", admin: true, toast: true } },
    { path: "/users", name: "users", component: UsersPage, meta: { title: "用户与部门", subtitle: "账号、部门和数据权限管理", section: "users", admin: true, toast: true } },
    { path: "/audit", name: "audit", component: AuditPage, meta: { title: "审计日志", subtitle: "关键操作的安全审计记录", section: "audit", admin: true } },
    { path: "/:pathMatch(.*)*", redirect: "/workbench" },
  ],
});

router.beforeEach(async to => {
  await ensureAuth();
  if (!authUser.value && !to.meta.public) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (authUser.value && to.name === "login") {
    const redirect = typeof to.query.redirect === "string" && to.query.redirect.startsWith("/") && !to.query.redirect.startsWith("//")
      ? to.query.redirect : "/workbench";
    return redirect;
  }
  if (to.meta.admin && !authUser.value?.is_platform_admin) return { name: "workbench" };
});

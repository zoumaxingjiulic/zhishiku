<script setup lang="ts">
import { computed, onBeforeUnmount, onErrorCaptured, onMounted, reactive, ref } from "vue";
import { api } from "./api";
import AppModal from "./components/AppModal.vue";
import DashboardPage from "./pages/DashboardPage.vue";
import KnowledgePage from "./pages/KnowledgePage.vue";
import AgentsPage from "./pages/AgentsPage.vue";
import ConnectionsPage from "./pages/ConnectionsPage.vue";
import UsersPage from "./pages/UsersPage.vue";
import AuditPage from "./pages/AuditPage.vue";

type PageKey = "home" | "knowledge" | "agents" | "connections" | "users" | "audit";

const user = ref<any>(null);
const checkingSession = ref(true);
const page = ref<PageKey>("home");
const loginBusy = ref(false);
const loginError = ref("");
const passwordModal = ref(false);
const toastState = reactive({ message: "", bad: false, visible: false });
const loginForm = reactive({ username: "", password: "" });
const passwordForm = reactive({ current_password: "", new_password: "", confirmation: "" });
let toastTimer: number | undefined;

const pageMeta: Record<PageKey, { title: string; subtitle: string }> = {
  home: { title: "工作台", subtitle: "企业知识服务运行概览" },
  knowledge: { title: "知识库", subtitle: "资料、文件夹与索引生命周期管理" },
  agents: { title: "智能体", subtitle: "选择有权访问的企业知识问答智能体" },
  connections: { title: "系统连接", subtitle: "ERP、PLM 与 MOM 系统接入预留" },
  users: { title: "用户与部门", subtitle: "账号、部门和数据权限管理" },
  audit: { title: "审计日志", subtitle: "关键操作的安全审计记录" },
};
const currentMeta = computed(() => pageMeta[page.value]);
const isAdmin = computed(() => Boolean(user.value?.is_platform_admin));
const departments = computed(() => user.value?.departments?.map((item: any) => item.name).join("、") || "未分配部门");
const avatar = computed(() => user.value?.display_name?.trim()?.charAt(0) || "企");

const navItems = computed(() => [
  { key: "home", icon: "⌂", label: "工作台" },
  { key: "knowledge", icon: "▤", label: "知识库" },
  { key: "agents", icon: "✦", label: "智能体" },
  { key: "connections", icon: "⌘", label: "系统连接" },
  ...(isAdmin.value ? [
    { key: "users", icon: "♟", label: "用户与部门" },
    { key: "audit", icon: "◷", label: "审计日志" },
  ] : []),
] as Array<{ key: PageKey; icon: string; label: string }>);

function toast(message: string, bad = false) {
  toastState.message = message;
  toastState.bad = bad;
  toastState.visible = true;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toastState.visible = false; }, 3500);
}

function showLogin() {
  user.value = null;
  page.value = "home";
  passwordModal.value = false;
  loginForm.password = "";
}

async function restoreSession() {
  try { user.value = await api("/api/v1/auth/me"); }
  catch { showLogin(); }
  finally { checkingSession.value = false; }
}

async function login() {
  loginBusy.value = true;
  loginError.value = "";
  try {
    const result = await api<any>("/api/v1/auth/login", { method: "POST", body: JSON.stringify(loginForm) });
    user.value = result.user;
    loginForm.password = "";
    page.value = "home";
  } catch (error: any) { loginError.value = error.message; }
  finally { loginBusy.value = false; }
}

async function logout() {
  try { await api("/api/v1/auth/logout", { method: "POST" }); }
  finally { showLogin(); }
}

function openPasswordModal() {
  Object.assign(passwordForm, { current_password: "", new_password: "", confirmation: "" });
  passwordModal.value = true;
}

async function changePassword() {
  if (passwordForm.new_password !== passwordForm.confirmation) {
    toast("两次输入的新密码不一致", true);
    return;
  }
  try {
    await api("/api/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: passwordForm.current_password, new_password: passwordForm.new_password }),
    });
    passwordModal.value = false;
    toast("密码已修改，请重新登录");
    window.setTimeout(showLogin, 700);
  } catch (error: any) { toast(error.message, true); }
}

function navigate(target: string) {
  if (target === "users" || target === "audit") {
    if (!isAdmin.value) return;
  }
  page.value = target as PageKey;
}

function onAuthExpired() {
  showLogin();
  loginError.value = "登录已失效，请重新登录";
}

onErrorCaptured((error: any) => {
  toast(error?.message || "页面加载失败", true);
  return false;
});
onMounted(() => {
  window.addEventListener("auth-expired", onAuthExpired);
  restoreSession();
});
onBeforeUnmount(() => {
  window.removeEventListener("auth-expired", onAuthExpired);
  if (toastTimer) window.clearTimeout(toastTimer);
});
</script>

<template>
  <div v-if="checkingSession" class="boot-screen"><div class="brand-mark">智</div><p>正在连接企业知识平台…</p></div>

  <main v-else-if="!user" class="login-shell">
    <section class="login-brand">
      <div class="brand-mark">智</div>
      <div><h1>企业智能体平台</h1><p>让制度、技术资料与业务知识安全地服务每个部门</p></div>
    </section>
    <form class="login-card" @submit.prevent="login">
      <div><span class="eyebrow">INTERNAL PLATFORM</span><h2>欢迎登录</h2><p class="muted">使用管理员创建的企业账号</p></div>
      <label>用户名<input v-model.trim="loginForm.username" name="username" autocomplete="username" required autofocus></label>
      <label>密码<input v-model="loginForm.password" name="password" type="password" autocomplete="current-password" required></label>
      <button class="primary wide" :disabled="loginBusy">{{ loginBusy ? "登录中…" : "登录平台" }}</button>
      <p class="error" role="alert">{{ loginError }}</p>
    </form>
  </main>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="logo"><div class="brand-mark small">智</div><div><strong>企业智能体</strong><small>KNOWLEDGE OS</small></div></div>
      <nav aria-label="平台导航">
        <button v-for="item in navItems" :key="item.key" :class="{ active: page === item.key }" @click="navigate(item.key)"><span>{{ item.icon }}</span>{{ item.label }}</button>
      </nav>
      <div class="sidebar-foot"><span class="health-dot"></span>服务运行正常</div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div><h1>{{ currentMeta.title }}</h1><p>{{ currentMeta.subtitle }}</p></div>
        <div class="user-area">
          <div class="avatar">{{ avatar }}</div>
          <div><strong>{{ user.display_name }}</strong><small>{{ departments }}</small></div>
          <button class="ghost" @click="openPasswordModal">修改密码</button>
          <button class="ghost" @click="logout">退出</button>
        </div>
      </header>
      <section class="content">
        <DashboardPage v-if="page === 'home'" @navigate="navigate" />
        <KnowledgePage v-else-if="page === 'knowledge'" :user="user" @toast="toast" />
        <AgentsPage v-else-if="page === 'agents'" @toast="toast" />
        <ConnectionsPage v-else-if="page === 'connections'" />
        <UsersPage v-else-if="page === 'users' && isAdmin" @toast="toast" />
        <AuditPage v-else-if="page === 'audit' && isAdmin" />
      </section>
    </main>

    <AppModal v-if="passwordModal" title="修改登录密码" @close="passwordModal=false">
      <form class="form-stack" @submit.prevent="changePassword">
        <label>当前密码<input v-model="passwordForm.current_password" type="password" autocomplete="current-password" required></label>
        <label>新密码<input v-model="passwordForm.new_password" type="password" autocomplete="new-password" minlength="10" required><small class="muted">至少 10 位，并包含大小写字母、数字和特殊字符。</small></label>
        <label>确认新密码<input v-model="passwordForm.confirmation" type="password" autocomplete="new-password" minlength="10" required></label>
        <button class="primary">保存新密码</button>
      </form>
    </AppModal>
  </div>

  <div class="toast" :class="{ show: toastState.visible, bad: toastState.bad }" role="status">{{ toastState.message }}</div>
</template>

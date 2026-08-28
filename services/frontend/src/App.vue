<script setup lang="ts">
import { computed, onBeforeUnmount, onErrorCaptured, reactive, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api } from "./api";
import { authReady, authUser, clearAuthUser, setAuthUser } from "./auth";
import AppModal from "./components/AppModal.vue";

const route = useRoute();
const router = useRouter();
const loginBusy = ref(false);
const loginError = ref("");
const passwordModal = ref(false);
const toastState = reactive({ message: "", bad: false, visible: false });
const loginForm = reactive({ username: "", password: "" });
const passwordForm = reactive({ current_password: "", new_password: "", confirmation: "" });
let toastTimer: number | undefined;

const currentMeta = computed(() => ({
  title: String(route.meta.title || "企业智能体平台"),
  subtitle: String(route.meta.subtitle || ""),
}));
const isAdmin = computed(() => Boolean(authUser.value?.is_platform_admin));
const departments = computed(() => authUser.value?.departments?.map((item: any) => item.name).join("、") || "未分配部门");
const avatar = computed(() => authUser.value?.display_name?.trim()?.charAt(0) || "企");
const viewProps = computed(() => route.meta.passUser ? { user: authUser.value } : {});
const viewListeners = computed(() => ({
  ...(route.meta.toast ? { toast } : {}),
  ...(route.meta.navigate ? { navigate } : {}),
}));

const navItems = computed(() => [
  { section: "workbench", to: { name: "workbench" }, icon: "⌂", label: "工作台" },
  { section: "knowledge", to: { name: "knowledge" }, icon: "▤", label: "知识库" },
  { section: "agents", to: { name: "agents" }, icon: "✦", label: "智能体" },
  { section: "prompts", to: { name: "prompts" }, icon: "⌑", label: "提示词模板" },
  { section: "agent-requests", to: { name: "agent-requests" }, icon: "✎", label: "智能体申请" },
  { section: "connections", to: { name: "connections" }, icon: "⌘", label: "系统连接" },
  ...(isAdmin.value ? [
    { section: "model-gateway", to: { name: "model-gateway" }, icon: "◈", label: "大模型网关" },
    { section: "users", to: { name: "users" }, icon: "♟", label: "用户与部门" },
    { section: "audit", to: { name: "audit" }, icon: "◷", label: "审计日志" },
  ] : []),
]);

function toast(message: string, bad = false) {
  toastState.message = message;
  toastState.bad = bad;
  toastState.visible = true;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toastState.visible = false; }, 3500);
}

async function login() {
  loginBusy.value = true;
  loginError.value = "";
  try {
    const result = await api<any>("/api/v1/auth/login", { method: "POST", body: JSON.stringify(loginForm) });
    setAuthUser(result.user);
    loginForm.password = "";
    const redirect = typeof route.query.redirect === "string" && route.query.redirect.startsWith("/") && !route.query.redirect.startsWith("//")
      ? route.query.redirect
      : "/workbench";
    await router.replace(redirect);
  } catch (error: any) { loginError.value = error.message; }
  finally { loginBusy.value = false; }
}

async function logout() {
  try { await api("/api/v1/auth/logout", { method: "POST" }); }
  finally {
    clearAuthUser();
    passwordModal.value = false;
    loginForm.password = "";
    await router.replace({ name: "login" });
  }
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
    window.setTimeout(async () => {
      clearAuthUser();
      await router.replace({ name: "login" });
    }, 700);
  } catch (error: any) { toast(error.message, true); }
}

function navigate(target: string) {
  const mapping: Record<string, string> = {
    home: "workbench", knowledge: "knowledge", agents: "agents", prompts: "prompts",
    agentRequests: "agent-requests", connections: "connections", modelGateway: "model-gateway",
    users: "users", audit: "audit",
  };
  if ((target === "users" || target === "audit" || target === "modelGateway") && !isAdmin.value) return;
  router.push({ name: mapping[target] || target });
}

async function onAuthExpired() {
  if (!authUser.value) return;
  clearAuthUser();
  passwordModal.value = false;
  loginError.value = "登录已失效，请重新登录";
  await router.replace({ name: "login", query: { redirect: route.fullPath } });
}

onErrorCaptured((error: any) => {
  toast(error?.message || "页面加载失败", true);
  return false;
});
window.addEventListener("auth-expired", onAuthExpired);
onBeforeUnmount(() => {
  window.removeEventListener("auth-expired", onAuthExpired);
  if (toastTimer) window.clearTimeout(toastTimer);
});
</script>

<template>
  <div v-if="!authReady" class="boot-screen"><div class="brand-mark">智</div><p>正在连接企业知识平台…</p></div>

  <main v-else-if="!authUser" class="login-shell">
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
        <RouterLink v-for="item in navItems" :key="item.section" :to="item.to" :class="{ active: route.meta.section === item.section }"><span>{{ item.icon }}</span>{{ item.label }}</RouterLink>
      </nav>
      <div class="sidebar-foot"><span class="health-dot"></span>服务运行正常</div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div><h1>{{ currentMeta.title }}</h1><p>{{ currentMeta.subtitle }}</p></div>
        <div class="user-area">
          <div class="avatar">{{ avatar }}</div>
          <div><strong>{{ authUser.display_name }}</strong><small>{{ departments }}</small></div>
          <button class="ghost" @click="openPasswordModal">修改密码</button>
          <button class="ghost" @click="logout">退出</button>
        </div>
      </header>
      <section class="content">
        <RouterView v-slot="{ Component }">
          <component :is="Component" v-bind="viewProps" v-on="viewListeners" />
        </RouterView>
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

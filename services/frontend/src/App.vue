<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onErrorCaptured, reactive, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api } from "./api";
import { authReady, authUser, clearAuthUser, setAuthUser } from "./auth";
import AppModal from "./components/AppModal.vue";
import BaseIcon from "./components/base/BaseIcon.vue";

const route = useRoute();
const router = useRouter();
const loginBusy = ref(false);
const loginError = ref("");
const passwordModal = ref(false);
const sidebarOpen = ref(false);
const isNarrow = ref(window.innerWidth < 1024);
const sidebar = ref<HTMLElement | null>(null);
const drawerToggle = ref<HTMLButtonElement | null>(null);
const pageTitle = ref<HTMLElement | null>(null);
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

const navGroups = computed(() => [
  {
    label: "工作",
    items: [
      { section: "workbench", to: { name: "workbench" }, icon: "home", label: "工作台" },
      { section: "agents", to: { name: "agents" }, icon: "bot", label: "智能体" },
      { section: "agent-requests", to: { name: "agent-requests" }, icon: "request", label: "智能体申请" },
    ],
  },
  {
    label: "资源",
    items: [
      { section: "knowledge", to: { name: "knowledge" }, icon: "database", label: "知识库" },
      { section: "prompts", to: { name: "prompts" }, icon: "file", label: "提示词模板" },
      { section: "connections", to: { name: "connections" }, icon: "link", label: "系统连接" },
    ],
  },
  ...(isAdmin.value ? [{
    label: "管理",
    items: [
      { section: "studio", to: { name: "studio" }, icon: "studio", label: "智能体工作室" },
      { section: "skills", to: { name: "skills" }, icon: "sparkles", label: "能力配置" },
      { section: "model-gateway", to: { name: "model-gateway" }, icon: "cpu", label: "大模型网关" },
      { section: "users", to: { name: "users" }, icon: "users", label: "用户与部门" },
      { section: "observability", to: { name: "observability" }, icon: "activity", label: "运行监控" },
      { section: "audit", to: { name: "audit" }, icon: "shield", label: "审计日志" },
    ],
  }] : []),
]);

function syncLayout() {
  isNarrow.value = window.innerWidth < 1024;
  if (!isNarrow.value) sidebarOpen.value = false;
}

const sidebarFocusableSelector = [
  "button:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function sidebarFocusableElements() {
  return sidebar.value
    ? Array.from(sidebar.value.querySelectorAll<HTMLElement>(sidebarFocusableSelector))
    : [];
}

async function openSidebar() {
  sidebarOpen.value = true;
  await nextTick();
  const currentLink = sidebar.value?.querySelector<HTMLElement>("a[aria-current='page']");
  (currentLink ?? sidebarFocusableElements()[0])?.focus();
}

async function closeSidebar(restoreToggleFocus: boolean) {
  const wasOpen = sidebarOpen.value;
  sidebarOpen.value = false;
  await nextTick();
  if (wasOpen && restoreToggleFocus) drawerToggle.value?.focus();
}

async function toggleSidebar() {
  if (sidebarOpen.value) await closeSidebar(true);
  else await openSidebar();
}

function handleSidebarKeydown(event: KeyboardEvent) {
  if (!isNarrow.value || !sidebarOpen.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    void closeSidebar(true);
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = sidebarFocusableElements();
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

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
    agentRequests: "agent-requests", connections: "connections", skills: "skills", modelGateway: "model-gateway",
    studio: "studio", users: "users", observability: "observability", audit: "audit",
  };
  if ((target === "studio" || target === "users" || target === "audit" || target === "observability" || target === "modelGateway" || target === "skills") && !isAdmin.value) return;
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
window.addEventListener("resize", syncLayout);
watch(() => route.fullPath, async () => {
  if (!isNarrow.value || !sidebarOpen.value) return;
  sidebarOpen.value = false;
  await nextTick();
  pageTitle.value?.focus();
});
onBeforeUnmount(() => {
  window.removeEventListener("auth-expired", onAuthExpired);
  window.removeEventListener("resize", syncLayout);
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
    <div
      v-if="isNarrow && sidebarOpen"
      class="sidebar-backdrop"
      aria-hidden="true"
      @click="closeSidebar(true)"
    />
    <aside
      id="app-sidebar"
      ref="sidebar"
      class="sidebar"
      :class="{ open: !isNarrow || sidebarOpen }"
      :aria-hidden="isNarrow ? !sidebarOpen : undefined"
      :inert="isNarrow && !sidebarOpen ? true : undefined"
      @keydown="handleSidebarKeydown"
    >
      <div class="logo">
        <div class="brand-mark small">智</div>
        <div><strong>企业智能体</strong><small>KNOWLEDGE OS</small></div>
        <button
          v-if="isNarrow"
          class="sidebar-close"
          type="button"
          aria-label="关闭导航侧栏"
          data-sidebar-close
          @click="closeSidebar(true)"
        >
          <BaseIcon name="close" :size="20" />
        </button>
      </div>
      <nav aria-label="平台导航">
        <section v-for="group in navGroups" :key="group.label" class="nav-section">
          <h2>{{ group.label }}</h2>
          <RouterLink
            v-for="item in group.items"
            :key="item.section"
            :to="item.to"
            :class="{ active: route.meta.section === item.section }"
            :aria-current="route.meta.section === item.section ? 'page' : undefined"
          >
            <BaseIcon :name="item.icon" :size="18" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </section>
      </nav>
      <div class="sidebar-foot"><span class="health-dot"></span>服务运行正常</div>
    </aside>

    <main class="main" :inert="isNarrow && sidebarOpen ? true : undefined">
      <header class="topbar">
        <button
          v-if="isNarrow"
          ref="drawerToggle"
          class="drawer-toggle"
          type="button"
          :aria-label="sidebarOpen ? '关闭导航菜单' : '打开导航菜单'"
          :aria-expanded="sidebarOpen"
          aria-controls="app-sidebar"
          @click="toggleSidebar"
        >
          <BaseIcon :name="sidebarOpen ? 'close' : 'menu'" :size="22" />
        </button>
        <div><h1 ref="pageTitle" tabindex="-1">{{ currentMeta.title }}</h1><p>{{ currentMeta.subtitle }}</p></div>
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

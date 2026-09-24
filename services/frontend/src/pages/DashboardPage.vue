<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import AssistantComposer from "../components/assistant/AssistantComposer.vue";
import AssistantConversationList from "../components/assistant/AssistantConversationList.vue";
import AssistantMessages from "../components/assistant/AssistantMessages.vue";
import CapabilityPanel from "../components/assistant/CapabilityPanel.vue";
import BaseIcon from "../components/base/BaseIcon.vue";
import BaseSkeleton from "../components/base/BaseSkeleton.vue";
import { useAssistantChat } from "../composables/useAssistantChat";

const {
  sessions, activeSessionId, capabilities, loading, error, capabilityError,
  messages, activeTask, isAwaitingAnswer, activeSessionReady, draft, createSession, selectSession,
  renameSession, deleteSession, sendMessage, stopAnswer,
} = useAssistantChat();

const isNarrow = ref(window.innerWidth < 1360);
const sessionsOpen = ref(false);
const capabilitiesOpen = ref(false);
const sessionPanel = ref<HTMLElement | null>(null);
const capabilityPanel = ref<HTMLElement | null>(null);
let drawerTrigger: HTMLElement | null = null;
const activeSession = computed(() => sessions.value.find(item => item.id === activeSessionId.value));
const drawerOpen = computed(() => isNarrow.value && (sessionsOpen.value || capabilitiesOpen.value));

function syncViewport() {
  isNarrow.value = window.innerWidth < 1360;
  if (!isNarrow.value) {
    sessionsOpen.value = false;
  }
}

function focusable(panel: HTMLElement | null) {
  return panel ? [...panel.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')]
    .filter(item => item.tabIndex >= 0) : [];
}

async function focusDrawer(panel: HTMLElement | null) {
  await nextTick();
  focusable(panel)[0]?.focus();
}

function openSessions(event: MouseEvent) {
  drawerTrigger = event.currentTarget as HTMLElement;
  sessionsOpen.value = true;
  capabilitiesOpen.value = false;
  void focusDrawer(sessionPanel.value);
}

function openCapabilities(event: MouseEvent) {
  drawerTrigger = event.currentTarget as HTMLElement;
  capabilitiesOpen.value = true;
  sessionsOpen.value = false;
  void focusDrawer(capabilityPanel.value);
}

async function closeDrawer() {
  sessionsOpen.value = false;
  capabilitiesOpen.value = false;
  await nextTick();
  drawerTrigger?.focus();
  drawerTrigger = null;
}

function trapDrawerFocus(event: KeyboardEvent, panel: HTMLElement | null, isOpen: boolean, isModal: boolean) {
  if (!isOpen) return;
  if (event.key === "Escape") {
    event.preventDefault();
    void closeDrawer();
    return;
  }
  if (!isModal) return;
  if (event.key !== "Tab") return;
  const items = focusable(panel);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function handleSend(question: string) {
  await sendMessage(question);
}

async function chooseSession(sessionId: string) {
  await selectSession(sessionId);
  if (isNarrow.value) await closeDrawer();
}

window.addEventListener("resize", syncViewport);
onBeforeUnmount(() => window.removeEventListener("resize", syncViewport));
</script>

<template>
  <section class="assistant-workspace" aria-label="企业总助手工作区" :data-layout="isNarrow ? 'compact' : 'wide'" data-viewport-bound="true">
    <div class="assistant-mobile-tools" :inert="drawerOpen ? true : undefined">
      <button type="button" aria-label="打开会话列表" :aria-expanded="sessionsOpen" @click="openSessions"><BaseIcon name="menu" :size="18" />会话</button>
    </div>

    <div v-if="drawerOpen" class="assistant-drawer-backdrop" aria-hidden="true" @click="closeDrawer" />
    <aside
      ref="sessionPanel"
      class="assistant-session-panel"
      :class="{ 'is-drawer': isNarrow, 'is-open': !isNarrow || sessionsOpen }"
      :role="isNarrow ? 'dialog' : 'complementary'"
      aria-label="会话列表"
      :aria-modal="isNarrow ? 'true' : undefined"
      :aria-hidden="isNarrow ? !sessionsOpen : undefined"
      :inert="isNarrow && !sessionsOpen ? true : undefined"
      @keydown="trapDrawerFocus($event, sessionPanel, sessionsOpen, isNarrow)"
    >
      <button v-if="isNarrow" class="assistant-drawer-close" type="button" aria-label="关闭会话列表" @click="closeDrawer"><BaseIcon name="close" /></button>
      <AssistantConversationList
        :sessions="sessions"
        :active-session-id="activeSessionId"
        @create="createSession"
        @select="chooseSession"
        @rename="renameSession"
        @delete="deleteSession"
      />
    </aside>

    <main class="assistant-chat-panel" :inert="drawerOpen ? true : undefined">
      <header class="assistant-chat-heading">
        <div><span class="assistant-eyebrow">ENTERPRISE ASSISTANT</span><h2>{{ activeSession?.title || "企业总助手" }}</h2><p>统一访问企业知识、只读系统工具与专业智能体</p></div>
        <div class="assistant-chat-actions">
          <span v-if="activeTask" class="assistant-status">{{ activeTask.stage || activeTask.status }}</span>
          <button type="button" class="assistant-capability-trigger" aria-label="查看可用能力" :aria-expanded="capabilitiesOpen" @click="openCapabilities">
            <BaseIcon name="sparkles" :size="17" />
            <span>可用能力</span>
          </button>
        </div>
      </header>
      <div v-if="error" class="assistant-alert" role="alert">{{ error }}</div>
      <div class="assistant-message-region" role="region" aria-label="对话消息" data-scroll-region="true">
        <div v-if="loading" class="assistant-loading" role="status" aria-label="正在加载企业总助手"><BaseSkeleton height="68px" /><BaseSkeleton height="68px" width="72%" /></div>
        <AssistantMessages v-else :messages="messages" :task="activeTask" :capabilities="capabilities" />
      </div>
      <AssistantComposer v-model="draft" :awaiting="isAwaitingAnswer" :disabled="!activeSessionId || !activeSessionReady" @send="handleSend" @stop="stopAnswer" />
    </main>

    <aside
      ref="capabilityPanel"
      class="assistant-capability-panel is-drawer"
      :class="{ 'is-open': capabilitiesOpen }"
      :role="isNarrow ? 'dialog' : 'complementary'"
      aria-label="可用能力"
      :aria-modal="isNarrow ? 'true' : undefined"
      :aria-hidden="!capabilitiesOpen"
      :inert="!capabilitiesOpen ? true : undefined"
      @keydown="trapDrawerFocus($event, capabilityPanel, capabilitiesOpen, isNarrow)"
    >
      <button class="assistant-drawer-close" type="button" aria-label="关闭能力面板" @click="closeDrawer"><BaseIcon name="close" /></button>
      <CapabilityPanel :capabilities="capabilities" :task="activeTask" :loading="loading" :error="capabilityError" />
    </aside>
  </section>
</template>

<style scoped>
.assistant-workspace { position: relative; height: calc(100dvh - 176px); min-height: 0; display: grid; grid-template-columns: minmax(210px, 260px) minmax(0, 1fr); overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.assistant-session-panel, .assistant-capability-panel { min-width: 0; padding: var(--space-5); background: var(--color-surface); }
.assistant-session-panel { min-height: 0; overflow: hidden; border-right: 1px solid var(--color-border); }.assistant-capability-panel { border-left: 1px solid var(--color-border); }
.assistant-chat-panel { min-width: 0; min-height: 0; overflow: hidden; display: flex; flex-direction: column; background: var(--color-surface); }
.assistant-chat-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: var(--space-4) var(--space-6); border-bottom: 1px solid var(--color-border); }.assistant-chat-heading h2 { margin: 3px 0; font-size: 1.125rem; }.assistant-chat-heading p { margin: 0; color: var(--color-text-muted); font-size: .75rem; }.assistant-eyebrow { color: var(--color-primary); font-size: .625rem; font-weight: 800; letter-spacing: .12em; }.assistant-status { flex: 0 0 auto; border-radius: 999px; padding: var(--space-1) var(--space-3); background: var(--color-warning-soft); color: var(--color-warning); font-size: .6875rem; font-weight: 700; }
.assistant-chat-actions { display: flex; align-items: center; gap: var(--space-2); }
.assistant-capability-trigger { display: inline-flex; align-items: center; gap: var(--space-2); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: var(--space-2) var(--space-3); background: var(--color-surface); color: var(--color-text); font-size: .75rem; font-weight: 700; cursor: pointer; }
.assistant-capability-trigger:hover { border-color: var(--color-primary); color: var(--color-primary); }
.assistant-alert { margin: var(--space-3) var(--space-5) 0; border-radius: var(--radius-sm); padding: var(--space-3); background: var(--color-danger-soft); color: var(--color-danger); font-size: .8125rem; }
.assistant-message-region { min-height: 0; flex: 1 1 auto; overflow: hidden; display: flex; flex-direction: column; }
.assistant-loading { flex: 1; display: grid; align-content: start; gap: var(--space-4); padding: var(--space-7); }
.assistant-mobile-tools { display: none; }
.assistant-drawer-backdrop { position: absolute; z-index: 14; inset: 0; display: block; background: rgba(11, 23, 43, .48); }
.assistant-drawer-close { display: grid; place-items: center; width: 34px; height: 34px; margin: 0 0 var(--space-3) auto; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text); }
.assistant-capability-panel.is-drawer { position: absolute; z-index: 15; top: 0; right: 0; bottom: 0; width: min(360px, calc(100% - 48px)); overflow-y: auto; transform: translateX(110%); transition: transform .2s ease; box-shadow: var(--shadow-md); }
.assistant-capability-panel.is-open { transform: translateX(0); }
@media (max-width: 1359px) {
  .assistant-workspace { grid-template-columns: minmax(0, 1fr); }
  .assistant-mobile-tools { position: absolute; z-index: 3; top: var(--space-3); right: var(--space-3); display: flex; gap: var(--space-2); }
  .assistant-mobile-tools button { display: inline-flex; align-items: center; gap: var(--space-1); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: var(--space-2); background: var(--color-surface); color: var(--color-text); font-size: .75rem; }
  .assistant-chat-heading { padding-right: 92px; }
  .assistant-session-panel.is-drawer, .assistant-capability-panel.is-drawer { position: fixed; z-index: 15; top: 0; bottom: 0; width: min(340px, calc(100vw - 48px)); transition: transform .2s ease; box-shadow: var(--shadow-md); }
  .assistant-session-panel.is-drawer { left: 0; border-right: 0; }.assistant-capability-panel.is-drawer { right: 0; border-left: 0; transform: translateX(110%); }
  .assistant-session-panel.is-drawer { transform: translateX(-110%); }
  .assistant-session-panel.is-open, .assistant-capability-panel.is-open { transform: translateX(0); }
  .assistant-drawer-backdrop { position: fixed; z-index: 14; inset: 0; display: block; background: rgba(11, 23, 43, .48); }
}
@media (max-width: 900px) { .assistant-workspace { height: calc(100dvh - 154px); } }
@media (max-width: 640px) { .assistant-workspace { height: calc(100dvh - 170px); }.assistant-chat-heading { padding: 58px var(--space-4) var(--space-3); }.assistant-chat-heading p { display: none; }.assistant-capability-trigger span { display: none; } }
</style>

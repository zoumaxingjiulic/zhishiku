<script setup lang="ts">
import { computed, ref } from "vue";
import BaseButton from "../base/BaseButton.vue";
import type { AssistantSession } from "../../shared/types/assistant";

const props = defineProps<{ sessions: AssistantSession[]; activeSessionId: string }>();
const emit = defineEmits<{
  create: [];
  select: [sessionId: string];
  rename: [sessionId: string, title: string];
  delete: [sessionId: string];
}>();

const query = ref("");
const editingId = ref("");
const editingTitle = ref("");
const filtered = computed(() => {
  const value = query.value.trim().toLocaleLowerCase();
  return value ? props.sessions.filter(item => item.title.toLocaleLowerCase().includes(value)) : props.sessions;
});

function beginRename(session: AssistantSession) {
  editingId.value = session.id;
  editingTitle.value = session.title;
}

function saveRename() {
  const title = editingTitle.value.trim();
  if (editingId.value && title) emit("rename", editingId.value, title);
  editingId.value = "";
}
</script>

<template>
  <div class="assistant-session-list">
    <div class="assistant-panel-heading">
      <div><span class="assistant-eyebrow">CONVERSATIONS</span><h2>会话</h2></div>
      <BaseButton size="sm" @click="emit('create')">新建会话</BaseButton>
    </div>
    <label class="assistant-search">
      <span class="sr-only">搜索会话</span>
      <input v-model="query" type="search" placeholder="搜索会话" aria-label="搜索会话">
    </label>
    <p v-if="!filtered.length" class="assistant-muted">没有匹配的会话</p>
    <ol v-else class="assistant-session-items">
      <li v-for="session in filtered" :key="session.id" :class="{ active: session.id === activeSessionId }">
        <form v-if="editingId === session.id" class="assistant-rename" @submit.prevent="saveRename">
          <input v-model="editingTitle" maxlength="100" aria-label="会话标题" autofocus>
          <BaseButton size="sm" type="submit">保存</BaseButton>
        </form>
        <template v-else>
          <button class="assistant-session-select" type="button" @click="emit('select', session.id)">
            <strong>{{ session.title }}</strong>
            <small>{{ session.message_count || 0 }} 条消息<span v-if="session.latest_task_status"> · {{ session.latest_task_status }}</span></small>
          </button>
          <div class="assistant-session-actions">
            <button type="button" :aria-label="`重命名 ${session.title}`" @click="beginRename(session)">编辑</button>
            <button type="button" :aria-label="`删除 ${session.title}`" @click="emit('delete', session.id)">删除</button>
          </div>
        </template>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.assistant-session-list { height: 100%; display: flex; flex-direction: column; gap: var(--space-4); }
.assistant-panel-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.assistant-panel-heading h2 { margin: 3px 0 0; font-size: 1rem; }
.assistant-eyebrow { color: var(--color-primary); font-size: .625rem; font-weight: 800; letter-spacing: .12em; }
.assistant-search input { min-height: 38px; padding: var(--space-2) var(--space-3); }
.assistant-session-items { min-height: 0; overflow: auto; display: grid; align-content: start; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.assistant-session-items li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--space-2); border: 1px solid transparent; border-radius: var(--radius-md); padding: var(--space-2); }
.assistant-session-items li:hover, .assistant-session-items li.active { border-color: #b8cdf4; background: var(--color-primary-soft); }
.assistant-session-select { min-width: 0; border: 0; padding: var(--space-1); background: transparent; color: var(--color-text); text-align: left; }
.assistant-session-select strong, .assistant-session-select small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assistant-session-select small { margin-top: var(--space-1); color: var(--color-text-muted); font-size: .6875rem; }
.assistant-session-actions { display: flex; gap: var(--space-1); opacity: 0; }
li:hover .assistant-session-actions, li:focus-within .assistant-session-actions, li.active .assistant-session-actions { opacity: 1; }
.assistant-session-actions button { border: 0; padding: var(--space-1); background: transparent; color: var(--color-text-muted); font-size: .6875rem; }
.assistant-session-actions button:last-child { color: var(--color-danger); }
.assistant-rename { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); }
.assistant-rename input { min-height: 34px; padding: var(--space-1) var(--space-2); }
.assistant-muted { color: var(--color-text-muted); font-size: .8125rem; text-align: center; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
</style>

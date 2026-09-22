import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import type {
  AssistantCapabilities,
  AssistantConversationDetail,
  AssistantMessage,
  AssistantSession,
  AssistantTask,
} from "../shared/types/assistant";

const ACTIVE_STATUSES = new Set(["queued", "running", "cancel_requested"]);

export interface AssistantChatOptions {
  pollInterval?: number;
  autoStart?: boolean;
}

function messageFor(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function requestKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function useAssistantChat(options: AssistantChatOptions = {}) {
  const pollInterval = options.pollInterval ?? 1200;
  const sessions = ref<AssistantSession[]>([]);
  const activeSessionId = ref("");
  const capabilities = ref<AssistantCapabilities | null>(null);
  const loading = ref(true);
  const error = ref("");
  const capabilityError = ref("");
  const messagesBySession = reactive<Record<string, AssistantMessage[]>>({});
  const tasksBySession = reactive<Record<string, AssistantTask | undefined>>({});
  const pollers = new Map<string, number>();
  let disposed = false;

  const messages = computed(() => messagesBySession[activeSessionId.value] ?? []);
  const activeTask = computed(() => tasksBySession[activeSessionId.value] ?? null);
  const isAwaitingAnswer = computed(() => Boolean(activeTask.value && ACTIVE_STATUSES.has(activeTask.value.status)));

  function clearError() {
    error.value = "";
  }

  function stopPolling(sessionId: string) {
    const timer = pollers.get(sessionId);
    if (timer !== undefined) window.clearTimeout(timer);
    pollers.delete(sessionId);
  }

  function stopAllPolling() {
    disposed = true;
    for (const sessionId of pollers.keys()) stopPolling(sessionId);
  }

  function schedulePoll(sessionId: string, taskId: string) {
    if (disposed) return;
    stopPolling(sessionId);
    const timer = window.setTimeout(() => void pollTask(sessionId, taskId), pollInterval);
    pollers.set(sessionId, timer);
  }

  async function loadMessages(sessionId: string) {
    const detail = await api<AssistantConversationDetail>(`/api/v1/assistant/sessions/${sessionId}/messages`);
    const confirmed = detail.messages;
    const optimistic = (messagesBySession[sessionId] ?? []).filter(message =>
      message.optimistic && !confirmed.some(item => item.role === message.role && item.content === message.content),
    );
    messagesBySession[sessionId] = [...confirmed, ...optimistic];
    if (detail.latest_task) {
      const previous = tasksBySession[sessionId];
      tasksBySession[sessionId] = previous?.id === detail.latest_task.id
        ? { ...detail.latest_task, execution_summary: detail.latest_task.execution_summary ?? previous.execution_summary }
        : detail.latest_task;
    }
    return detail;
  }

  function updateSessionTask(sessionId: string, task: AssistantTask) {
    const row = sessions.value.find(item => item.id === sessionId);
    if (row) {
      row.latest_task = task;
      row.latest_task_status = task.status;
    }
  }

  async function fetchTask(sessionId: string, taskId: string) {
    const task = await api<AssistantTask>(`/api/v1/assistant/tasks/${taskId}`);
    tasksBySession[sessionId] = task;
    updateSessionTask(sessionId, task);
    return task;
  }

  async function finishTask(sessionId: string) {
    stopPolling(sessionId);
    try {
      await loadMessages(sessionId);
    } catch (cause) {
      error.value = messageFor(cause);
    }
  }

  async function pollTask(sessionId: string, taskId: string) {
    if (disposed) return;
    try {
      const task = await fetchTask(sessionId, taskId);
      if (ACTIVE_STATUSES.has(task.status)) schedulePoll(sessionId, taskId);
      else await finishTask(sessionId);
    } catch (cause) {
      stopPolling(sessionId);
      error.value = messageFor(cause);
      try { await loadMessages(sessionId); } catch { /* retain the original task error */ }
    }
  }

  async function restoreTask(session: AssistantSession) {
    const latest = session.latest_task;
    if (!latest?.id) return;
    tasksBySession[session.id] = latest;
    try {
      const task = await fetchTask(session.id, latest.id);
      if (ACTIVE_STATUSES.has(task.status)) schedulePoll(session.id, task.id);
      else if (task.status !== latest.status) await loadMessages(session.id);
    } catch (cause) {
      error.value = messageFor(cause);
    }
  }

  async function loadCapabilities() {
    try {
      capabilities.value = await api<AssistantCapabilities>("/api/v1/assistant/capabilities");
      capabilityError.value = "";
    } catch (cause) {
      capabilityError.value = messageFor(cause);
      if (!error.value) error.value = capabilityError.value;
    }
  }

  async function loadSessions() {
    sessions.value = await api<AssistantSession[]>("/api/v1/assistant/sessions");
    if (!sessions.value.length) {
      const created = await api<AssistantSession>("/api/v1/assistant/sessions", { method: "POST" });
      sessions.value = [created];
    }
    if (!sessions.value.some(item => item.id === activeSessionId.value)) {
      activeSessionId.value = sessions.value[0]?.id ?? "";
    }
    await Promise.all(sessions.value.map(restoreTask));
    if (activeSessionId.value) await loadMessages(activeSessionId.value);
  }

  async function initialize() {
    disposed = false;
    loading.value = true;
    clearError();
    const capabilityRequest = loadCapabilities();
    try {
      await loadSessions();
    } catch (cause) {
      error.value = messageFor(cause);
    } finally {
      await capabilityRequest;
      loading.value = false;
    }
  }

  async function createSession() {
    clearError();
    try {
      const created = await api<AssistantSession>("/api/v1/assistant/sessions", { method: "POST" });
      sessions.value.unshift(created);
      activeSessionId.value = created.id;
      messagesBySession[created.id] = [];
      await loadMessages(created.id);
      return created;
    } catch (cause) {
      error.value = messageFor(cause);
      return null;
    }
  }

  async function selectSession(sessionId: string) {
    activeSessionId.value = sessionId;
    clearError();
    if (messagesBySession[sessionId]) return;
    try { await loadMessages(sessionId); }
    catch (cause) { error.value = messageFor(cause); }
  }

  async function renameSession(sessionId: string, title: string) {
    const normalized = title.trim();
    if (!normalized) return;
    clearError();
    try {
      const renamed = await api<Pick<AssistantSession, "id" | "title">>(`/api/v1/assistant/sessions/${sessionId}`, {
        method: "PATCH", body: JSON.stringify({ title: normalized }),
      });
      const row = sessions.value.find(item => item.id === sessionId);
      if (row) row.title = renamed.title;
    } catch (cause) { error.value = messageFor(cause); }
  }

  async function deleteSession(sessionId: string) {
    clearError();
    try {
      await api(`/api/v1/assistant/sessions/${sessionId}`, { method: "DELETE" });
      stopPolling(sessionId);
      sessions.value = sessions.value.filter(item => item.id !== sessionId);
      delete messagesBySession[sessionId];
      delete tasksBySession[sessionId];
      if (activeSessionId.value === sessionId) {
        if (!sessions.value.length) await createSession();
        else await selectSession(sessions.value[0].id);
      }
    } catch (cause) { error.value = messageFor(cause); }
  }

  async function sendMessage(question: string) {
    const sessionId = activeSessionId.value;
    const content = question.trim();
    if (!sessionId || !content || isAwaitingAnswer.value) return false;
    clearError();
    const key = requestKey();
    const optimistic: AssistantMessage = {
      id: `optimistic-${key}`, role: "user", content, citations: [], tool_calls: [], optimistic: true,
    };
    messagesBySession[sessionId] = [...(messagesBySession[sessionId] ?? []), optimistic];
    tasksBySession[sessionId] = { id: `pending-${key}`, session_id: sessionId, status: "queued", stage: "正在提交" };
    try {
      const task = await api<AssistantTask>(`/api/v1/assistant/sessions/${sessionId}/messages`, {
        method: "POST", body: JSON.stringify({ question: content, request_key: key }),
      });
      tasksBySession[sessionId] = task;
      updateSessionTask(sessionId, task);
      schedulePoll(sessionId, task.id);
      return true;
    } catch (cause) {
      delete tasksBySession[sessionId];
      messagesBySession[sessionId] = (messagesBySession[sessionId] ?? []).filter(message => message.id !== optimistic.id);
      error.value = messageFor(cause);
      try { await loadMessages(sessionId); } catch { /* keep the submit error visible */ }
      return false;
    }
  }

  async function stopAnswer() {
    const sessionId = activeSessionId.value;
    const task = tasksBySession[sessionId];
    if (!task || !ACTIVE_STATUSES.has(task.status) || task.id.startsWith("pending-")) return;
    clearError();
    try {
      await api(`/api/v1/assistant/tasks/${task.id}/cancel`, { method: "POST" });
      const refreshed = await fetchTask(sessionId, task.id);
      await loadMessages(sessionId);
      if (ACTIVE_STATUSES.has(refreshed.status)) schedulePoll(sessionId, task.id);
      else stopPolling(sessionId);
    } catch (cause) { error.value = messageFor(cause); }
  }

  if (options.autoStart !== false) onMounted(initialize);
  onBeforeUnmount(stopAllPolling);

  return {
    sessions, activeSessionId, capabilities, loading, error, capabilityError,
    messagesBySession, tasksBySession, messages, activeTask, isAwaitingAnswer,
    initialize, createSession, selectSession, renameSession, deleteSession,
    sendMessage, stopAnswer, stopAllPolling,
  };
}

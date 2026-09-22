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
  const readyBySession = reactive<Record<string, boolean>>({});
  const draftsBySession = reactive<Record<string, string>>({});
  const pollers = new Map<string, { timer: number; taskId: string; epoch: number }>();
  const epochs = new Map<string, number>();
  const retryCounts = new Map<string, number>();
  const retrySubmissions = new Map<string, { content: string; key: string }>();
  let disposed = false;

  const messages = computed(() => messagesBySession[activeSessionId.value] ?? []);
  const activeTask = computed(() => tasksBySession[activeSessionId.value] ?? null);
  const isAwaitingAnswer = computed(() => Boolean(activeTask.value && ACTIVE_STATUSES.has(activeTask.value.status)));
  const activeSessionReady = computed(() => Boolean(readyBySession[activeSessionId.value]));
  const draft = computed({
    get: () => draftsBySession[activeSessionId.value] ?? "",
    set: value => { if (activeSessionId.value) draftsBySession[activeSessionId.value] = value; },
  });

  function clearError() {
    error.value = "";
  }

  function epochFor(sessionId: string) { return epochs.get(sessionId) ?? 0; }

  function advanceEpoch(sessionId: string) {
    const next = epochFor(sessionId) + 1;
    epochs.set(sessionId, next);
    return next;
  }

  function isCurrent(sessionId: string, epoch: number, taskId?: string) {
    if (disposed || epochFor(sessionId) !== epoch) return false;
    return !taskId || tasksBySession[sessionId]?.id === taskId;
  }

  function stopPolling(sessionId: string, taskId?: string, epoch?: number) {
    const entry = pollers.get(sessionId);
    if (!entry || (taskId && entry.taskId !== taskId) || (epoch !== undefined && entry.epoch !== epoch)) return;
    window.clearTimeout(entry.timer);
    pollers.delete(sessionId);
  }

  function stopAllPolling() {
    disposed = true;
    for (const sessionId of pollers.keys()) stopPolling(sessionId);
  }

  function schedulePoll(sessionId: string, taskId: string, epoch = epochFor(sessionId), delay = pollInterval) {
    if (!isCurrent(sessionId, epoch, taskId)) return;
    stopPolling(sessionId);
    const timer = window.setTimeout(() => void pollTask(sessionId, taskId, epoch), delay);
    pollers.set(sessionId, { timer, taskId, epoch });
  }

  async function loadMessages(sessionId: string, expectedEpoch = epochFor(sessionId), expectedTaskId?: string) {
    const detail = await api<AssistantConversationDetail>(`/api/v1/assistant/sessions/${sessionId}/messages`);
    if (!isCurrent(sessionId, expectedEpoch, expectedTaskId)) return null;
    const confirmed = detail.messages;
    const optimistic = (messagesBySession[sessionId] ?? []).filter(message =>
      message.optimistic && !confirmed.some(item => item.role === message.role && item.content === message.content),
    );
    messagesBySession[sessionId] = [...confirmed, ...optimistic];
    if (detail.latest_task) {
      const previous = tasksBySession[sessionId];
      tasksBySession[sessionId] = previous?.id === detail.latest_task.id
        ? {
            ...detail.latest_task,
            execution_summary: detail.latest_task.execution_summary ?? previous.execution_summary,
            assistant_message_id: detail.latest_task.assistant_message_id ?? previous.assistant_message_id,
          }
        : detail.latest_task;
      updateSessionTask(sessionId, tasksBySession[sessionId]!);
      if (ACTIVE_STATUSES.has(tasksBySession[sessionId]!.status)) {
        schedulePoll(sessionId, tasksBySession[sessionId]!.id, expectedEpoch);
      }
    }
    readyBySession[sessionId] = true;
    return detail;
  }

  function updateSessionTask(sessionId: string, task: AssistantTask) {
    const row = sessions.value.find(item => item.id === sessionId);
    if (row) {
      row.latest_task = task;
      row.latest_task_status = task.status;
    }
  }

  async function fetchTask(sessionId: string, taskId: string, expectedEpoch = epochFor(sessionId)) {
    const task = await api<AssistantTask>(`/api/v1/assistant/tasks/${taskId}`);
    if (!isCurrent(sessionId, expectedEpoch, taskId)) return null;
    tasksBySession[sessionId] = task;
    updateSessionTask(sessionId, task);
    return task;
  }

  async function refreshSessionsFromServer(sessionId: string, expectedEpoch: number) {
    const truth = await api<AssistantSession[]>("/api/v1/assistant/sessions");
    if (!isCurrent(sessionId, expectedEpoch)) return;
    sessions.value = truth;
  }

  async function finishTask(sessionId: string, taskId: string, expectedEpoch: number) {
    stopPolling(sessionId, taskId, expectedEpoch);
    try {
      await loadMessages(sessionId, expectedEpoch, taskId);
      await refreshSessionsFromServer(sessionId, expectedEpoch);
      retryCounts.delete(`${sessionId}:${taskId}`);
    } catch (cause) {
      if (!isCurrent(sessionId, expectedEpoch, taskId)) return;
      error.value = `同步回答失败，正在重试：${messageFor(cause)}`;
      schedulePoll(sessionId, taskId, expectedEpoch, Math.min(pollInterval * 4, 5000));
    }
  }

  async function pollTask(sessionId: string, taskId: string, expectedEpoch: number) {
    if (!isCurrent(sessionId, expectedEpoch, taskId)) return;
    try {
      const task = await fetchTask(sessionId, taskId, expectedEpoch);
      if (!task) return;
      retryCounts.delete(`${sessionId}:${taskId}`);
      if (error.value.includes("正在重试")) error.value = "";
      if (ACTIVE_STATUSES.has(task.status)) schedulePoll(sessionId, taskId, expectedEpoch);
      else await finishTask(sessionId, taskId, expectedEpoch);
    } catch (cause) {
      if (!isCurrent(sessionId, expectedEpoch, taskId)) return;
      const key = `${sessionId}:${taskId}`;
      const failures = (retryCounts.get(key) ?? 0) + 1;
      retryCounts.set(key, failures);
      error.value = `连接中断，正在重试：${messageFor(cause)}`;
      const delay = Math.min(pollInterval * (2 ** Math.min(failures, 4)), 5000);
      schedulePoll(sessionId, taskId, expectedEpoch, delay);
    }
  }

  async function restoreTask(session: AssistantSession) {
    const latest = session.latest_task;
    if (!latest?.id) return;
    const expectedEpoch = epochFor(session.id);
    tasksBySession[session.id] = latest;
    try {
      const task = await fetchTask(session.id, latest.id, expectedEpoch);
      if (!task) return;
      if (ACTIVE_STATUSES.has(task.status)) schedulePoll(session.id, task.id, expectedEpoch);
      else if (task.status !== latest.status) await loadMessages(session.id, expectedEpoch, task.id);
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
    for (const row of sessions.value) {
      if (!epochs.has(row.id)) epochs.set(row.id, 0);
      readyBySession[row.id] = false;
    }
    await Promise.all(sessions.value.map(restoreTask));
    if (activeSessionId.value) await loadMessages(activeSessionId.value, epochFor(activeSessionId.value));
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
      readyBySession[created.id] = false;
      epochs.set(created.id, 0);
      await loadMessages(created.id, epochFor(created.id));
      return created;
    } catch (cause) {
      error.value = messageFor(cause);
      return null;
    }
  }

  async function selectSession(sessionId: string) {
    activeSessionId.value = sessionId;
    clearError();
    if (readyBySession[sessionId]) return;
    readyBySession[sessionId] = false;
    try { await loadMessages(sessionId, epochFor(sessionId)); }
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
      delete readyBySession[sessionId];
      delete draftsBySession[sessionId];
      epochs.delete(sessionId);
      retrySubmissions.delete(sessionId);
      if (activeSessionId.value === sessionId) {
        if (!sessions.value.length) await createSession();
        else await selectSession(sessions.value[0].id);
      }
    } catch (cause) { error.value = messageFor(cause); }
  }

  async function sendMessage(question: string) {
    const sessionId = activeSessionId.value;
    const content = question.trim();
    if (!sessionId || !content || isAwaitingAnswer.value || !readyBySession[sessionId]) return false;
    clearError();
    const retry = retrySubmissions.get(sessionId);
    const key = retry?.content === content ? retry.key : requestKey();
    retrySubmissions.set(sessionId, { content, key });
    const expectedEpoch = advanceEpoch(sessionId);
    const optimistic: AssistantMessage = {
      id: `optimistic-${key}`, role: "user", content, citations: [], tool_calls: [], optimistic: true,
    };
    messagesBySession[sessionId] = [...(messagesBySession[sessionId] ?? []), optimistic];
    tasksBySession[sessionId] = { id: `pending-${key}`, session_id: sessionId, status: "queued", stage: "正在提交" };
    try {
      const task = await api<AssistantTask>(`/api/v1/assistant/sessions/${sessionId}/messages`, {
        method: "POST", body: JSON.stringify({ question: content, request_key: key }),
      });
      if (!isCurrent(sessionId, expectedEpoch, `pending-${key}`)) return false;
      tasksBySession[sessionId] = task;
      updateSessionTask(sessionId, task);
      retrySubmissions.delete(sessionId);
      schedulePoll(sessionId, task.id, expectedEpoch);
      return true;
    } catch (cause) {
      if (!isCurrent(sessionId, expectedEpoch, `pending-${key}`)) return false;
      error.value = messageFor(cause);
      try {
        const detail = await loadMessages(sessionId, expectedEpoch);
        const recovered = detail?.latest_task;
        if (recovered && ACTIVE_STATUSES.has(recovered.status)) {
          retrySubmissions.delete(sessionId);
          error.value = "";
          schedulePoll(sessionId, recovered.id, expectedEpoch);
          return true;
        }
      } catch { /* keep the submit error visible */ }
      if (isCurrent(sessionId, expectedEpoch, `pending-${key}`)) delete tasksBySession[sessionId];
      messagesBySession[sessionId] = (messagesBySession[sessionId] ?? []).filter(message => message.id !== optimistic.id);
      return false;
    }
  }

  async function stopAnswer() {
    const sessionId = activeSessionId.value;
    const task = tasksBySession[sessionId];
    if (!task || !ACTIVE_STATUSES.has(task.status) || task.id.startsWith("pending-")) return;
    clearError();
    const expectedEpoch = epochFor(sessionId);
    const taskId = task.id;
    try {
      await api(`/api/v1/assistant/tasks/${taskId}/cancel`, { method: "POST" });
      if (!isCurrent(sessionId, expectedEpoch, taskId)) return;
      const refreshed = await fetchTask(sessionId, taskId, expectedEpoch);
      if (!refreshed) return;
      await loadMessages(sessionId, expectedEpoch, taskId);
      if (ACTIVE_STATUSES.has(refreshed.status)) schedulePoll(sessionId, taskId, expectedEpoch);
      else await finishTask(sessionId, taskId, expectedEpoch);
    } catch (cause) { error.value = messageFor(cause); }
  }

  if (options.autoStart !== false) onMounted(initialize);
  onBeforeUnmount(stopAllPolling);

  return {
    sessions, activeSessionId, capabilities, loading, error, capabilityError,
    messagesBySession, tasksBySession, readyBySession, draftsBySession,
    messages, activeTask, isAwaitingAnswer, activeSessionReady, draft,
    initialize, createSession, selectSession, renameSession, deleteSession,
    sendMessage, stopAnswer, stopAllPolling,
  };
}

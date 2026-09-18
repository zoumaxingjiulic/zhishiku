<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { formatPageRange, isActiveChatTaskStatus } from "../utils";
import WorkflowRun from '../components/WorkflowRun.vue';

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const route = useRoute();
const router = useRouter();
const agents = ref<any[]>([]);
const selected = ref<any>(null);
const question = ref("");
const sessionId = ref<string | null>(null);
const pendingSessionIds = ref(new Set<string>());
const messages = ref<any[]>([]);
const sessions = ref<any[]>([]);
const task = ref<any>(null);
const modeMeta: Record<string, { label: string; icon: string; hint: string }> = {
  chat: { label: "问答", icon: "✦", hint: "对话查询与知识检索" },
  form: { label: "任务", icon: "▣", hint: "填写参数后执行任务" },
  workflow: { label: "流程", icon: "⇢", hint: "多步骤业务流程" },
  dashboard: { label: "看板", icon: "▥", hint: "业务数据分析看板" },
  external: { label: "系统", icon: "↗", hint: "打开外部业务应用" },
};
const welcomeMessage = { role: "assistant", text: "您好，我会使用该智能体获授权的企业知识与只读系统工具协助您。" };
const isAwaitingAnswer = computed(() => Boolean(
  sessionId.value && (pendingSessionIds.value.has(sessionId.value)
    || (task.value?.session_id===sessionId.value && isActiveChatTaskStatus(task.value?.status)))
));
let pollTimer: number | undefined;
let routeSyncVersion = 0;

onMounted(async () => {
  try {
    agents.value = await api<any[]>("/api/v1/agents");
    await syncFromRoute();
  } catch (error: any) { emit("toast", error.message, true); }
});
watch(() => route.fullPath, () => {
  if (agents.value.length) void syncFromRoute();
});
onBeforeUnmount(() => {
  routeSyncVersion += 1;
  stopPolling();
});

function sessionKey(agentId: number) { return `kb.chatSession.${agentId}`; }
function stopPolling() {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
    pollTimer = undefined;
  }
}
async function scrollToBottom(smooth = false) {
  await nextTick();
  document.querySelector(".messages")?.scrollTo({ top: 999999, behavior: smooth ? "smooth" : "auto" });
}
function schedulePendingPoll(id: string) {
  stopPolling();
  pollTimer = window.setTimeout(async () => {
    if (sessionId.value !== id || !selected.value) return;
    try {
      const [history,currentTask] = await Promise.all([api<any>(`/api/v1/agents/${selected.value.id}/chat/sessions/${id}`),api<any>(`/api/v1/agents/${selected.value.id}/chat/sessions/${id}/task`)]);
      if (sessionId.value !== id || route.params.sessionId !== id) return;
      messages.value = history.messages?.length ? history.messages : [welcomeMessage];
      task.value = currentTask;
      await loadSessions();
      if (currentTask && isActiveChatTaskStatus(currentTask.status)) schedulePendingPoll(id);
      else {
        stopPolling();
        await scrollToBottom(true);
      }
    } catch { if(sessionId.value===id)schedulePendingPoll(id); }
  }, 1000);
}
async function loadSessions() {
  if (!selected.value) return;
  sessions.value = await api<any[]>(`/api/v1/agents/${selected.value.id}/chat/sessions`);
}
async function fetchSession(id: string) {
  stopPolling();
  const history = await api<any>(`/api/v1/agents/${selected.value.id}/chat/sessions/${id}`);
  const currentTask = await api<any>(`/api/v1/agents/${selected.value.id}/chat/sessions/${id}/task`);
  if (route.params.sessionId !== id) return;
  task.value = currentTask;
  sessionId.value = id;
  sessionStorage.setItem(sessionKey(selected.value.id), id);
  sessionStorage.setItem("kb.lastAgentRoute", route.fullPath);
  messages.value = history.messages?.length ? history.messages : [welcomeMessage];
  schedulePendingPoll(id);
  await scrollToBottom();
}
async function loadSession(id: string) {
  if (!selected.value) return;
  const routeId = typeof route.params.sessionId === "string" ? route.params.sessionId : "";
  if (route.name !== "agent-chat" || routeId !== id) {
    await router.push({ name: "agent-chat", params: { agentId: selected.value.id, sessionId: id } });
    return;
  }
  await fetchSession(id);
}
async function syncFromRoute() {
  const version = ++routeSyncVersion;
  stopPolling();
  if (route.name === "agents") {
    const lastRoute = sessionStorage.getItem("kb.lastAgentRoute");
    if (lastRoute && lastRoute !== route.fullPath) {
      await router.replace(lastRoute);
      return;
    }
    selected.value = null;
    sessionId.value = null;
    messages.value = [];
    sessions.value = [];
    return;
  }

  const agentId = Number(route.params.agentId);
  const agent = agents.value.find((item) => item.id === agentId);
  if (!agent) {
    sessionStorage.removeItem("kb.lastAgentRoute");
    await router.replace({ name: "agents" });
    return;
  }
  selected.value = agent;
  if (agent.launch_mode !== "chat") {
    sessionStorage.setItem("kb.lastAgentRoute", route.fullPath);
    sessionId.value = null;
    messages.value = [];
    sessions.value = [];
    return;
  }

  try {
    await loadSessions();
    if (version !== routeSyncVersion) return;
    const requested = typeof route.params.sessionId === "string" ? route.params.sessionId : "";
    const remembered = sessionStorage.getItem(sessionKey(agent.id));
    const target = sessions.value.find((item) => item.id === requested)
      || sessions.value.find((item) => item.id === remembered)
      || sessions.value[0];
    if (!target) {
      await newConversation();
      return;
    }
    if (route.name !== "agent-chat" || requested !== target.id) {
      await router.replace({ name: "agent-chat", params: { agentId: agent.id, sessionId: target.id } });
      return;
    }
    sessionStorage.setItem("kb.lastAgentRoute", route.fullPath);
    await fetchSession(target.id);
  } catch (error: any) {
    sessionId.value = null;
    messages.value = [welcomeMessage];
    emit("toast", error.message, true);
  }
}
async function open(agent: any) {
  await router.push({ name: "agent", params: { agentId: agent.id } });
}
async function newConversation() {
  if (!selected.value) return null;
  try {
    const created = await api<any>(`/api/v1/agents/${selected.value.id}/chat/sessions`, { method: "POST" });
    await loadSessions();
    sessionId.value = created.id;
    messages.value = [welcomeMessage];
    sessionStorage.setItem(sessionKey(selected.value.id), created.id);
    await router.push({ name: "agent-chat", params: { agentId: selected.value.id, sessionId: created.id } });
    return created.id as string;
  } catch (error: any) {
    emit("toast", error.message, true);
    return null;
  }
}
async function deleteConversation(event: Event, item: any) {
  event.stopPropagation();
  if (isActiveChatTaskStatus(item.latest_task_status) || pendingSessionIds.value.has(item.id)) {
    emit("toast", "该对话正在生成回答，请完成后再删除", true);
    return;
  }
  if (!selected.value) return;
  if (!window.confirm(`确定删除对话“${item.title || "新对话"}”吗？删除后无法恢复。`)) return;
  try {
    await api(`/api/v1/agents/${selected.value.id}/chat/sessions/${item.id}`, { method: "DELETE" });
    if (sessionId.value === item.id) {
      sessionStorage.removeItem(sessionKey(selected.value.id));
      await loadSessions();
      if (sessions.value.length) {
        await router.replace({ name: "agent-chat", params: { agentId: selected.value.id, sessionId: sessions.value[0].id } });
      } else await newConversation();
    } else await loadSessions();
    emit("toast", "对话已删除");
  } catch (error: any) { emit("toast", error.message, true); }
}
async function backToList() {
  stopPolling();
  sessionStorage.removeItem("kb.lastAgentRoute");
  await router.push({ name: "agents" });
}
async function send() {
  const text = question.value.trim();
  if (!text || isAwaitingAnswer.value || !selected.value) return;
  if (!sessionId.value && !await newConversation()) return;
  const targetSessionId = sessionId.value as string;
  const targetAgentId = selected.value.id as number;
  messages.value.push({ role: "user", text });
  question.value = "";
  pendingSessionIds.value.add(targetSessionId);
  try {
    const result = await api<any>(`/api/v1/agents/${targetAgentId}/runs`, {
      method: "POST",
      body: JSON.stringify({ question: text, session_id: targetSessionId, request_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}` }),
    });
    if (selected.value?.id === targetAgentId && sessionId.value === targetSessionId) {
      task.value = result;
      schedulePendingPoll(targetSessionId);
    }
    if (selected.value?.id === targetAgentId) await loadSessions();
  } catch (error: any) {
    emit("toast", error.message, true);
  } finally {
    pendingSessionIds.value.delete(targetSessionId);
    if (selected.value?.id === targetAgentId) await loadSessions();
    if (sessionId.value === targetSessionId) await scrollToBottom(true);
  }
}
async function cancelTask(){if(!task.value)return;try{await api(`/api/v1/tasks/${task.value.id}/cancel`,{method:'POST'});emit('toast','已请求停止，正在结束当前调用');}catch(e:any){emit('toast',e.message,true);}}
async function feedback(message:any,rating:number){try{await api(`/api/v1/messages/${message.id}/feedback`,{method:'POST',body:JSON.stringify({rating})});message.rating=rating;emit('toast','反馈已记录');}catch(e:any){emit('toast',e.message,true);}}
</script>

<template>
  <template v-if="!selected">
    <div class="section-head"><div><h2>可用智能体</h2><p>问答、流程、任务和业务看板统一从这里进入</p></div><span class="badge success">{{agents.length}} 个可用</span></div>
    <div v-if="agents.length" class="agents-grid">
      <article v-for="agent in agents" :key="agent.id" class="card agent-list-card" @click="open(agent)">
        <div class="agent-card-top"><div class="agent-symbol compact">{{agent.icon||modeMeta[agent.launch_mode]?.icon||'✦'}}</div><span class="badge">{{modeMeta[agent.launch_mode]?.label||agent.agent_type}}</span></div>
        <div class="agent-list-content"><h2>{{agent.name}}</h2><p>{{agent.description||modeMeta[agent.launch_mode]?.hint}}</p><div class="agent-card-foot"><small>{{agent.category||modeMeta[agent.launch_mode]?.hint}}</small><span>打开 →</span></div></div>
      </article>
    </div>
    <div v-else class="card empty">暂无可用智能体</div>
  </template>

  <template v-else>
    <div class="agent-chat-head"><button class="secondary" @click="backToList">← 返回智能体列表</button><div><h2>{{selected.name}}</h2><p>{{modeMeta[selected.launch_mode]?.hint}}</p></div></div>
    <div v-if="selected.launch_mode==='chat'" class="chat-layout">
      <div class="agent-card">
        <div class="agent-identity"><div class="agent-symbol light">✦</div><span class="eyebrow">CHAT AGENT</span><h2>{{selected.name}}</h2><p>{{selected.description}}</p><div class="agent-scope dark"><small>授权知识 / 工具</small><strong>{{selected.knowledge_bases || selected.tools || '未配置'}}</strong></div></div>
        <div class="conversation-head"><strong>我的对话</strong><button class="new-chat" @click="newConversation">＋ 新建</button></div>
        <div class="conversation-list"><div v-for="item in sessions" :key="item.id" class="conversation-item" :class="{active:sessionId===item.id}"><button class="conversation-select" @click="loadSession(item.id)"><span><strong>{{item.title||'新对话'}}</strong><small>{{isActiveChatTaskStatus(item.latest_task_status)||pendingSessionIds.has(item.id)?'回答中…':item.message_count+' 条消息'}}</small></span></button><button class="conversation-delete" title="删除对话" :disabled="isActiveChatTaskStatus(item.latest_task_status)||pendingSessionIds.has(item.id)" @click="deleteConversation($event,item)">×</button></div></div>
      </div>
      <div class="card chat-box">
        <div v-if="isAwaitingAnswer" class="actions"><span class="badge">{{task?.stage||'提交中'}}</span><button class="danger" @click="cancelTask">停止回答</button></div>
        <div class="chat-scope fixed"><span>授权范围</span><strong>智能体知识库与企业系统工具</strong><small>由管理员统一配置，并在后端再次校验权限</small></div>
        <div class="messages"><div v-for="(message,index) in messages" :key="message.id||index" class="message" :class="message.role">{{message.content||message.text}}<div v-if="message.tool_calls?.length" class="tool-call-note"><span v-for="(event,i) in message.tool_calls" :key="i">{{event.success?'✓':'!'}} {{event.connector_name||event.connector}} / {{event.tool}}{{i<message.tool_calls.length-1?'；':''}}</span></div><div v-if="message.citations?.length" class="citation">参考资料：<span v-for="(citation,i) in message.citations" :key="i"><a :href="'/api/v1/documents/'+citation.document_id+'/download'">《{{citation.title}}》</a>{{formatPageRange(citation.page,citation.page_end)}}{{i<message.citations.length-1?'；':''}}</span></div><div v-if="message.role==='assistant'&&message.id" class="actions"><button class="ghost" :disabled="message.rating===1" @click="feedback(message,1)">有帮助</button><button class="ghost" :disabled="message.rating===-1" @click="feedback(message,-1)">需改进</button></div></div></div>
        <form class="chat-input" @submit.prevent="send"><textarea v-model="question" :disabled="isAwaitingAnswer" placeholder="请输入您想查询的问题…" required></textarea><button class="primary" :disabled="isAwaitingAnswer">{{isAwaitingAnswer?"回答中…":"发送"}}</button></form>
      </div>
    </div>
    <WorkflowRun v-else-if="selected.launch_mode==='workflow'" :key="selected.id" :agent-id="selected.id" />
    <div v-else class="card empty">该类型尚未配置运行器，请联系管理员。</div>
  </template>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";
import { formatDate } from "../utils";

const props = defineProps<{ user?: any }>();
const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const payload = ref<any>({ items: [], planned_types: [] });
const bindingData = ref<any>({ agents: [], tools: [], bindings: {} });
const selectedAgentId = ref<number | null>(null);
const modal = ref(false);
const busy = ref(false);
const editingId = ref<number | null>(null);
const form = reactive({ code: "", name: "", connector_type: "custom", description: "", base_url: "", bearer_token: "", protocol_version: "2025-06-18", status: "active" });
const isAdmin = computed(() => Boolean(props.user?.is_platform_admin));
const selectedTools = computed(() => selectedAgentId.value ? (bindingData.value.bindings[String(selectedAgentId.value)] || []) : []);

onMounted(load);
async function load() {
  try {
    payload.value = await api<any>("/api/v1/connectors");
    if (isAdmin.value) {
      bindingData.value = await api<any>("/api/v1/connectors/admin/bindings");
      if (!selectedAgentId.value && bindingData.value.agents.length) selectedAgentId.value = bindingData.value.agents[0].id;
    }
  } catch (error: any) { emit("toast", error.message, true); }
}
function openCreate() {
  editingId.value = null;
  Object.assign(form, { code: "", name: "", connector_type: "custom", description: "", base_url: "", bearer_token: "", protocol_version: "2025-06-18", status: "active" });
  modal.value = true;
}
function openEdit(item: any) {
  editingId.value = item.id;
  Object.assign(form, { code: item.code, name: item.name, connector_type: item.connector_type, description: item.description || "", base_url: item.base_url || "", bearer_token: "", protocol_version: item.protocol_version || "2025-06-18", status: item.status });
  modal.value = true;
}
async function save() {
  busy.value = true;
  try {
    await api(editingId.value ? `/api/v1/connectors/${editingId.value}` : "/api/v1/connectors", { method: editingId.value ? "PUT" : "POST", body: JSON.stringify(form) });
    modal.value = false;
    await load();
    emit("toast", editingId.value ? "连接器已更新" : "连接器已创建");
  } catch (error: any) { emit("toast", error.message, true); }
  finally { busy.value = false; }
}
async function discover(item: any) {
  busy.value = true;
  try {
    const result = await api<any>(`/api/v1/connectors/${item.id}/discover`, { method: "POST" });
    await load();
    emit("toast", `连接成功，发现 ${result.tool_count} 个工具`);
  } catch (error: any) { emit("toast", error.message, true); }
  finally { busy.value = false; }
}
function isChecked(toolId: number) { return selectedTools.value.includes(toolId); }
function toggleTool(toolId: number) {
  if (!selectedAgentId.value) return;
  const key = String(selectedAgentId.value);
  const current = [...(bindingData.value.bindings[key] || [])];
  bindingData.value.bindings[key] = current.includes(toolId) ? current.filter(id => id !== toolId) : [...current, toolId];
}
async function saveBinding() {
  if (!selectedAgentId.value) return;
  try {
    await api(`/api/v1/agents/${selectedAgentId.value}/connector-tools`, { method: "PUT", body: JSON.stringify({ connector_tool_ids: selectedTools.value }) });
    emit("toast", "智能体工具授权已保存");
  } catch (error: any) { emit("toast", error.message, true); }
}
</script>

<template>
  <div class="section-head"><div><h2>MCP 企业系统连接</h2><p>以 Streamable HTTP 接入企业系统，只向智能体授权明确声明为只读的工具</p></div><button v-if="isAdmin" class="primary" @click="openCreate">＋ 新建连接</button></div>
  <div class="connector-grid live-connectors">
    <article v-for="item in payload.items" :key="item.id" class="card connector">
      <div class="connector-title"><span class="connector-logo">{{ item.connector_type.toUpperCase() }}</span><span class="badge" :class="item.last_error?'failed':item.status==='active'?'success':'pending'">{{ item.last_error?'连接异常':item.status==='active'?'已启用':'待配置' }}</span></div>
      <h2>{{ item.name }}</h2><p>{{ item.description || "暂无说明" }}</p>
      <dl class="connector-meta"><div><dt>传输</dt><dd>Streamable HTTP</dd></div><div><dt>工具</dt><dd>{{ item.tool_count || 0 }} 个</dd></div><div v-if="isAdmin"><dt>凭据</dt><dd>{{ item.has_credential ? "已加密保存" : "未配置" }}</dd></div><div><dt>最近检查</dt><dd>{{ formatDate(item.last_checked_at) }}</dd></div></dl>
      <div v-if="item.tools?.length" class="tool-tags"><span v-for="tool in item.tools" :key="tool.id" class="tool-tag">{{ tool.title || tool.tool_name }}<i v-if="tool.annotations?.readOnlyHint">只读</i></span></div>
      <p v-if="isAdmin && item.last_error" class="error connector-error">{{ item.last_error }}</p>
      <div v-if="isAdmin" class="actions"><button class="secondary" @click="openEdit(item)">编辑</button><button class="primary" :disabled="busy || !item.has_credential" @click="discover(item)">连接并发现工具</button></div>
    </article>
    <div v-if="!payload.items.length" class="card empty">尚未配置企业系统连接</div>
  </div>

  <section v-if="isAdmin" class="card binding-panel">
    <div class="card-header"><div><h2>智能体工具授权</h2><p>工具先由 MCP 服务声明，再由平台管理员按智能体最小授权；当前仅允许只读工具</p></div><button class="primary" :disabled="!selectedAgentId" @click="saveBinding">保存授权</button></div>
    <label class="binding-agent">选择智能体<select v-model="selectedAgentId"><option v-for="agent in bindingData.agents" :key="agent.id" :value="agent.id">{{ agent.name }}（{{ agent.code }}）</option></select></label>
    <div class="tool-binding-grid"><label v-for="tool in bindingData.tools" :key="tool.id" class="tool-binding" :class="{checked:isChecked(tool.id)}"><input type="checkbox" :checked="isChecked(tool.id)" @change="toggleTool(tool.id)"><span><strong>{{ tool.connector_name }} / {{ tool.title || tool.tool_name }}</strong><small>{{ tool.description }}</small></span><i class="badge success">只读</i></label><div v-if="!bindingData.tools.length" class="empty">请先连接 MCP 并发现工具</div></div>
  </section>

  <AppModal v-if="modal" :title="editingId?'编辑 MCP 连接':'新建 MCP 连接'" @close="modal=false">
    <form class="form-stack" @submit.prevent="save">
      <div class="form-grid"><label>连接编码<input v-model.trim="form.code" placeholder="ERP_U9" required></label><label>显示名称<input v-model.trim="form.name" required></label></div>
      <div class="form-grid"><label>系统类型<select v-model="form.connector_type"><option value="erp">ERP</option><option value="oa">OA</option><option value="plm">PLM</option><option value="mom">MOM</option><option value="custom">自定义</option></select></label><label>状态<select v-model="form.status"><option value="active">启用</option><option value="draft">草稿</option><option value="disabled">停用</option></select></label></div>
      <label>MCP 服务地址<input v-model.trim="form.base_url" type="url" placeholder="http://server:port/mcp" required></label>
      <label>Bearer Token<input v-model="form.bearer_token" type="password" autocomplete="new-password" :placeholder="editingId?'留空表示保留原凭据':'输入访问 Token'"><small class="muted">凭据经 Fernet 加密后存储，页面不会回显。</small></label>
      <label>说明<textarea v-model="form.description"></textarea></label>
      <button class="primary" :disabled="busy">{{busy?'保存中…':'保存连接'}}</button>
    </form>
  </AppModal>
</template>

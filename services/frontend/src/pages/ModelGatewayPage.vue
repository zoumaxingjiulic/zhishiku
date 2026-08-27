<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const profiles = ref<any[]>([]);
const agents = ref<any[]>([]);
const modal = ref(false);
const editingId = ref<number | null>(null);
const bindings = reactive<Record<number, number | "">>({});
const form = reactive({ code: "", name: "", provider_type: "deepseek", base_url: "https://api.deepseek.com/v1", api_key: "", model_name: "", capabilities: ["chat"], status: "active", config: {} });

async function load() {
  [profiles.value, agents.value] = await Promise.all([api<any[]>("/api/v1/model-gateway/profiles"), api<any[]>("/api/v1/agents")]);
  for (const agent of agents.value) bindings[agent.id] = agent.llm_gateway_profile_id || "";
}
function open(item?: any) {
  editingId.value = item?.id || null;
  Object.assign(form, {
    code: item?.code || "", name: item?.name || "", provider_type: item?.provider_type || "deepseek",
    base_url: item?.base_url || "https://api.deepseek.com/v1", api_key: "", model_name: item?.model_name || "",
    capabilities: item?.capabilities || ["chat"], status: item?.status || "active", config: item?.config || {},
  });
  modal.value = true;
}
async function save() {
  const path = editingId.value ? `/api/v1/model-gateway/profiles/${editingId.value}` : "/api/v1/model-gateway/profiles";
  try { await api(path, { method: editingId.value ? "PUT" : "POST", body: JSON.stringify(form) }); modal.value = false; await load(); emit("toast", editingId.value ? "模型配置已更新" : "模型配置已创建"); }
  catch (error: any) { emit("toast", error.message, true); }
}
async function bind(agent: any) {
  const value = bindings[agent.id] === "" ? null : Number(bindings[agent.id]);
  try { await api(`/api/v1/agents/${agent.id}/model-profile`, { method: "PUT", body: JSON.stringify({ model_gateway_profile_id: value }) }); emit("toast", `${agent.name} 的模型路由已更新`); }
  catch (error: any) { emit("toast", error.message, true); }
}
onMounted(load);
</script>

<template>
  <div class="gateway-notice"><div class="notice-icon">◈</div><div><strong>模型请求统一从网关路由</strong><p>API Key 加密保存且不会回显；可为每个智能体绑定独立配置，未绑定时沿用服务器环境变量。</p></div><button class="primary" @click="open()">＋ 添加模型配置</button></div>
  <div class="section-head"><div><h2>模型配置档案</h2><p>按厂商、账号、模型或成本中心拆分配置</p></div><span class="badge success">{{ profiles.filter(item => item.status === 'active').length }} 个启用</span></div>
  <div v-if="profiles.length" class="gateway-grid">
    <article v-for="item in profiles" :key="item.id" class="card gateway-card">
      <div class="card-header"><div><span class="eyebrow">{{ item.provider_type }}</span><h2>{{ item.name }}</h2></div><span class="badge" :class="{ success: item.status === 'active' }">{{ item.status === 'active' ? '已启用' : '已停用' }}</span></div>
      <dl><div><dt>模型</dt><dd>{{ item.model_name }}</dd></div><div><dt>接口</dt><dd>{{ item.base_url }}</dd></div><div><dt>凭据</dt><dd>{{ item.has_api_key ? '已加密配置' : '无 API Key' }}</dd></div><div><dt>绑定</dt><dd>{{ item.agent_count }} 个智能体</dd></div></dl>
      <button class="secondary" @click="open(item)">编辑配置</button>
    </article>
  </div>
  <div v-else class="card empty"><strong>尚未建立模型配置</strong>添加厂商 API 后，再为智能体选择对应模型路由。</div>

  <div class="section-head gateway-binding-head"><div><h2>智能体模型路由</h2><p>一个配置可复用，也可以为关键智能体使用独立 API 账号</p></div></div>
  <div class="table-wrap"><table><thead><tr><th>智能体</th><th>类型</th><th>模型配置</th><th>操作</th></tr></thead><tbody><tr v-for="agent in agents" :key="agent.id"><td><strong>{{ agent.name }}</strong><br><small class="muted">{{ agent.code }}</small></td><td>{{ agent.agent_type }} / {{ agent.launch_mode }}</td><td><select v-model="bindings[agent.id]"><option value="">服务器默认配置</option><option v-for="profile in profiles.filter(item => item.status === 'active')" :key="profile.id" :value="profile.id">{{ profile.name }} · {{ profile.model_name }}</option></select></td><td><button class="secondary" @click="bind(agent)">保存路由</button></td></tr></tbody></table></div>

  <AppModal v-if="modal" :title="editingId ? '编辑模型配置' : '添加模型配置'" @close="modal=false">
    <form class="form-stack" @submit.prevent="save">
      <div class="form-grid"><label>配置编码<input v-model.trim="form.code" required pattern="[A-Z][A-Z0-9_]{1,63}" placeholder="DEEPSEEK_MAIN"></label><label>显示名称<input v-model.trim="form.name" required placeholder="DeepSeek 主账号"></label></div>
      <div class="form-grid"><label>厂商<select v-model="form.provider_type"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="azure_openai">Azure OpenAI</option><option value="qwen">通义千问</option><option value="ollama">Ollama / 本地</option><option value="custom">OpenAI 兼容接口</option></select></label><label>模型名称<input v-model.trim="form.model_name" required placeholder="deepseek-chat"></label></div>
      <label>API Base URL<input v-model.trim="form.base_url" required placeholder="https://api.example.com/v1"></label>
      <label>API Key<input v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="editingId ? '留空表示保持现有凭据' : '输入厂商 API Key'"><small class="muted">凭据使用服务器主密钥加密，页面和接口均不回显。</small></label>
      <label>状态<select v-model="form.status"><option value="active">启用</option><option value="disabled">停用</option></select></label>
      <button class="primary">保存模型配置</button>
    </form>
  </AppModal>
</template>

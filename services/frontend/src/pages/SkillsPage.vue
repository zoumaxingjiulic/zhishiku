<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const skills = ref<any[]>([]);
const selected = ref<any>(null);
const editing = ref(false);
const busy = ref(false);
const query = ref("");
const statusFilter = ref("all");
const options = reactive({ knowledgeBases: [] as any[], tools: [] as any[], agents: [] as any[], departments: [] as any[] });
const form = reactive(blankSkill());

function bindingChoices(source: any[], selectedIds: number[], fallbackLabel: string) {
  const knownIds = new Set(source.map((item) => item.id));
  return [
    ...source.filter((item) => item.available || selectedIds.includes(item.id)),
    ...selectedIds.filter((id) => !knownIds.has(id)).map((id) => ({
      id, label: `${fallbackLabel} #${id}`, available: false,
    })),
  ].map((item) => ({
    ...item,
    displayLabel: item.available ? item.label : `${item.label}（已停用/不可用）`,
  }));
}

const knowledgeBaseChoices = computed(() => bindingChoices(options.knowledgeBases, form.knowledge_base_ids, "知识库"));
const toolChoices = computed(() => bindingChoices(options.tools, form.tool_ids, "工具"));
const agentChoices = computed(() => bindingChoices(options.agents, form.agent_ids, "智能体"));
const departmentChoices = computed(() => bindingChoices(options.departments, form.department_ids, "部门"));

const filteredSkills = computed(() => skills.value.filter((item) => {
  const search = query.value.trim().toLowerCase();
  const matchesSearch = !search || `${item.code} ${item.name} ${item.description || ""}`.toLowerCase().includes(search);
  return matchesSearch && (statusFilter.value === "all" || item.status === statusFilter.value);
}));

function blankSkill() {
  return {
    id: null as number | null,
    code: "",
    name: "",
    description: "",
    instruction: "",
    input_schema_text: JSON.stringify({ type: "object", properties: {}, additionalProperties: false }, null, 2),
    trigger_examples_text: "",
    knowledge_base_ids: [] as number[],
    tool_ids: [] as number[],
    agent_ids: [] as number[],
    department_ids: [] as number[],
    status: "active",
    version: 1,
  };
}

function assignForm(item: any) {
  Object.assign(form, {
    ...blankSkill(),
    ...item,
    input_schema_text: JSON.stringify(item.input_schema || { type: "object", properties: {}, additionalProperties: false }, null, 2),
    trigger_examples_text: (item.trigger_examples || []).join("\n"),
  });
}

async function loadSkills() {
  skills.value = await api<any[]>("/api/v1/admin/skills");
}

async function loadOptions() {
  const [knowledgeBases, connectorResult, agents, departments] = await Promise.all([
    api<any[]>("/api/v1/knowledge-bases"),
    api<any>("/api/v1/connectors"),
    api<any[]>("/api/v1/studio/agents"),
    api<any[]>("/api/v1/departments"),
  ]);
  options.knowledgeBases = knowledgeBases.map((item) => ({
    id: item.id, label: item.name, available: item.status === undefined || item.status === "active",
  }));
  options.tools = (connectorResult.items || []).flatMap((connector: any) =>
    (connector.tools || []).map((tool: any) => ({
      id: tool.id,
      label: `${connector.name} / ${tool.title || tool.tool_name}`,
      available: (connector.status === undefined || connector.status === "active")
        && (tool.status === undefined || tool.status === "active")
        && tool.annotations?.readOnlyHint === true,
    })),
  );
  options.agents = agents.map((item) => ({
    id: item.id, label: item.name, available: item.status === "active"
      && item.launch_mode === "chat" && item.code !== "ENTERPRISE_ASSISTANT",
  }));
  options.departments = departments.map((item) => ({
    id: item.id, label: item.name, available: item.status === undefined || item.status === 1,
  }));
}

onMounted(async () => {
  try {
    await Promise.all([loadSkills(), loadOptions()]);
  } catch (error: any) {
    emit("toast", error.message, true);
  }
});

function startCreate() {
  selected.value = null;
  assignForm(blankSkill());
  editing.value = true;
}

async function openSkill(item: any) {
  try {
    selected.value = await api<any>(`/api/v1/admin/skills/${item.id}`);
    assignForm(selected.value);
    editing.value = true;
  } catch (error: any) {
    emit("toast", error.message, true);
  }
}

function payload() {
  let inputSchema: Record<string, any>;
  try {
    inputSchema = JSON.parse(form.input_schema_text);
  } catch {
    throw new Error("输入 Schema 必须是有效 JSON");
  }
  return {
    code: form.code,
    name: form.name,
    description: form.description || null,
    instruction: form.instruction,
    input_schema: inputSchema,
    trigger_examples: form.trigger_examples_text.split("\n").map((item) => item.trim()).filter(Boolean),
    knowledge_base_ids: form.knowledge_base_ids,
    tool_ids: form.tool_ids,
    agent_ids: form.agent_ids,
    department_ids: form.department_ids,
    status: form.status,
    version: form.version,
  };
}

async function save() {
  busy.value = true;
  try {
    const body = JSON.stringify(payload());
    const saved = form.id
      ? await api<any>(`/api/v1/admin/skills/${form.id}`, { method: "PUT", body })
      : await api<any>("/api/v1/admin/skills", { method: "POST", body });
    await loadSkills();
    selected.value = saved;
    assignForm(saved);
    emit("toast", "Skill 已保存");
  } catch (error: any) {
    emit("toast", error.message, true);
  } finally {
    busy.value = false;
  }
}

async function disable() {
  if (!form.id || !window.confirm(`确定停用 Skill“${form.name}”吗？历史任务仍会保留版本信息。`)) return;
  busy.value = true;
  try {
    const saved = await api<any>(`/api/v1/admin/skills/${form.id}?version=${form.version}`, { method: "DELETE" });
    await loadSkills();
    selected.value = saved;
    assignForm({ ...form, ...saved });
    emit("toast", "Skill 已停用");
  } catch (error: any) {
    emit("toast", error.message, true);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="section-head">
    <div><h2>能力配置</h2><p>Skill 是声明式业务能力，不执行任意代码、Shell 或原始 SQL。</p></div>
    <button class="primary" @click="startCreate">新建 Skill</button>
  </div>

  <div class="skills-layout">
    <section class="card skill-list">
      <div class="skill-filters">
        <input v-model="query" aria-label="筛选 Skill" placeholder="搜索名称或编码">
        <select v-model="statusFilter" aria-label="状态筛选">
          <option value="all">全部状态</option><option value="active">启用</option><option value="disabled">停用</option>
        </select>
      </div>
      <button v-for="item in filteredSkills" :key="item.id" class="skill-row" :class="{ active: form.id === item.id }" @click="openSkill(item)">
        <span><strong>{{ item.name }}</strong><small>{{ item.code }} · v{{ item.version }}</small></span>
        <span class="badge" :class="{ success: item.status === 'active' }">{{ item.status === "active" ? "启用" : "停用" }}</span>
      </button>
      <div v-if="!filteredSkills.length" class="empty">暂无 Skill</div>
    </section>

    <form v-if="editing" class="card form-stack skill-form" @submit.prevent="save">
      <div class="form-heading"><div><h3>{{ form.id ? "编辑 Skill" : "新建 Skill" }}</h3><p v-if="form.id">当前版本 v{{ form.version }}</p></div></div>
      <div class="form-grid">
        <label>编码<input v-model.trim="form.code" :disabled="Boolean(form.id)" required pattern="[A-Z][A-Z0-9_]+"></label>
        <label>名称<input v-model.trim="form.name" required></label>
      </div>
      <label>描述<textarea v-model.trim="form.description" rows="2"></textarea></label>
      <label>执行说明<textarea v-model="form.instruction" aria-label="执行说明" rows="6" required></textarea><small class="muted">仅描述流程与约束；服务端拒绝外部引用和可执行 Schema。</small></label>
      <label>输入 Schema<textarea v-model="form.input_schema_text" class="code-input" rows="9" spellcheck="false" required></textarea></label>
      <label>触发示例<textarea v-model="form.trigger_examples_text" rows="3" placeholder="每行一条自然语言示例"></textarea></label>
      <div class="binding-grid">
        <fieldset><legend>部门</legend><label v-for="item in departmentChoices" :key="item.id" class="check" :class="{ unavailable: !item.available }"><input v-model="form.department_ids" type="checkbox" :value="item.id">{{ item.displayLabel }}</label><small v-if="!departmentChoices.length">暂无可选项</small></fieldset>
        <fieldset><legend>知识库</legend><label v-for="item in knowledgeBaseChoices" :key="item.id" class="check" :class="{ unavailable: !item.available }"><input v-model="form.knowledge_base_ids" type="checkbox" :value="item.id">{{ item.displayLabel }}</label><small v-if="!knowledgeBaseChoices.length">暂无可选项</small></fieldset>
        <fieldset><legend>MCP 只读工具</legend><label v-for="item in toolChoices" :key="item.id" class="check" :class="{ unavailable: !item.available }"><input v-model="form.tool_ids" type="checkbox" :value="item.id">{{ item.displayLabel }}</label><small v-if="!toolChoices.length">暂无可选项</small></fieldset>
        <fieldset><legend>智能体</legend><label v-for="item in agentChoices" :key="item.id" class="check" :class="{ unavailable: !item.available }"><input v-model="form.agent_ids" type="checkbox" :value="item.id">{{ item.displayLabel }}</label><small v-if="!agentChoices.length">暂无可选项</small></fieldset>
      </div>
      <label>状态<select v-model="form.status"><option value="active">启用</option><option value="disabled">停用</option></select></label>
      <div class="actions"><button class="primary" :disabled="busy">{{ busy ? "保存中…" : "保存" }}</button><button v-if="form.id && form.status === 'active'" type="button" class="danger" :disabled="busy" @click="disable">停用</button></div>
    </form>
    <section v-else class="card empty">请选择一个 Skill，或新建声明式业务能力。</section>
  </div>
</template>

<style scoped>
.skills-layout{display:grid;grid-template-columns:minmax(260px,.8fr) minmax(0,2fr);gap:18px;align-items:start}.skill-list{padding:12px}.skill-filters{display:grid;grid-template-columns:1fr auto;gap:8px;margin-bottom:10px}.skill-row{width:100%;display:flex;justify-content:space-between;align-items:center;text-align:left;background:transparent;color:inherit;border:1px solid transparent;padding:12px;border-radius:10px}.skill-row:hover,.skill-row.active{background:var(--surface-soft,#f5f7fb);border-color:var(--border,#dfe4ee)}.skill-row span:first-child{display:grid;gap:3px}.skill-form{padding:20px}.form-heading{display:flex;justify-content:space-between}.form-heading p{margin:4px 0 0}.form-grid,.binding-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.binding-grid fieldset{border:1px solid var(--border,#dfe4ee);border-radius:10px;min-height:90px}.check{display:flex;flex-direction:row;gap:7px;align-items:center;font-weight:400}.check.unavailable{color:var(--muted,#6b7280)}.check input{width:auto}.code-input{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}@media(max-width:900px){.skills-layout,.form-grid,.binding-grid{grid-template-columns:1fr}}
</style>

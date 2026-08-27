<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const items = ref<any[]>([]);
const modal = ref(false);
const editingId = ref<number | null>(null);
const form = reactive({ name: "", description: "", content: "", variablesText: "" });

async function load() { items.value = await api<any[]>("/api/v1/prompt-templates"); }
function open(item?: any) {
  editingId.value = item?.id || null;
  Object.assign(form, {
    name: item?.name || "", description: item?.description || "", content: item?.content || "",
    variablesText: (item?.variables || []).join("、"),
  });
  modal.value = true;
}
async function save() {
  const variables = form.variablesText.split(/[，,、\s]+/).map(item => item.trim()).filter(Boolean);
  const path = editingId.value ? `/api/v1/prompt-templates/${editingId.value}` : "/api/v1/prompt-templates";
  try {
    await api(path, { method: editingId.value ? "PUT" : "POST", body: JSON.stringify({ ...form, variables }) });
    modal.value = false; await load(); emit("toast", editingId.value ? "模板已更新" : "模板已保存");
  } catch (error: any) { emit("toast", error.message, true); }
}
async function remove(item: any) {
  if (!window.confirm(`确定删除模板“${item.name}”吗？`)) return;
  try { await api(`/api/v1/prompt-templates/${item.id}`, { method: "DELETE" }); await load(); emit("toast", "模板已删除"); }
  catch (error: any) { emit("toast", error.message, true); }
}
async function copy(item: any) {
  await navigator.clipboard.writeText(item.content);
  emit("toast", "提示词已复制");
}
onMounted(load);
</script>

<template>
  <div class="section-head"><div><h2>我的提示词模板</h2><p>模板仅自己可见，适合沉淀高频任务和标准提问方式</p></div><button class="primary" @click="open()">＋ 新建模板</button></div>
  <div v-if="items.length" class="template-grid">
    <article v-for="item in items" :key="item.id" class="card template-card">
      <div class="card-header"><div><span class="eyebrow">MY PROMPT</span><h2>{{ item.name }}</h2></div><span class="badge">{{ item.variables.length }} 个变量</span></div>
      <p class="muted">{{ item.description || "未填写说明" }}</p>
      <pre>{{ item.content }}</pre>
      <div v-if="item.variables.length" class="tag-row"><span v-for="variable in item.variables" :key="variable">{{ variable }}</span></div>
      <div class="actions"><button class="primary" @click="copy(item)">复制使用</button><button class="secondary" @click="open(item)">编辑</button><button class="danger" @click="remove(item)">删除</button></div>
    </article>
  </div>
  <div v-else class="card empty"><strong>还没有提示词模板</strong>把反复使用的提示词保存下来，下次一键复制。</div>

  <AppModal v-if="modal" :title="editingId ? '编辑提示词模板' : '新建提示词模板'" @close="modal=false">
    <form class="form-stack" @submit.prevent="save">
      <label>模板名称<input v-model.trim="form.name" maxlength="128" required placeholder="例如：周报润色"></label>
      <label>用途说明<input v-model.trim="form.description" maxlength="512" placeholder="这个模板适用于什么场景"></label>
      <label>提示词内容<textarea v-model="form.content" class="prompt-editor" required placeholder="请将以下内容整理为……"></textarea></label>
      <label>变量（选填）<input v-model="form.variablesText" placeholder="主题、受众、字数；用逗号分隔"><small class="muted">用于标记模板中需要替换的内容。</small></label>
      <button class="primary">保存模板</button>
    </form>
  </AppModal>
</template>

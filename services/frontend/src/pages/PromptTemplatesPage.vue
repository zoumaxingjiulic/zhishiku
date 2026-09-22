<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";
import BaseButton from "../components/base/BaseButton.vue";
import BaseBadge from "../components/base/BaseBadge.vue";
import BaseCard from "../components/base/BaseCard.vue";
import BaseEmptyState from "../components/base/BaseEmptyState.vue";
import BaseIcon from "../components/base/BaseIcon.vue";
import BaseSkeleton from "../components/base/BaseSkeleton.vue";

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const items = ref<any[]>([]);
const modal = ref(false);
const loading = ref(true);
const loadError = ref("");
const editingId = ref<number | null>(null);
const form = reactive({ name: "", description: "", content: "", variablesText: "" });

async function load() { loading.value=true;loadError.value="";try{items.value = await api<any[]>("/api/v1/prompt-templates");}catch(error:any){loadError.value=error?.message||"提示词模板加载失败";emit("toast",loadError.value,true);}finally{loading.value=false;} }
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
  try { if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable"); await navigator.clipboard.writeText(item.content); emit("toast", "提示词已复制"); }
  catch { emit("toast", "无法自动复制，请手工选中模板内容并复制", true); }
}
onMounted(load);
</script>

<template>
  <div class="page-header"><div><h2>我的提示词模板</h2><p>模板仅自己可见，适合沉淀高频任务和标准提问方式</p></div></div>
  <div class="page-toolbar"><BaseButton @click="open()"><BaseIcon name="add" />新建模板</BaseButton></div>
  <BaseCard v-if="loading" class="content-card page-loading"><BaseSkeleton height="160px" /><BaseSkeleton height="160px" /></BaseCard>
  <BaseCard v-else-if="loadError" class="content-card"><BaseEmptyState title="提示词模板加载失败" :description="loadError" role="alert"><template #action><BaseButton variant="secondary" @click="load">重试</BaseButton></template></BaseEmptyState></BaseCard>
  <div v-else-if="items.length" class="template-grid">
    <BaseCard v-for="item in items" :key="item.id" class="template-card">
      <div class="card-header"><div><span class="eyebrow">MY PROMPT</span><h2>{{ item.name }}</h2></div><BaseBadge>{{ item.variables.length }} 个变量</BaseBadge></div>
      <p class="muted">{{ item.description || "未填写说明" }}</p>
      <pre>{{ item.content }}</pre>
      <div v-if="item.variables.length" class="tag-row"><span v-for="variable in item.variables" :key="variable">{{ variable }}</span></div>
      <div class="actions"><BaseButton @click="copy(item)"><BaseIcon name="copy" />复制使用</BaseButton><BaseButton variant="secondary" @click="open(item)">编辑</BaseButton><BaseButton variant="danger" @click="remove(item)"><BaseIcon name="trash" />删除</BaseButton></div>
    </BaseCard>
  </div>
  <BaseCard v-else class="content-card"><BaseEmptyState title="还没有提示词模板" description="把反复使用的提示词保存下来，下次一键复制。" /></BaseCard>

  <AppModal v-if="modal" :title="editingId ? '编辑提示词模板' : '新建提示词模板'" @close="modal=false">
    <form class="form-stack" @submit.prevent="save">
      <label>模板名称<input v-model.trim="form.name" maxlength="128" required placeholder="例如：周报润色"></label>
      <label>用途说明<input v-model.trim="form.description" maxlength="512" placeholder="这个模板适用于什么场景"></label>
      <label>提示词内容<textarea v-model="form.content" class="prompt-editor" required placeholder="请将以下内容整理为……"></textarea></label>
      <label>变量（选填）<input v-model="form.variablesText" placeholder="主题、受众、字数；用逗号分隔"><small class="muted">用于标记模板中需要替换的内容。</small></label>
      <BaseButton type="submit">保存模板</BaseButton>
    </form>
  </AppModal>
</template>

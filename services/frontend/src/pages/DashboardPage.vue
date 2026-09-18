<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import type { DashboardStats } from "../shared/types/dashboard";
const emit = defineEmits<{ navigate: [page: string] }>();
const stats = ref<DashboardStats>({
  knowledge_bases: 0,
  documents: 0,
  agents: 0,
  processing: 0,
  succeeded: 0,
  failed: 0,
});
onMounted(async () => {
  stats.value = await api<DashboardStats>("/api/v1/dashboard/stats");
});
</script>
<template>
  <div class="stats">
    <div class="stat"><small>可访问知识库</small><strong>{{ stats.knowledge_bases }}</strong><span>按部门授权</span></div>
    <div class="stat"><small>知识文档</small><strong>{{ stats.documents }}</strong><span>{{ stats.succeeded }} 份已入库</span></div>
    <div class="stat"><small>可用智能体</small><strong>{{ stats.agents }}</strong><span>权限自动继承</span></div>
    <div class="stat"><small>处理任务</small><strong>{{ stats.processing }}</strong><span>后台异步执行</span></div>
  </div>
  <div class="grid-2">
    <div class="card"><div class="card-header"><h2>知识处理链路</h2><span class="badge success">完整启用</span></div><div class="pipeline"><div><b>01</b>对象存储</div><div><b>02</b>解析/OCR</div><div><b>03</b>语义切片</div><div><b>04</b>向量索引</div><div><b>05</b>混合检索</div><div><b>06</b>模型重排</div></div></div>
    <div class="card"><div class="card-header"><h2>快速开始</h2></div><p class="muted">上传资料后自动完成解析、切片、向量化与全文索引。</p><div class="actions"><button class="primary" @click="emit('navigate','knowledge')">管理知识库</button><button class="secondary" @click="emit('navigate','agents')">开始问答</button></div></div>
  </div>
</template>

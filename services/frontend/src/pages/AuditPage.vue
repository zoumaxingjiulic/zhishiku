<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import { formatDate } from "../utils";
import BaseButton from "../components/base/BaseButton.vue";
import BaseCard from "../components/base/BaseCard.vue";
import BaseEmptyState from "../components/base/BaseEmptyState.vue";
import BaseSkeleton from "../components/base/BaseSkeleton.vue";
const logs = ref<any[]>([]);
const loading = ref(true), loadError = ref("");
async function load(){loading.value=true;loadError.value="";try{logs.value=await api<any[]>("/api/v1/audit-logs?limit=200");}catch(error:any){loadError.value=error?.message||"审计日志加载失败";}finally{loading.value=false;}}
onMounted(load);
</script>
<template><div class="page-header"><div><h2>审计日志</h2><p>追踪平台关键操作、资源和来源地址</p></div></div><div class="page-toolbar"><BaseButton variant="secondary" :loading="loading" loading-text="刷新中…" @click="load">刷新</BaseButton></div><BaseCard class="content-card" padding="none"><div v-if="loading" class="page-loading"><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /></div><BaseEmptyState v-else-if="loadError" title="审计日志加载失败" :description="loadError" role="alert"><template #action><BaseButton variant="secondary" @click="load">重试</BaseButton></template></BaseEmptyState><div v-else-if="logs.length" class="table-wrap"><table><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>来源 IP</th></tr></thead><tbody><tr v-for="item in logs" :key="item.id"><td>{{ formatDate(item.created_at) }}</td><td>{{ item.display_name || item.username || "系统" }}</td><td>{{ item.action }}</td><td>{{ item.resource_type }} #{{ item.resource_id || "—" }}</td><td>{{ item.ip_address || "—" }}</td></tr></tbody></table></div><BaseEmptyState v-else title="暂无日志" description="当前筛选范围内没有审计记录。" /></BaseCard></template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import { formatDate } from "../utils";
const logs = ref<any[]>([]);
onMounted(async () => { logs.value = await api<any[]>("/api/v1/audit-logs?limit=200"); });
</script>
<template><div class="card"><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>来源 IP</th></tr></thead><tbody><tr v-for="item in logs" :key="item.id"><td>{{ formatDate(item.created_at) }}</td><td>{{ item.display_name || item.username || "系统" }}</td><td>{{ item.action }}</td><td>{{ item.resource_type }} #{{ item.resource_id || "—" }}</td><td>{{ item.ip_address || "—" }}</td></tr><tr v-if="!logs.length"><td colspan="5"><div class="empty">暂无日志</div></td></tr></tbody></table></div></div></template>

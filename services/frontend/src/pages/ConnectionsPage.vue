<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
const planned = ref<any[]>([]);
onMounted(async () => { planned.value = (await api<any>("/api/v1/connectors")).planned_types || []; });
</script>
<template>
  <div class="section-head"><div><h2>企业系统连接</h2><p>连接器当前仅预留架构，不会主动访问生产系统</p></div></div>
  <div class="connector-grid"><div v-for="item in planned" :key="item.type" class="card connector"><span class="icon">{{ item.type === 'erp' ? '▦' : item.type === 'plm' ? '◇' : '⌁' }}</span><h2>{{ item.name }}</h2><p>{{ item.description }}</p><span class="coming">待配置接入</span></div></div>
  <div class="card empty" style="margin-top:18px"><strong>后续接入建议</strong>采用只读服务账号、字段级映射、增量同步、失败重试和完整审计；凭据不存放在普通配置表中。</div>
</template>

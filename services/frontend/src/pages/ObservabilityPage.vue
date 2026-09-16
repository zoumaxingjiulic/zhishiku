<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import { formatDate } from "../utils";

const runs = ref<any[]>([]);
const loading = ref(false);
onMounted(load);
async function load() {
  loading.value = true;
  try { runs.value = await api<any[]>("/api/v1/agent-runs?limit=100"); }
  finally { loading.value = false; }
}
function routeName(value: string) { return ({ rag: "知识检索", tool: "系统工具", hybrid: "知识 + 工具", chat: "模型对话" } as any)[value] || value; }
</script>

<template>
  <div class="section-head"><div><h2>智能体运行追踪</h2><p>查看检索、重排序、模型生成和 MCP 工具调用的运行结果与耗时</p></div><button class="secondary" :disabled="loading" @click="load">{{loading?'刷新中…':'刷新'}}</button></div>
  <div class="card"><div class="table-wrap"><table class="run-table"><thead><tr><th>开始时间</th><th>智能体 / 用户</th><th>路由</th><th>状态</th><th>候选</th><th>阶段耗时</th><th>工具事件</th><th>追踪 ID</th></tr></thead><tbody>
    <tr v-for="run in runs" :key="run.id"><td>{{formatDate(run.started_at)}}</td><td><strong>{{run.agent_name}}</strong><br><small>{{run.display_name}}</small></td><td>{{routeName(run.route)}}</td><td><span class="badge" :class="run.status==='succeeded'?'success':run.status==='failed'?'failed':'pending'">{{run.status==='succeeded'?'成功':run.status==='failed'?'失败':'运行中'}}</span><small v-if="run.error_type" class="error block">{{run.error_type}}</small></td><td>向量 {{run.candidate_counts?.vector||0}}<br>关键词 {{run.candidate_counts?.keyword||0}}<br>最终 {{run.candidate_counts?.final||0}}</td><td>检索 {{run.timings?.retrieve_ms||0}} ms<br>重排 {{run.timings?.rerank_ms||0}} ms<br>生成 {{run.timings?.generation_ms||0}} ms<br><strong>合计 {{run.timings?.total_ms||0}} ms</strong></td><td><span v-if="!run.tool_events?.length">—</span><div v-for="(event,index) in run.tool_events" :key="index"><span class="badge" :class="event.success?'success':'failed'">{{event.connector}} / {{event.tool}}</span><small class="block">{{event.duration_ms}} ms</small></div></td><td><code>{{run.id.slice(0,8)}}</code></td></tr>
    <tr v-if="!runs.length"><td colspan="8"><div class="empty">暂无运行记录</div></td></tr>
  </tbody></table></div></div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import { formatDate } from "../utils";
import BaseButton from "../components/base/BaseButton.vue";
import BaseBadge from "../components/base/BaseBadge.vue";
import BaseCard from "../components/base/BaseCard.vue";
import BaseEmptyState from "../components/base/BaseEmptyState.vue";
import BaseSkeleton from "../components/base/BaseSkeleton.vue";

const runs = ref<any[]>([]);
const loading = ref(false);
const loadError = ref("");
onMounted(load);
async function load() {
  loading.value = true;
  loadError.value = "";
  try { runs.value = await api<any[]>("/api/v1/agent-runs?limit=100"); }
  catch(error:any){loadError.value=error?.message||"运行记录加载失败";}
  finally { loading.value = false; }
}
function routeName(value: string) { return ({ rag: "知识检索", tool: "系统工具", hybrid: "知识 + 工具", chat: "模型对话" } as any)[value] || value; }
</script>

<template>
  <div class="page-header"><div><h2>智能体运行追踪</h2><p>查看检索、重排序、模型生成和 MCP 工具调用的运行结果与耗时</p></div></div><div class="page-toolbar"><BaseButton variant="secondary" :loading="loading" loading-text="刷新中…" @click="load">刷新</BaseButton></div>
  <BaseCard class="content-card" padding="none"><div v-if="loading" class="page-loading"><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /></div><BaseEmptyState v-else-if="loadError" title="运行记录加载失败" :description="loadError" role="alert"><template #action><BaseButton variant="secondary" @click="load">重试</BaseButton></template></BaseEmptyState><div v-else-if="runs.length" class="table-wrap"><table class="run-table"><thead><tr><th>开始时间</th><th>智能体 / 用户</th><th>路由</th><th>状态</th><th>候选</th><th>阶段耗时</th><th>工具事件</th><th>追踪 ID</th></tr></thead><tbody>
    <tr v-for="run in runs" :key="run.id"><td>{{formatDate(run.started_at)}}</td><td><strong>{{run.agent_name}}</strong><br><small>{{run.display_name}}</small></td><td>{{routeName(run.route)}}</td><td><BaseBadge :tone="run.status==='succeeded'?'success':run.status==='failed'?'danger':'warning'">{{run.status==='succeeded'?'成功':run.status==='failed'?'失败':'运行中'}}</BaseBadge><small v-if="run.error_type" class="error block">{{run.error_type}}</small></td><td>向量 {{run.candidate_counts?.vector||0}}<br>关键词 {{run.candidate_counts?.keyword||0}}<br>最终 {{run.candidate_counts?.final||0}}</td><td>检索 {{run.timings?.retrieve_ms||0}} ms<br>重排 {{run.timings?.rerank_ms||0}} ms<br>生成 {{run.timings?.generation_ms||0}} ms<br><strong>合计 {{run.timings?.total_ms||0}} ms</strong></td><td><span v-if="!run.tool_events?.length">—</span><div v-for="(event,index) in run.tool_events" :key="index"><BaseBadge :tone="event.success?'success':'danger'">{{event.connector}} / {{event.tool}}</BaseBadge><small class="block">{{event.duration_ms}} ms</small></div></td><td><code>{{run.id.slice(0,8)}}</code></td></tr>
  </tbody></table></div><BaseEmptyState v-else title="暂无运行记录" description="智能体开始运行后，追踪记录会显示在这里。" /></BaseCard>
</template>

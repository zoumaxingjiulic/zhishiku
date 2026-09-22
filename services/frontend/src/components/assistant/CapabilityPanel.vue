<script setup lang="ts">
import { computed } from "vue";
import BaseBadge from "../base/BaseBadge.vue";
import BaseEmptyState from "../base/BaseEmptyState.vue";
import BaseSkeleton from "../base/BaseSkeleton.vue";
import type { AssistantCapabilities, AssistantTask } from "../../shared/types/assistant";

const props = defineProps<{
  capabilities: AssistantCapabilities | null;
  task?: AssistantTask | null;
  loading?: boolean;
  error?: string;
}>();

const groups = computed(() => props.capabilities ? [
  { key: "knowledge", label: "知识库", items: props.capabilities.knowledge_bases },
  { key: "tool", label: "只读工具", items: props.capabilities.tools },
  { key: "agent", label: "专业智能体", items: props.capabilities.agents },
  { key: "skill", label: "Skill", items: props.capabilities.skills },
] : []);
const capabilityCount = computed(() => groups.value.reduce((sum, group) => sum + group.items.length, 0));
</script>

<template>
  <div class="capability-panel">
    <div class="capability-heading"><span class="assistant-eyebrow">AUTHORIZED</span><h2>可用能力</h2><p>仅展示当前账号已授权范围</p></div>
    <div v-if="loading" class="capability-loading"><BaseSkeleton v-for="item in 4" :key="item" height="46px" label="正在加载可用能力" /></div>
    <p v-else-if="error" class="capability-error">{{ error }}</p>
    <BaseEmptyState v-else-if="!capabilityCount" title="暂无已授权的企业能力" description="您仍可进行普通对话；如需企业数据，请联系管理员授权。" />
    <div v-else class="capability-groups">
      <section v-for="group in groups" :key="group.key">
        <div class="capability-group-title"><h3>{{ group.label }}</h3><BaseBadge>{{ group.items.length }}</BaseBadge></div>
        <ul><li v-for="item in group.items" :key="item.id"><strong>{{ item.name }}</strong><small>{{ item.description || item.code }}</small></li></ul>
      </section>
    </div>
    <section v-if="task" class="recent-task" aria-label="最近任务">
      <div><h3>最近任务</h3><BaseBadge :tone="task.status === 'failed' ? 'danger' : task.status === 'succeeded' ? 'success' : 'warning'">{{ task.status }}</BaseBadge></div>
      <p>{{ task.stage || "等待处理" }}</p>
      <small v-if="task.updated_at">更新于 {{ task.updated_at }}</small>
    </section>
    <section class="assistant-system-status"><span aria-hidden="true" /><div><strong>系统服务正常</strong><small>任务在后台安全执行</small></div></section>
  </div>
</template>

<style scoped>
.capability-panel { height: 100%; display: flex; flex-direction: column; gap: var(--space-5); }
.capability-heading h2 { margin: 3px 0 var(--space-1); font-size: 1rem; }
.capability-heading p { margin: 0; color: var(--color-text-muted); font-size: .75rem; }
.assistant-eyebrow { color: var(--color-primary); font-size: .625rem; font-weight: 800; letter-spacing: .12em; }
.capability-loading { display: grid; gap: var(--space-3); }
.capability-error { border-radius: var(--radius-sm); padding: var(--space-3); background: var(--color-danger-soft); color: var(--color-danger); font-size: .8125rem; }
.capability-groups { min-height: 0; overflow: auto; display: grid; align-content: start; gap: var(--space-5); }
.capability-group-title, .recent-task > div { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.capability-group-title h3, .recent-task h3 { margin: 0; font-size: .8125rem; }
.capability-groups ul { display: grid; gap: var(--space-2); margin: var(--space-2) 0 0; padding: 0; list-style: none; }
.capability-groups li { display: grid; gap: 3px; padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--color-surface-muted); }
.capability-groups strong { font-size: .75rem; }
.capability-groups small, .recent-task small { overflow: hidden; color: var(--color-text-muted); font-size: .6875rem; text-overflow: ellipsis; white-space: nowrap; }
.recent-task { display: grid; gap: var(--space-2); border-top: 1px solid var(--color-border); padding-top: var(--space-4); }
.recent-task p { margin: 0; color: var(--color-text-muted); font-size: .75rem; }
.assistant-system-status { display: flex; align-items: center; gap: var(--space-2); margin-top: auto; border-top: 1px solid var(--color-border); padding-top: var(--space-4); }
.assistant-system-status > span { width: 8px; height: 8px; border-radius: 50%; background: var(--color-success); }
.assistant-system-status strong, .assistant-system-status small { display: block; }
.assistant-system-status strong { font-size: .75rem; }.assistant-system-status small { margin-top: 2px; color: var(--color-text-muted); font-size: .625rem; }
</style>

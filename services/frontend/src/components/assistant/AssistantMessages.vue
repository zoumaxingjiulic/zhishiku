<script setup lang="ts">
import BaseBadge from "../base/BaseBadge.vue";
import BaseEmptyState from "../base/BaseEmptyState.vue";
import BaseIcon from "../base/BaseIcon.vue";
import type { AssistantCapabilities, AssistantMessage, AssistantTask, CapabilityRef, ExecutionSummary } from "../../shared/types/assistant";

const props = defineProps<{
  messages: AssistantMessage[];
  task: AssistantTask | null;
  capabilities: AssistantCapabilities | null;
}>();

const intentNames: Record<string, string> = {
  general_chat: "普通对话", knowledge_query: "知识查询", system_query: "系统查询",
  agent_task: "智能体任务", multi_capability: "多能力协作", clarification: "需要澄清", forbidden: "受限请求",
};

function selected(source: CapabilityRef[] | undefined, ids: number[] | undefined) {
  if (!source || !ids) return [];
  const wanted = new Set(ids);
  return source.filter(item => wanted.has(item.id));
}

function summaryFor(message: AssistantMessage): ExecutionSummary | null {
  if (message.execution_summary) return message.execution_summary;
  if (props.task?.assistant_message_id === message.id) return props.task.execution_summary ?? null;
  return null;
}

function selectedCapabilities(message: AssistantMessage) {
  const selection = summaryFor(message)?.selection;
  if (!props.capabilities || !selection) return [];
  return [
    ...selected(props.capabilities.knowledge_bases, selection.knowledge_base_ids).map(item => ({ ...item, kind: "知识库" })),
    ...selected(props.capabilities.tools, selection.tool_ids).map(item => ({ ...item, kind: "工具" })),
    ...selected(props.capabilities.agents, selection.agent_ids).map(item => ({ ...item, kind: "智能体" })),
    ...selected(props.capabilities.skills, selection.skill_ids).map(item => ({ ...item, kind: "Skill" })),
  ];
}

function pageLabel(page?: number | null, pageEnd?: number | null) {
  if (!page) return "";
  return pageEnd && pageEnd !== page ? `（第 ${page}–${pageEnd} 页）` : `（第 ${page} 页）`;
}
</script>

<template>
  <div class="assistant-messages" aria-live="polite">
    <BaseEmptyState v-if="!messages.length" title="从一个问题开始" description="查询制度、企业系统数据，或让专业智能体协助完成复杂任务。">
      <template #icon><BaseIcon name="sparkles" :size="30" /></template>
    </BaseEmptyState>
    <article v-for="message in messages" v-else :key="message.id" class="assistant-message" :class="message.role">
      <div class="assistant-message-avatar" aria-hidden="true">{{ message.role === 'user' ? '我' : '智' }}</div>
      <div class="assistant-message-body">
        <div class="assistant-message-copy">{{ message.content }}</div>
        <span v-if="message.optimistic" class="assistant-saving">正在保存…</span>
        <div v-if="selectedCapabilities(message).length" class="assistant-capability-tags" aria-label="本次使用的能力">
          <BaseBadge v-for="item in selectedCapabilities(message)" :key="`${item.kind}-${item.id}`" tone="info" :title="item.kind">{{ item.name }}</BaseBadge>
        </div>
        <section v-if="message.citations?.length" class="assistant-citations" aria-label="引用来源">
          <strong>引用来源</strong>
          <ul><li v-for="citation in message.citations" :key="`${citation.document_id}-${citation.page}`"><a :href="`/api/v1/documents/${citation.document_id}/download`">《{{ citation.title || `文档 ${citation.document_id}` }}》</a>{{ pageLabel(citation.page, citation.page_end) }}</li></ul>
        </section>
        <details v-if="message.role === 'assistant' && (summaryFor(message) || message.tool_calls?.length)" class="assistant-timeline">
          <summary>执行时间线</summary>
          <ol>
            <li v-if="summaryFor(message)"><strong>意图识别</strong><span>{{ intentNames[summaryFor(message)!.intent_type] || summaryFor(message)!.intent_type }}<template v-if="summaryFor(message)!.confidence != null"> · {{ Math.round(summaryFor(message)!.confidence! * 100) }}%</template></span><small v-if="summaryFor(message)!.reason">{{ summaryFor(message)!.reason }}</small></li>
            <li v-for="(tool, index) in message.tool_calls" :key="index"><strong>{{ tool.success === false ? '工具调用失败' : '工具调用' }}</strong><span>{{ tool.connector_name || tool.connector || '企业系统' }} / {{ tool.tool || '只读查询' }}</span><small v-if="tool.called_at">{{ tool.called_at }}</small><small v-if="tool.argument_keys?.length">参数字段：{{ tool.argument_keys.join('、') }}</small><small v-if="tool.error">{{ tool.error }}</small></li>
            <li v-if="task?.assistant_message_id === message.id && task?.stage"><strong>任务状态</strong><span>{{ task.stage }}</span></li>
          </ol>
        </details>
      </div>
    </article>
    <div v-if="task && ['queued','running','cancel_requested'].includes(task.status)" class="assistant-progress" role="status">
      <span class="assistant-progress-dot" aria-hidden="true" /><div><strong>{{ task.status === 'cancel_requested' ? '正在停止' : '总助手正在处理' }}</strong><small>{{ task.stage || '正在准备能力' }}</small></div>
    </div>
    <div v-if="task?.status === 'failed'" class="assistant-task-error" role="alert">任务执行失败{{ task.error_code ? `：${task.error_code}` : '' }}</div>
  </div>
</template>

<style scoped>
.assistant-messages { min-height: 0; flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: var(--space-5); padding: var(--space-6); background: linear-gradient(180deg, #f9fbff, var(--color-surface)); }
.assistant-message { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: start; gap: var(--space-3); width: min(780px, 92%); }
.assistant-message.user { align-self: flex-end; grid-template-columns: minmax(0, 1fr) 34px; }
.assistant-message.user .assistant-message-avatar { grid-column: 2; }.assistant-message.user .assistant-message-body { grid-column: 1; grid-row: 1; justify-self: end; background: var(--color-primary); color: var(--color-on-primary); }
.assistant-message-avatar { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; background: var(--color-nav); color: white; font-size: .75rem; font-weight: 800; }
.assistant-message-body { min-width: 0; border: 1px solid var(--color-border); border-radius: 4px var(--radius-lg) var(--radius-lg); padding: var(--space-4); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.assistant-message-copy { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.assistant-saving { display: block; margin-top: var(--space-2); opacity: .72; font-size: .6875rem; }
.assistant-capability-tags { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-3); }
.assistant-citations { margin-top: var(--space-4); border-top: 1px solid var(--color-border); padding-top: var(--space-3); color: var(--color-text-muted); font-size: .75rem; }
.assistant-citations > strong { color: var(--color-text); }.assistant-citations ul { display: grid; gap: var(--space-1); margin: var(--space-2) 0 0; padding-left: var(--space-5); }.assistant-citations a { color: var(--color-primary-strong); }
.assistant-timeline { margin-top: var(--space-3); border-top: 1px solid var(--color-border); padding-top: var(--space-3); color: var(--color-text-muted); font-size: .75rem; }
.assistant-timeline summary { cursor: pointer; color: var(--color-primary-strong); font-weight: 700; }.assistant-timeline ol { display: grid; gap: var(--space-3); margin: var(--space-3) 0 0; padding-left: var(--space-5); }.assistant-timeline li strong, .assistant-timeline li span, .assistant-timeline li small { display: block; }.assistant-timeline li span { margin-top: 2px; color: var(--color-text); }.assistant-timeline li small { margin-top: 2px; }
.assistant-progress { align-self: flex-start; display: flex; align-items: center; gap: var(--space-3); margin-left: 46px; border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); background: var(--color-primary-soft); color: var(--color-primary-strong); }.assistant-progress strong, .assistant-progress small { display: block; }.assistant-progress small { margin-top: 2px; font-size: .6875rem; }.assistant-progress-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--color-primary); animation: assistant-pulse 1.2s infinite alternate; }
.assistant-task-error { align-self: center; border-radius: var(--radius-sm); padding: var(--space-3) var(--space-4); background: var(--color-danger-soft); color: var(--color-danger); }
@keyframes assistant-pulse { to { opacity: .35; transform: scale(.8); } }
@media (prefers-reduced-motion: reduce) { .assistant-progress-dot { animation: none; } }
</style>

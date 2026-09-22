<script setup lang="ts">
import { ref } from "vue";
import BaseButton from "../base/BaseButton.vue";

const props = defineProps<{ awaiting: boolean; disabled?: boolean }>();
const emit = defineEmits<{ send: [question: string]; stop: [] }>();
const question = ref("");
const quickQuestions = ["年假怎么申请？", "查询物料可用库存", "帮我梳理这份制度的重点"];

function submit(value = question.value) {
  const content = value.trim();
  if (!content || props.awaiting || props.disabled) return;
  emit("send", content);
  question.value = "";
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
}
</script>

<template>
  <div class="assistant-composer">
    <div class="assistant-quick-questions" aria-label="常用问题">
      <button v-for="item in quickQuestions" :key="item" type="button" :disabled="awaiting || disabled" @click="submit(item)">{{ item }}</button>
    </div>
    <form class="assistant-compose-form" @submit.prevent="submit()">
      <label>
        <span class="sr-only">向企业总助手提问</span>
        <textarea v-model="question" rows="2" maxlength="4000" placeholder="输入问题，Enter 发送，Shift + Enter 换行" :disabled="disabled" @keydown="onKeydown" />
      </label>
      <BaseButton v-if="awaiting" variant="danger" @click="emit('stop')">停止</BaseButton>
      <BaseButton v-else type="submit" :disabled="disabled || !question.trim()">发送</BaseButton>
    </form>
    <small>总助手只会使用您已获授权的知识与只读企业能力。</small>
  </div>
</template>

<style scoped>
.assistant-composer { display: grid; gap: var(--space-3); padding: var(--space-4) var(--space-5); border-top: 1px solid var(--color-border); background: var(--color-surface); }
.assistant-quick-questions { display: flex; gap: var(--space-2); overflow-x: auto; padding-bottom: 2px; }
.assistant-quick-questions button { flex: 0 0 auto; border: 1px solid var(--color-border); border-radius: 999px; padding: 7px 11px; background: var(--color-surface-muted); color: var(--color-text-muted); font-size: .75rem; white-space: nowrap; }
.assistant-compose-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: var(--space-3); }
.assistant-compose-form textarea { min-height: 60px; resize: vertical; }
.assistant-composer > small { color: var(--color-text-muted); font-size: .6875rem; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
@media (max-width: 640px) { .assistant-compose-form { grid-template-columns: 1fr; } }
</style>

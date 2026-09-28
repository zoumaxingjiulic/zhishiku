<script setup lang="ts">
import { computed } from "vue";
import { renderAssistantMarkdown } from "../../shared/markdown";

const props = defineProps<{ content: string }>();
const renderedContent = computed(() => renderAssistantMarkdown(props.content));
</script>

<template>
  <div class="markdown-content" v-html="renderedContent" />
</template>

<style scoped>
.markdown-content {
  max-width: 100%;
  overflow-x: auto;
  overflow-wrap: anywhere;
  line-height: 1.7;
}

.markdown-content :deep(:first-child) { margin-top: 0; }
.markdown-content :deep(:last-child) { margin-bottom: 0; }
.markdown-content :deep(p) { margin: 0 0 .75em; }
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) { margin: 1em 0 .55em; line-height: 1.35; }
.markdown-content :deep(h1) { font-size: 1.3em; }
.markdown-content :deep(h2) { font-size: 1.2em; }
.markdown-content :deep(h3) { font-size: 1.1em; }
.markdown-content :deep(ul),
.markdown-content :deep(ol) { margin: .6em 0 .8em; padding-left: 1.6em; }
.markdown-content :deep(li + li) { margin-top: .25em; }
.markdown-content :deep(blockquote) { margin: .8em 0; border-left: 3px solid var(--color-primary); padding: .25em 0 .25em 1em; color: var(--color-text-muted); }
.markdown-content :deep(a) { color: var(--color-primary-strong); text-decoration: underline; text-underline-offset: 2px; }
.markdown-content :deep(code) { border-radius: 4px; padding: .12em .35em; background: var(--color-background); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; }
.markdown-content :deep(pre) { max-width: 100%; overflow-x: auto; margin: .8em 0; border-radius: var(--radius-md); padding: var(--space-4); background: #10233f; color: #eef5ff; }
.markdown-content :deep(pre code) { padding: 0; background: transparent; color: inherit; }
.markdown-content :deep(table) { width: 100%; min-width: 420px; margin: .8em 0; border-collapse: collapse; font-size: .92em; }
.markdown-content :deep(th),
.markdown-content :deep(td) { border: 1px solid var(--color-border); padding: .55em .7em; text-align: left; vertical-align: top; }
.markdown-content :deep(th) { background: var(--color-background); font-weight: 700; }
.markdown-content :deep(tbody tr:nth-child(even)) { background: color-mix(in srgb, var(--color-background) 62%, transparent); }
.markdown-content :deep(hr) { margin: 1em 0; border: 0; border-top: 1px solid var(--color-border); }
</style>

<script setup lang="ts">
withDefaults(defineProps<{
  tone?: "neutral" | "info" | "success" | "warning" | "danger";
}>(), {
  tone: "neutral",
});
</script>

<template>
  <span class="base-badge" :class="`base-badge--${tone}`"><slot /></span>
</template>

<style scoped>
.base-badge {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  border-radius: 999px;
  padding: var(--space-1) var(--space-2);
  background: var(--color-neutral-soft);
  color: var(--color-text-muted);
  font-size: .75rem;
  font-weight: 700;
  white-space: nowrap;
}
.base-badge--info { background: var(--color-primary-soft); color: var(--color-primary-strong); }
.base-badge--success { background: var(--color-success-soft); color: var(--color-success); }
.base-badge--warning { background: var(--color-warning-soft); color: var(--color-warning); }
.base-badge--danger { background: var(--color-danger-soft); color: var(--color-danger); }
</style>

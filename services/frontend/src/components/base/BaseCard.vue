<script setup lang="ts">
withDefaults(defineProps<{
  as?: string;
  padding?: "none" | "sm" | "md";
}>(), {
  as: "section",
  padding: "md",
});
</script>

<template>
  <component :is="as" class="base-card" :class="`base-card--${padding}`">
    <slot />
  </component>
</template>

<style scoped>
.base-card {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-sm);
}
.base-card--sm { padding: var(--space-4); }
.base-card--md { padding: var(--space-6); }
</style>

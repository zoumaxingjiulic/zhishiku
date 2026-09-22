<script setup lang="ts">
defineProps<{
  description?: string;
  title: string;
}>();
</script>

<template>
  <section class="base-empty-state">
    <div v-if="$slots.icon" class="base-empty-state__icon"><slot name="icon" /></div>
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.action" class="base-empty-state__action"><slot name="action" /></div>
  </section>
</template>

<style scoped>
.base-empty-state {
  display: grid;
  justify-items: center;
  gap: var(--space-2);
  padding: var(--space-8) var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}
.base-empty-state__icon { display: grid; place-items: center; color: var(--color-primary); font-size: 2rem; }
h3 { margin: 0; color: var(--color-text); font-size: 1.0625rem; }
p { max-width: 36rem; margin: 0; line-height: 1.6; }
.base-empty-state__action { margin-top: var(--space-3); }
</style>

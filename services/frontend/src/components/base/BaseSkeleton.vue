<script setup lang="ts">
withDefaults(defineProps<{
  height?: string;
  label?: string;
  rounded?: boolean;
  width?: string;
}>(), {
  height: "1rem",
  label: "正在加载",
  rounded: false,
  width: "100%",
});
</script>

<template>
  <div class="base-skeleton" role="status" :aria-label="label">
    <span
      class="base-skeleton__shape"
      :class="{ 'base-skeleton__shape--rounded': rounded }"
      :style="{ width, height }"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.base-skeleton { display: block; max-width: 100%; }
.base-skeleton__shape {
  display: block;
  max-width: 100%;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--color-neutral-soft), var(--color-surface-muted), var(--color-neutral-soft));
  background-size: 200% 100%;
  animation: base-skeleton-pulse 1.4s ease-in-out infinite;
}
.base-skeleton__shape--rounded { border-radius: 999px; }
@keyframes base-skeleton-pulse { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .base-skeleton__shape { animation: none; } }
</style>

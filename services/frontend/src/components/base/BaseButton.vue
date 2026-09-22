<script setup lang="ts">
const props = withDefaults(defineProps<{
  disabled?: boolean;
  loading?: boolean;
  loadingText?: string;
  size?: "sm" | "md";
  type?: "button" | "submit" | "reset";
  variant?: "primary" | "secondary" | "ghost" | "ghost-inverse" | "danger";
}>(), {
  disabled: false,
  loading: false,
  loadingText: "处理中…",
  size: "md",
  type: "button",
  variant: "primary",
});

const emit = defineEmits<{ click: [event: MouseEvent] }>();

function handleClick(event: MouseEvent) {
  if (props.disabled || props.loading) {
    event.preventDefault();
    return;
  }
  emit("click", event);
}
</script>

<template>
  <button
    class="base-button"
    :class="[`base-button--${variant}`, `base-button--${size}`]"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    @click="handleClick"
  >
    <span v-if="loading" class="base-button__spinner" aria-hidden="true" />
    <span v-if="loading">{{ loadingText }}</span>
    <slot v-else />
  </button>
</template>

<style scoped>
.base-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
}
.base-button--md { min-height: 40px; padding: var(--space-2) var(--space-4); }
.base-button--sm { min-height: 32px; padding: var(--space-1) var(--space-3); font-size: .8125rem; }
.base-button--primary { background: var(--color-primary); color: var(--color-on-primary); }
.base-button--primary:not(:disabled):hover { background: var(--color-primary-hover); }
.base-button--secondary { background: var(--color-primary-soft); color: var(--color-primary-strong); }
.base-button--ghost { background: transparent; color: var(--color-text-muted); }
.base-button--ghost-inverse { background: transparent; color: #dbe7f6; }
.base-button--ghost-inverse:not(:disabled):hover { background: rgba(255, 255, 255, .12); color: #fff; }
.base-button--danger { background: var(--color-danger-soft); color: var(--color-danger); }
.base-button:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
.base-button:disabled { cursor: not-allowed; opacity: .64; }
.base-button__spinner {
  width: 1em;
  height: 1em;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: base-button-spin .75s linear infinite;
}
@keyframes base-button-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .base-button__spinner { animation-duration: 1.5s; } }
</style>

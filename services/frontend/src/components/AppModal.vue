<script setup lang="ts">
defineProps<{ title: string }>();
defineEmits<{ close: [] }>();
</script>
<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <section class="modal-card vue-modal">
      <header><h2>{{ title }}</h2><button class="icon-button" type="button" @click="$emit('close')">×</button></header>
      <slot />
    </section>
  </div>
</template>

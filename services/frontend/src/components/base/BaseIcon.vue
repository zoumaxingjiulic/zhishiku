<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{
  name: string;
  size?: number | string;
  title?: string;
}>(), {
  size: 20,
  title: undefined,
});

const paths: Record<string, string> = {
  add: "M12 5v14M5 12h14",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  "arrow-left": "m15 18-6-6 6-6M9 12h12",
  "arrow-right": "m9 18 6-6-6-6M15 12H3",
  bot: "M12 8V4m-2 0h4M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Zm2 5h.01M16 13h.01M9 17h6",
  check: "m5 12 4 4L19 6",
  "chevron-down": "m6 9 6 6 6-6",
  "chevron-up": "m18 15-6-6-6 6",
  close: "m6 6 12 12M18 6 6 18",
  copy: "M9 9h11v11H9zM4 15H3V4h11v1",
  cpu: "M9 9h6v6H9zM9 1v3m6-3v3M9 20v3m6-3v3M20 9h3m-3 6h3M1 9h3m-3 6h3M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  database: "M4 6c0 2 3.6 3 8 3s8-1 8-3-3.6-3-8-3-8 1-8 3Zm0 0v6c0 2 3.6 3 8 3s8-1 8-3V6M4 12v6c0 2 3.6 3 8 3s8-1 8-3v-6",
  download: "M12 4v12m-5-5 5 5 5-5M4 19h16",
  file: "M6 2h8l4 4v16H6zM14 2v5h5M9 12h6m-6 4h6",
  folder: "M3 6h7l2 2h9v11H3z",
  "folder-open": "M3 7h7l2 2h9l-3 10H4L3 7Zm1 3h16",
  form: "M5 3h14v18H5zM8 7h8M8 11h8M8 15h5",
  home: "m3 11 9-8 9 8v10h-6v-6H9v6H3z",
  key: "M21 2 13.6 9.4M15 6l3 3M10.5 8a6 6 0 1 0 5.5 5.5",
  link: "M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1M14 11a5 5 0 0 0-7.1 0l-2 2A5 5 0 0 0 12 20.1l1.1-1.1",
  logout: "M10 17l5-5-5-5m5 5H3m12-9h5v18h-5",
  menu: "M4 6h16M4 12h16M4 18h16",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  request: "M5 4h14v16H5zM8 8h8m-8 4h8m-8 4h5",
  shield: "M12 3 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6zM9 12l2 2 4-5",
  send: "m3 11 18-8-8 18-2-8-8-2Zm8 2 4-4",
  sparkles: "m12 3 1.3 3.7L17 8l-3.7 1.3L12 13l-1.3-3.7L7 8l3.7-1.3ZM5 14l.9 2.1L8 17l-2.1.9L5 20l-.9-2.1L2 17l2.1-.9Z",
  stop: "M6 6h12v12H6z",
  studio: "M4 4h16v12H4zM8 20h8M12 16v4M8 8h8m-8 4h5",
  trash: "M4 7h16M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6m4-6v6",
  upload: "M12 16V4m-5 5 5-5 5 5M4 15v5h16v-5",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7-1a4 4 0 0 1 0 7m5 4v-2a4 4 0 0 0-3-3.9",
  warning: "M12 3 2 21h20L12 3Zm0 6v5m0 3h.01",
  workflow: "M5 5h5v5H5zM14 14h5v5h-5zM10 7h4a3 3 0 0 1 3 3v4M7 10v4a3 3 0 0 0 3 3h4",
};

const iconPath = computed(() => paths[props.name] ?? paths.sparkles);
const dimension = computed(() => typeof props.size === "number" ? `${props.size}px` : props.size);
</script>

<template>
  <svg
    class="base-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    :width="dimension"
    :height="dimension"
    :role="title ? 'img' : undefined"
    :aria-label="title"
    :aria-hidden="title ? undefined : 'true'"
  >
    <title v-if="title">{{ title }}</title>
    <path :d="iconPath" />
  </svg>
</template>

<style scoped>
.base-icon { display: inline-block; flex: 0 0 auto; vertical-align: middle; }
</style>

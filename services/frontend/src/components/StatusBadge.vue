<script setup lang="ts">
import { computed } from "vue";
import { statusMap } from "../utils";
const props = defineProps<{ status?: string | number | null }>();
const item = computed(() => statusMap[String(props.status ?? "")] || [String(props.status || "未知"), ""]);
</script>
<template><span class="badge" :class="item[1]">{{ item[0] }}</span></template>

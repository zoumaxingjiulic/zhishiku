<script setup lang="ts">
import { computed } from "vue";
import { statusMap } from "../utils";
import BaseBadge from "./base/BaseBadge.vue";
const props = defineProps<{ status?: string | number | null }>();
const item = computed(() => statusMap[String(props.status ?? "")] || [String(props.status || "未知"), ""]);
const tone = computed(() => ({ success: "success", pending: "warning", failed: "danger" } as const)[item.value[1] as "success" | "pending" | "failed"] || "neutral");
</script>
<template><BaseBadge :tone="tone">{{ item[0] }}</BaseBadge></template>

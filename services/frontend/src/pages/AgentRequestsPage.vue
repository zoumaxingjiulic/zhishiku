<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";

const props = defineProps<{ user: any }>();
const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const items = ref<any[]>([]);
const modal = ref(false);
const reviewModal = ref(false);
const selected = ref<any>(null);
const isAdmin = computed(() => Boolean(props.user?.is_platform_admin));
const form = reactive({ department_id: 0, title: "", business_problem: "", expected_outcome: "", dataSourcesText: "", frequency: "按需", urgency: "normal" });
const review = reactive({ status: "reviewing", admin_comment: "" });
const statusText: Record<string, string> = { submitted: "待评审", reviewing: "评审中", approved: "已批准", rejected: "未采纳", delivered: "已交付", closed: "已关闭" };

async function load() { items.value = await api<any[]>("/api/v1/agent-requests"); }
function openCreate() {
  Object.assign(form, { department_id: props.user?.departments?.[0]?.id || 0, title: "", business_problem: "", expected_outcome: "", dataSourcesText: "", frequency: "按需", urgency: "normal" });
  modal.value = true;
}
async function submit() {
  const data_sources = form.dataSourcesText.split(/[，,、\n]+/).map(item => item.trim()).filter(Boolean);
  try {
    const result = await api<any>("/api/v1/agent-requests", { method: "POST", body: JSON.stringify({ ...form, data_sources }) });
    modal.value = false; await load(); emit("toast", `申请已提交：${result.request_no}`);
  } catch (error: any) { emit("toast", error.message, true); }
}
function openReview(item: any) { selected.value = item; review.status = item.status === "submitted" ? "reviewing" : item.status; review.admin_comment = item.admin_comment || ""; reviewModal.value = true; }
async function saveReview() {
  try { await api(`/api/v1/agent-requests/${selected.value.id}`, { method: "PATCH", body: JSON.stringify(review) }); reviewModal.value = false; await load(); emit("toast", "评审状态已更新"); }
  catch (error: any) { emit("toast", error.message, true); }
}
onMounted(load);
</script>

<template>
  <div class="section-head"><div><h2>{{ isAdmin ? "智能体需求池" : "我的智能体申请" }}</h2><p>{{ isAdmin ? "查看全公司的智能体需求并推进评审交付" : "说明业务问题、数据来源和期望结果，平台管理员会统一评估" }}</p></div><button class="primary" @click="openCreate">＋ 提交申请</button></div>
  <div v-if="items.length" class="request-list">
    <article v-for="item in items" :key="item.id" class="card request-card">
      <div class="request-main"><div class="request-title"><span class="request-no">{{ item.request_no }}</span><h2>{{ item.title }}</h2><span class="badge" :class="{ success: ['approved','delivered'].includes(item.status), pending: ['submitted','reviewing'].includes(item.status), failed: item.status === 'rejected' }">{{ statusText[item.status] || item.status }}</span></div><p>{{ item.business_problem }}</p><small>{{ item.department_name }} · {{ item.applicant_name }} · {{ new Date(item.created_at).toLocaleDateString() }}</small></div>
      <div class="request-result"><small>期望结果</small><p>{{ item.expected_outcome }}</p><div class="tag-row"><span v-for="source in item.data_sources" :key="source">{{ source }}</span></div><button v-if="isAdmin" class="secondary" @click="openReview(item)">评审申请</button></div>
    </article>
  </div>
  <div v-else class="card empty"><strong>暂无智能体申请</strong>从一个明确、可衡量的业务问题开始。</div>

  <AppModal v-if="modal" title="提交智能体申请" @close="modal=false">
    <form class="form-stack" @submit.prevent="submit">
      <label>申请部门<select v-model.number="form.department_id" required><option v-for="department in user.departments" :key="department.id" :value="department.id">{{ department.name }}</option></select></label>
      <label>需求名称<input v-model.trim="form.title" required placeholder="例如：采购订单异常检查助手"></label>
      <label>当前业务问题<textarea v-model="form.business_problem" required placeholder="请描述现在由谁、用什么方式处理，主要痛点是什么"></textarea></label>
      <label>期望结果<textarea v-model="form.expected_outcome" required placeholder="希望智能体完成哪些动作，输出什么结果"></textarea></label>
      <label>可能涉及的数据来源<input v-model="form.dataSourcesText" placeholder="ERP、PLM、Excel、知识库；用逗号分隔"></label>
      <div class="form-grid"><label>使用频率<select v-model="form.frequency"><option>按需</option><option>每天</option><option>每周</option><option>事件触发</option></select></label><label>优先级<select v-model="form.urgency"><option value="normal">一般</option><option value="urgent">紧急</option><option value="strategic">战略项目</option></select></label></div>
      <button class="primary">提交申请</button>
    </form>
  </AppModal>
  <AppModal v-if="reviewModal" title="评审智能体申请" @close="reviewModal=false">
    <form class="form-stack" @submit.prevent="saveReview"><label>处理状态<select v-model="review.status"><option value="reviewing">评审中</option><option value="approved">已批准</option><option value="rejected">未采纳</option><option value="delivered">已交付</option><option value="closed">已关闭</option></select></label><label>管理员意见<textarea v-model="review.admin_comment" placeholder="记录评估结论、下一步或未采纳原因"></textarea></label><button class="primary">保存评审</button></form>
  </AppModal>
</template>

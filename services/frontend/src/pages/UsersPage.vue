<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";
import StatusBadge from "../components/StatusBadge.vue";
import BaseButton from "../components/base/BaseButton.vue";
import BaseBadge from "../components/base/BaseBadge.vue";
import BaseCard from "../components/base/BaseCard.vue";
import BaseEmptyState from "../components/base/BaseEmptyState.vue";
import BaseIcon from "../components/base/BaseIcon.vue";
import BaseSkeleton from "../components/base/BaseSkeleton.vue";
import { useTemporaryPassword } from "../features/users/temporaryPassword";
import type {
  CreateUserResponse,
  DepartmentDto,
  KnowledgeBasePermissionGrant,
  ResetPasswordResponse,
  UserDto,
  UserPermissionDetail,
} from "../shared/types/users";
import { formatDate } from "../utils";

const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const users = ref<UserDto[]>([]);
const departments = ref<DepartmentDto[]>([]);
const knowledgeBases = ref<any[]>([]);
const tools = ref<any[]>([]);
const permissionDetail = ref<UserPermissionDetail | null>(null);
const inheritedPreview = ref<KnowledgeBasePermissionGrant[]>([]);
const loading = ref(true), permissionsLoading = ref(false), inheritedLoading = ref(false), loadError = ref(""), copyHint = ref("");
const modal = ref(""), editing = ref<UserDto | null>(null);
const { notice: temporaryPasswordNotice, show: showTemporaryPassword, clear: clearTemporaryPassword } = useTemporaryPassword();
const form = reactive({
  username: "", display_name: "", email: "", department_id: 0,
  knowledge_base_grants: [] as Array<{ knowledge_base_id: number; permission: "read" | "manage" }>,
  tool_ids: [] as number[], name: "", code: "",
});

const readonlyTools = computed(() => tools.value.filter((item) => item.annotations?.readOnlyHint === true));
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : "操作失败"; }
function connectorTools(payload: any): any[] {
  return (payload?.items || []).flatMap((connector: any) =>
    (connector.tools || []).map((tool: any) => ({ ...tool, connector_name: connector.name }))
  );
}
async function load() {
  loading.value = true; loadError.value = "";
  try {
    const [departmentRows, userRows, kbRows, connectors] = await Promise.all([
      api<DepartmentDto[]>("/api/v1/departments"), api<UserDto[]>("/api/v1/users"),
      api<any[]>("/api/v1/knowledge-bases"), api<any>("/api/v1/connectors"),
    ]);
    departments.value = departmentRows; users.value = userRows; knowledgeBases.value = kbRows;
    tools.value = connectorTools(connectors);
  } catch (error) {
    loadError.value = errorMessage(error); emit("toast", loadError.value, true);
  } finally { loading.value = false; }
}
onMounted(load);

function resetUserForm(user?: UserDto) {
  Object.assign(form, {
    username: user?.username || "", display_name: user?.display_name || "", email: user?.email || "",
    department_id: Number(user?.department_id || departments.value[0]?.id || 0),
    knowledge_base_grants: [], tool_ids: [],
  });
}
async function openUser(user?: UserDto) {
  clearTemporaryPassword(); copyHint.value = ""; permissionDetail.value = null; inheritedPreview.value = [];
  editing.value = user || null; resetUserForm(user); modal.value = "user";
  if (!user) { await loadDepartmentPreview(); return; }
  permissionsLoading.value = true;
  try {
    const detail = await api<UserPermissionDetail>(`/api/v1/users/${user.id}/permissions`);
    permissionDetail.value = detail;
    inheritedPreview.value = detail.department_inherited_knowledge_base_grants;
    form.knowledge_base_grants = detail.direct_knowledge_base_grants.map((item) => ({
      knowledge_base_id: item.knowledge_base_id, permission: item.permission,
    }));
    form.tool_ids = detail.direct_tools.map((item) => item.id);
  } catch (error) {
    emit("toast", errorMessage(error), true); modal.value = "";
  } finally { permissionsLoading.value = false; }
}
let inheritedRequest = 0;
async function loadDepartmentPreview() {
  const departmentId = Number(form.department_id), requestId = ++inheritedRequest;
  inheritedPreview.value = [];
  if (!departmentId) return;
  inheritedLoading.value = true;
  try {
    const rows = await api<KnowledgeBasePermissionGrant[]>(`/api/v1/departments/${departmentId}/knowledge-base-grants`);
    if (requestId === inheritedRequest) inheritedPreview.value = rows;
  } catch (error) {
    if (requestId === inheritedRequest) emit("toast", errorMessage(error), true);
  } finally {
    if (requestId === inheritedRequest) inheritedLoading.value = false;
  }
}
function directKbGrant(id: number) { return form.knowledge_base_grants.find((item) => item.knowledge_base_id === id); }
function toggleKnowledgeBase(id: number) {
  const index = form.knowledge_base_grants.findIndex((item) => item.knowledge_base_id === id);
  if (index >= 0) form.knowledge_base_grants.splice(index, 1);
  else form.knowledge_base_grants.push({ knowledge_base_id: id, permission: "read" });
}
function setKnowledgePermission(id: number, permission: "read" | "manage") {
  const grant = directKbGrant(id); if (grant) grant.permission = permission;
}
function toggleTool(id: number) {
  form.tool_ids = form.tool_ids.includes(id) ? form.tool_ids.filter((item) => item !== id) : [...form.tool_ids, id];
}
function openDepartment() { clearTemporaryPassword(); modal.value = "department"; }
function closeTemporaryPassword() { clearTemporaryPassword(); copyHint.value = ""; modal.value = ""; }
async function saveUser() {
  try {
    const payload = {
      username: form.username, display_name: form.display_name, email: form.email || null,
      department_id: Number(form.department_id), knowledge_base_grants: form.knowledge_base_grants,
      tool_ids: form.tool_ids,
    };
    const wasEditing = Boolean(editing.value);
    if (editing.value) {
      clearTemporaryPassword();
      await api(`/api/v1/users/${editing.value.id}`, { method: "PUT", body: JSON.stringify(payload) });
      modal.value = "";
    } else {
      const response = await api<CreateUserResponse>("/api/v1/users", { method: "POST", body: JSON.stringify(payload) });
      showTemporaryPassword(response.username, response.temporary_password); modal.value = "password";
    }
    await load(); emit("toast", wasEditing ? "账号与权限已更新" : "账号已创建");
  } catch (error) { emit("toast", errorMessage(error), true); }
}
async function createDepartment() { try { await api("/api/v1/departments", { method: "POST", body: JSON.stringify({ name: form.name, code: form.code, parent_id: 1 }) }); modal.value = ""; await load(); emit("toast", "部门已创建"); } catch (error) { emit("toast", errorMessage(error), true); } }
async function setStatus(user: UserDto) { try { await api(`/api/v1/users/${user.id}/status`, { method: "PATCH", body: JSON.stringify({ status: user.status ? 0 : 1 }) }); await load(); emit("toast", "账号状态已更新"); } catch (error) { emit("toast", errorMessage(error), true); } }
async function reset(user: UserDto) { if (!confirm(`确定重置账号 ${user.username} 的密码？`)) return; try { const response = await api<ResetPasswordResponse>(`/api/v1/users/${user.id}/reset-password`, { method: "POST" }); showTemporaryPassword(user.username, response.temporary_password); modal.value = "password"; } catch (error) { emit("toast", errorMessage(error), true); } }
async function remove(user: UserDto) { if (!confirm(`确定删除账号 ${user.username}？`)) return; try { await api(`/api/v1/users/${user.id}`, { method: "DELETE" }); await load(); emit("toast", "账号已删除"); } catch (error) { emit("toast", errorMessage(error), true); } }
async function copyPassword() { if (!temporaryPasswordNotice.value) return; copyHint.value = ""; try { if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable"); await navigator.clipboard.writeText(temporaryPasswordNotice.value.temporaryPassword); emit("toast", "临时密码已复制"); } catch { copyHint.value = "无法自动复制，请手工选中临时密码并复制。"; emit("toast", copyHint.value, true); } }
</script>

<template>
  <div class="page-header"><div><h2>账号与权限</h2><p>部门决定默认知识范围；管理员可再按账号直授知识库和只读 MCP 工具</p></div></div>
  <div class="page-toolbar"><div class="actions"><BaseButton variant="secondary" @click="openDepartment"><BaseIcon name="add" />新建部门</BaseButton><BaseButton @click="openUser()"><BaseIcon name="add" />创建账号</BaseButton></div></div>
  <BaseCard class="content-card" padding="none">
    <div v-if="loading && !users.length" class="page-loading"><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /></div>
    <BaseEmptyState v-else-if="loadError" title="用户目录加载失败" :description="loadError" role="alert"><template #action><BaseButton variant="secondary" @click="load">重试</BaseButton></template></BaseEmptyState>
    <div v-else-if="users.length" class="table-wrap"><table><thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{ user.username }}</td><td>{{ user.display_name }}</td><td><BaseBadge :tone="user.department_code==='PLATFORM_ADMIN' ? 'info' : 'neutral'">{{ user.department_name||"未分配" }}</BaseBadge></td><td><StatusBadge :status="user.status"/></td><td>{{ formatDate(user.last_login_at) }}</td><td><div class="actions table-actions"><BaseButton size="sm" variant="secondary" @click="openUser(user)">编辑</BaseButton><BaseButton size="sm" variant="secondary" @click="reset(user)">重置密码</BaseButton><BaseButton size="sm" :variant="user.status?'danger':'secondary'" @click="setStatus(user)">{{user.status?'停用':'启用'}}</BaseButton><BaseButton size="sm" variant="danger" @click="remove(user)">删除</BaseButton></div></td></tr></tbody></table></div>
    <BaseEmptyState v-else title="暂无账号" description="创建企业账号后，可组合部门继承和账号直授权。" />
  </BaseCard>

  <AppModal v-if="modal==='user'" :title="editing?'编辑企业账号':'创建企业账号'" @close="modal=''">
    <form class="form-stack account-editor" @submit.prevent="saveUser">
      <div class="form-grid"><label>用户名<input v-model="form.username" required></label><label>姓名<input v-model="form.display_name" required></label></div>
      <label>邮箱（可选）<input v-model="form.email" type="email"></label>
      <label>所属部门<select v-model.number="form.department_id" @change="loadDepartmentPreview"><option v-for="dept in departments" :key="dept.id" :value="dept.id">{{dept.name}}（{{dept.code}}）</option></select></label>
      <div v-if="permissionsLoading" class="page-loading"><BaseSkeleton height="56px" /><BaseSkeleton height="80px" /></div>
      <template v-else>
        <section class="permission-section">
          <div><h3>部门继承知识库</h3><p>随所属部门自动变化，只在该账号的部门权限范围内生效。</p></div>
          <BaseSkeleton v-if="inheritedLoading" height="32px" />
          <div v-else-if="inheritedPreview.length" class="permission-chips"><BaseBadge v-for="grant in inheritedPreview" :key="grant.knowledge_base_id" tone="info">{{grant.name}} · {{grant.permission==='manage'?'可管理':'可查询'}}</BaseBadge></div>
          <p v-else class="muted">该部门当前没有继承知识库。</p>
        </section>
        <fieldset class="permission-section"><legend>账号直授知识库</legend><p class="muted">直授权与部门继承取权限较高者；“可管理”允许上传、移动和删除资料。</p><div class="permission-grid"><label v-for="kb in knowledgeBases" :key="kb.id" class="permission-option"><input type="checkbox" :checked="Boolean(directKbGrant(kb.id))" @change="toggleKnowledgeBase(kb.id)"><span><strong>{{kb.name}}</strong><small>{{kb.code}}</small></span><select v-if="directKbGrant(kb.id)" :value="directKbGrant(kb.id)?.permission" :aria-label="`${kb.name}权限`" @change="setKnowledgePermission(kb.id, ($event.target as HTMLSelectElement).value as 'read'|'manage')"><option value="read">可查询</option><option value="manage">可管理</option></select></label></div></fieldset>
        <fieldset class="permission-section"><legend>账号直授 MCP 工具</legend><p class="muted">这里只允许分配已标记为只读的工具；专业智能体自身权限不在这里展开。</p><div class="permission-grid"><label v-for="tool in readonlyTools" :key="tool.id" class="permission-option"><input type="checkbox" :checked="form.tool_ids.includes(tool.id)" @change="toggleTool(tool.id)"><span><strong>{{tool.connector_name}} / {{tool.title||tool.tool_name}}</strong><small>{{tool.description||'只读 MCP 工具'}}</small></span><BaseBadge tone="success">只读</BaseBadge></label></div><p v-if="!readonlyTools.length" class="muted">暂无可分配的只读工具。</p></fieldset>
      </template>
      <BaseButton type="submit" :disabled="permissionsLoading">保存</BaseButton>
    </form>
  </AppModal>
  <AppModal v-if="modal==='department'" title="新建部门" @close="modal=''"><form class="form-stack" @submit.prevent="createDepartment"><label>部门名称<input v-model="form.name" required></label><label>部门编码<input v-model="form.code" pattern="[A-Z][A-Z0-9_]+" required></label><BaseButton type="submit">创建部门</BaseButton></form></AppModal>
  <AppModal v-if="modal==='password' && temporaryPasswordNotice" title="临时密码（仅显示一次）" @close="closeTemporaryPassword"><div class="temporary-password"><p>账号 <strong>{{temporaryPasswordNotice.username}}</strong> 的临时密码：</p><div class="password-once"><code tabindex="0">{{temporaryPasswordNotice.temporaryPassword}}</code><BaseButton variant="secondary" @click="copyPassword"><BaseIcon name="copy" />复制密码</BaseButton></div><p v-if="copyHint" class="copy-hint" role="status">{{copyHint}}</p><BaseButton class="wide" @click="closeTemporaryPassword">我已保存，关闭</BaseButton></div></AppModal>
</template>

<style scoped>
.account-editor { min-width: min(760px, 78vw); }
.permission-section { display: grid; gap: .65rem; padding: 1rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle, #f8fafc); }
.permission-section h3, .permission-section p { margin: 0; }
.permission-section legend { padding: 0 .35rem; font-weight: 700; }
.permission-chips { display: flex; flex-wrap: wrap; gap: .5rem; }
.permission-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; }
.permission-option { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .65rem; min-height: 58px; padding: .7rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); }
.permission-option span { display: grid; gap: .2rem; min-width: 0; }
.permission-option small { color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.permission-option select { width: auto; min-width: 92px; }
@media (max-width: 760px) { .account-editor { min-width: 0; } .permission-grid { grid-template-columns: 1fr; } }
</style>

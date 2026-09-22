<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
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
import type { CreateUserResponse, DepartmentDto, ResetPasswordResponse, UserDto } from "../shared/types/users";
import { formatDate } from "../utils";
const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const users = ref<UserDto[]>([]), departments = ref<DepartmentDto[]>([]);
const loading=ref(true),loadError=ref(""),copyHint=ref("");
const modal = ref(""), editing = ref<UserDto | null>(null);
const { notice: temporaryPasswordNotice, show: showTemporaryPassword, clear: clearTemporaryPassword } = useTemporaryPassword();
const form = reactive({ username: "", display_name: "", email: "", department_id: 0, name: "", code: "" });
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : "操作失败"; }
async function load() { loading.value=true;loadError.value="";try{[departments.value, users.value] = await Promise.all([api<DepartmentDto[]>("/api/v1/departments"), api<UserDto[]>("/api/v1/users")]);}catch(e){loadError.value=errorMessage(e);emit("toast",loadError.value,true);}finally{loading.value=false;} }
onMounted(load);
function openUser(user?: UserDto) {
  clearTemporaryPassword();
  copyHint.value="";
  editing.value = user || null; Object.assign(form, { username:user?.username||"", display_name:user?.display_name||"", email:user?.email||"", department_id:Number(user?.department_id||departments.value[0]?.id||0) }); modal.value="user";
}
function openDepartment() { clearTemporaryPassword(); modal.value="department"; }
function closeTemporaryPassword() { clearTemporaryPassword();copyHint.value="";modal.value=""; }
async function saveUser() {
  try {
    const payload={username:form.username,display_name:form.display_name,email:form.email||null,department_id:Number(form.department_id)};
    if(editing.value) {
      clearTemporaryPassword();
      await api(`/api/v1/users/${editing.value.id}`,{method:"PUT",body:JSON.stringify(payload)});
      modal.value="";
    } else {
      const response = await api<CreateUserResponse>("/api/v1/users",{method:"POST",body:JSON.stringify(payload)});
      showTemporaryPassword(response.username,response.temporary_password);
      modal.value="password";
    }
    await load(); emit("toast",editing.value?"账号信息已更新":"账号已创建");
  } catch(e){emit("toast",errorMessage(e),true);}
}
async function createDepartment(){try{await api("/api/v1/departments",{method:"POST",body:JSON.stringify({name:form.name,code:form.code,parent_id:1})});modal.value="";await load();emit("toast","部门已创建");}catch(e){emit("toast",errorMessage(e),true);}}
async function setStatus(user:UserDto){try{await api(`/api/v1/users/${user.id}/status`,{method:"PATCH",body:JSON.stringify({status:user.status?0:1})});await load();emit("toast","账号状态已更新");}catch(e){emit("toast",errorMessage(e),true);}}
async function reset(user:UserDto){if(!confirm(`确定重置账号 ${user.username} 的密码？`))return;try{const response=await api<ResetPasswordResponse>(`/api/v1/users/${user.id}/reset-password`,{method:"POST"});showTemporaryPassword(user.username,response.temporary_password);modal.value="password";}catch(e){emit("toast",errorMessage(e),true);}}
async function remove(user:UserDto){if(!confirm(`确定删除账号 ${user.username}？`))return;try{await api(`/api/v1/users/${user.id}`,{method:"DELETE"});await load();emit("toast","账号已删除");}catch(e){emit("toast",errorMessage(e),true);}}
async function copyPassword(){if(!temporaryPasswordNotice.value)return;copyHint.value="";try{if(!navigator.clipboard?.writeText)throw new Error("Clipboard unavailable");await navigator.clipboard.writeText(temporaryPasswordNotice.value.temporaryPassword);emit("toast","临时密码已复制");}catch{copyHint.value="无法自动复制，请手工选中临时密码并复制。";emit("toast",copyHint.value,true);}}
</script>
<template>
  <div class="page-header"><div><h2>账号与部门权限</h2><p>权限完全由所属部门决定；平台管理员部门拥有全局管理权限</p></div></div>
  <div class="page-toolbar"><div class="actions"><BaseButton variant="secondary" @click="openDepartment"><BaseIcon name="add" />新建部门</BaseButton><BaseButton @click="openUser()"><BaseIcon name="add" />创建账号</BaseButton></div></div>
  <BaseCard class="content-card" padding="none"><div v-if="loading && !users.length" class="page-loading"><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /><BaseSkeleton height="48px" /></div><BaseEmptyState v-else-if="loadError" title="用户目录加载失败" :description="loadError" role="alert"><template #action><BaseButton variant="secondary" @click="load">重试</BaseButton></template></BaseEmptyState><div v-else-if="users.length" class="table-wrap"><table><thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{ user.username }}</td><td>{{ user.display_name }}</td><td><BaseBadge :tone="user.department_code==='PLATFORM_ADMIN' ? 'info' : 'neutral'">{{ user.department_name||"未分配" }}</BaseBadge></td><td><StatusBadge :status="user.status"/></td><td>{{ formatDate(user.last_login_at) }}</td><td><div class="actions table-actions"><BaseButton size="sm" variant="secondary" @click="openUser(user)">编辑</BaseButton><BaseButton size="sm" variant="secondary" @click="reset(user)">重置密码</BaseButton><BaseButton size="sm" :variant="user.status?'danger':'secondary'" @click="setStatus(user)">{{user.status?'停用':'启用'}}</BaseButton><BaseButton size="sm" variant="danger" @click="remove(user)">删除</BaseButton></div></td></tr></tbody></table></div><BaseEmptyState v-else title="暂无账号" description="创建企业账号后，可按部门授予数据访问权限。" /></BaseCard>
  <AppModal v-if="modal==='user'" :title="editing?'编辑企业账号':'创建企业账号'" @close="modal=''"><form class="form-stack" @submit.prevent="saveUser"><div class="form-grid"><label>用户名<input v-model="form.username" required></label><label>姓名<input v-model="form.display_name" required></label></div><label>邮箱（可选）<input v-model="form.email" type="email"></label><label>所属部门<select v-model.number="form.department_id"><option v-for="dept in departments" :key="dept.id" :value="dept.id">{{dept.name}}（{{dept.code}}）</option></select></label><BaseButton type="submit">保存</BaseButton></form></AppModal>
  <AppModal v-if="modal==='department'" title="新建部门" @close="modal=''"><form class="form-stack" @submit.prevent="createDepartment"><label>部门名称<input v-model="form.name" required></label><label>部门编码<input v-model="form.code" pattern="[A-Z][A-Z0-9_]+" required></label><BaseButton type="submit">创建部门</BaseButton></form></AppModal>
  <AppModal v-if="modal==='password' && temporaryPasswordNotice" title="临时密码（仅显示一次）" @close="closeTemporaryPassword"><div class="temporary-password"><p>账号 <strong>{{temporaryPasswordNotice.username}}</strong> 的临时密码：</p><div class="password-once"><code tabindex="0">{{temporaryPasswordNotice.temporaryPassword}}</code><BaseButton variant="secondary" @click="copyPassword"><BaseIcon name="copy" />复制密码</BaseButton></div><p v-if="copyHint" class="copy-hint" role="status">{{copyHint}}</p><BaseButton class="wide" @click="closeTemporaryPassword">我已保存，关闭</BaseButton></div></AppModal>
</template>

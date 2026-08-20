<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api";
import AppModal from "../components/AppModal.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { formatDate } from "../utils";
const emit = defineEmits<{ toast: [message: string, bad?: boolean] }>();
const users = ref<any[]>([]), departments = ref<any[]>([]);
const modal = ref(""), editing = ref<any>(null), temporary = ref<any>(null);
const form = reactive({ username: "", display_name: "", email: "", department_id: 0, name: "", code: "" });
async function load() { [departments.value, users.value] = await Promise.all([api<any[]>("/api/v1/departments"), api<any[]>("/api/v1/users")]); }
onMounted(load);
function openUser(user?: any) {
  editing.value = user || null; Object.assign(form, { username:user?.username||"", display_name:user?.display_name||"", email:user?.email||"", department_id:Number(user?.department_id||departments.value[0]?.id||0) }); modal.value="user";
}
async function saveUser() {
  try {
    const payload={username:form.username,display_name:form.display_name,email:form.email||null,department_id:Number(form.department_id)};
    if(editing.value) await api(`/api/v1/users/${editing.value.id}`,{method:"PUT",body:JSON.stringify(payload)});
    else temporary.value=await api("/api/v1/users",{method:"POST",body:JSON.stringify(payload)});
    modal.value=temporary.value?"password":""; await load(); emit("toast",editing.value?"账号信息已更新":"账号已创建");
  } catch(e:any){emit("toast",e.message,true);}
}
async function createDepartment(){try{await api("/api/v1/departments",{method:"POST",body:JSON.stringify({name:form.name,code:form.code,parent_id:1})});modal.value="";await load();emit("toast","部门已创建");}catch(e:any){emit("toast",e.message,true);}}
async function setStatus(user:any){try{await api(`/api/v1/users/${user.id}/status`,{method:"PATCH",body:JSON.stringify({status:user.status?0:1})});await load();emit("toast","账号状态已更新");}catch(e:any){emit("toast",e.message,true);}}
async function reset(user:any){if(!confirm(`确定重置账号 ${user.username} 的密码？`))return;try{temporary.value={username:user.username,...await api<any>(`/api/v1/users/${user.id}/reset-password`,{method:"POST"})};modal.value="password";}catch(e:any){emit("toast",e.message,true);}}
async function remove(user:any){if(!confirm(`确定删除账号 ${user.username}？`))return;try{await api(`/api/v1/users/${user.id}`,{method:"DELETE"});await load();emit("toast","账号已删除");}catch(e:any){emit("toast",e.message,true);}}
async function copyPassword(){await navigator.clipboard.writeText(temporary.value.temporary_password);emit("toast","临时密码已复制");}
</script>
<template>
  <div class="section-head"><div><h2>账号与部门权限</h2><p>权限完全由所属部门决定；平台管理员部门拥有全局管理权限</p></div><div class="actions"><button class="secondary" @click="modal='department'">＋ 新建部门</button><button class="primary" @click="openUser()">＋ 创建账号</button></div></div>
  <div class="card"><div class="table-wrap"><table><thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{ user.username }}</td><td>{{ user.display_name }}</td><td><span class="badge" :class="{'admin-dept':user.department_code==='PLATFORM_ADMIN'}">{{ user.department_name||"未分配" }}</span></td><td><StatusBadge :status="user.status"/></td><td>{{ formatDate(user.last_login_at) }}</td><td><div class="actions"><button class="secondary" @click="openUser(user)">编辑</button><button class="secondary" @click="reset(user)">重置密码</button><button :class="user.status?'danger':'secondary'" @click="setStatus(user)">{{user.status?'停用':'启用'}}</button><button class="danger" @click="remove(user)">删除</button></div></td></tr></tbody></table></div></div>
  <AppModal v-if="modal==='user'" :title="editing?'编辑企业账号':'创建企业账号'" @close="modal=''"><form class="form-stack" @submit.prevent="saveUser"><div class="form-grid"><label>用户名<input v-model="form.username" required></label><label>姓名<input v-model="form.display_name" required></label></div><label>邮箱（可选）<input v-model="form.email" type="email"></label><label>所属部门<select v-model.number="form.department_id"><option v-for="dept in departments" :key="dept.id" :value="dept.id">{{dept.name}}（{{dept.code}}）</option></select></label><button class="primary">保存</button></form></AppModal>
  <AppModal v-if="modal==='department'" title="新建部门" @close="modal=''"><form class="form-stack" @submit.prevent="createDepartment"><label>部门名称<input v-model="form.name" required></label><label>部门编码<input v-model="form.code" pattern="[A-Z][A-Z0-9_]+" required></label><button class="primary">创建部门</button></form></AppModal>
  <AppModal v-if="modal==='password'" title="临时密码（仅显示一次）" @close="modal=''"><div class="temporary-password"><p>账号 <strong>{{temporary.username}}</strong> 的临时密码：</p><div class="password-once"><code>{{temporary.temporary_password}}</code><button class="secondary" @click="copyPassword">复制密码</button></div><button class="primary wide" @click="modal=''">我已保存，关闭</button></div></AppModal>
</template>

<script setup lang="ts">
import { onBeforeUnmount,onMounted,ref,watch } from 'vue';
import { api } from '../api';
const props=defineProps<{agentId:number}>();
const question=ref(''),runs=ref<any[]>([]),error=ref(''),busy=ref(false);let timer:number|undefined;let stopped=false;
async function load(){try{const id=props.agentId;const data=await api<any[]>(`/api/v1/agents/${id}/workflow-runs`);if(id===props.agentId)runs.value=data;}catch(e:any){error.value=e.message;}finally{if(!stopped){if(timer)clearTimeout(timer);timer=window.setTimeout(load,2000);}}}
onMounted(load);watch(()=>props.agentId,load);onBeforeUnmount(()=>{stopped=true;if(timer)clearTimeout(timer);});
async function start(){busy.value=true;error.value='';try{await api(`/api/v1/agents/${props.agentId}/workflow-runs`,{method:'POST',body:JSON.stringify({question:question.value})});await load();}catch(e:any){error.value=e.message;}finally{busy.value=false;}}
async function action(id:string,type:string){try{await api(`/api/v1/workflow-runs/${id}/${type}`,{method:'POST'});await load();}catch(e:any){error.value=e.message;}}
</script>
<template><section class="card form-stack"><h2>工作流运行</h2><p class="muted">运行记录保存在服务器，离开页面不会中止任务。停止会在当前步骤完成后生效，不会撤回已经执行的工具。</p><form class="form-stack" @submit.prevent="start"><label>业务问题 / 输入<textarea v-model="question" required rows="3" maxlength="4000"></textarea></label><button class="primary" :disabled="busy">开始执行</button></form><p class="error" v-if="error">{{error}}</p><article v-for="r in runs" :key="r.id" class="flow-step"><div class="section-head"><strong>{{r.status}} · {{r.created_at}}</strong><div class="actions"><button v-if="r.status==='waiting'" class="primary" @click="action(r.id,'approve')">确认并继续</button><button v-if="['queued','running','waiting'].includes(r.status)" class="danger" @click="action(r.id,'cancel')">停止</button></div></div><p v-if="r.error_code" class="error">{{r.error_code}}</p><p>已完成 {{r.state.next}} 个步骤</p><details v-for="(v,k) in r.state.outputs" :key="k" open><summary>{{k}}</summary><pre>{{JSON.stringify(v,null,2)}}</pre></details></article></section></template>

const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const state = {user:null, departments:[], knowledgeBases:[], agents:[], selectedKb:null, selectedAgent:null, sessionId:null};
const pages = {
  home:["工作台","企业知识与智能体统一入口"], knowledge:["知识库","资料上传、解析、切片与检索索引管理"],
  agents:["智能体","在授权知识范围内获得有出处的回答"], connections:["系统连接","连接 ERP、PLM 与 MOM 等企业业务系统"],
  users:["用户与部门","账号、部门和角色权限管理"], audit:["审计日志","关键操作全程留痕"]
};
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const fmt = value => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "—";
const size = bytes => bytes == null ? "—" : bytes < 1024*1024 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1024/1024).toFixed(1)} MB`;
const isAdmin = () => state.user?.roles.includes("platform_admin");
const canManageKb = () => isAdmin() || state.user?.roles.includes("knowledge_base_admin");

async function api(path, options={}) {
  const response = await fetch(path, {...options, credentials:"same-origin", headers:{...(options.body instanceof FormData ? {} : {"Content-Type":"application/json"}), ...(options.headers||{})}});
  if (response.status === 401 && !path.includes("/auth/login")) { showLogin(); throw new Error("登录已失效，请重新登录"); }
  const type = response.headers.get("content-type") || "";
  const data = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status >= 500) throw new Error("平台服务暂时不可用，请稍后重试");
    throw new Error(data?.detail || data || `请求失败 (${response.status})`);
  }
  return data;
}
function toast(message, bad=false) { const el=$("#toast"); el.textContent=message; el.className=`toast show${bad?" bad":""}`; clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.className="toast",3000); }
function showLogin(){ $("#app-view").classList.add("hidden"); $("#login-view").classList.remove("hidden"); state.user=null; }
function showApp(){ $("#login-view").classList.add("hidden"); $("#app-view").classList.remove("hidden"); $("#user-name").textContent=state.user.display_name; $("#user-dept").textContent=state.user.departments.map(x=>x.name).join("、")||"未分配部门"; $("#avatar").textContent=state.user.display_name[0]; $$(".admin-only").forEach(el=>el.classList.toggle("hidden",!isAdmin())); navigate("home"); }
function modal(title, html){ $("#modal-title").textContent=title; $("#modal-body").innerHTML=html; $("#modal").showModal(); }
function closeModal(){ $("#modal").close(); }
function badge(status){ const map={succeeded:["已完成","success"],active:["启用","success"],indexed:["已索引","success"],queued:["排队中","pending"],running:["处理中","pending"],processing:["处理中","pending"],pending:["待处理","pending"],failed:["失败","failed"],0:["停用","failed"],1:["启用","success"]}; const item=map[status]||[status||"未知",""]; return `<span class="badge ${item[1]}">${esc(item[0])}</span>`; }

$("#login-form").addEventListener("submit", async event => { event.preventDefault(); const form=new FormData(event.target); $("#login-error").textContent=""; try { const result=await api("/api/v1/auth/login",{method:"POST",body:JSON.stringify(Object.fromEntries(form))}); state.user=result.user; showApp(); } catch(error){ $("#login-error").textContent=error.message; }});
$("#logout").addEventListener("click", async()=>{ try{await api("/api/v1/auth/logout",{method:"POST"});}finally{showLogin();} });
$("#modal-close").addEventListener("click", closeModal);
$("#change-password").addEventListener("click", ()=>{
  modal("修改登录密码",`<form id="change-password-form" class="form-stack"><label>当前密码<input name="current_password" type="password" required></label><label>新密码<input name="new_password" type="password" minlength="10" required></label><p class="muted">至少 10 位，且包含大写、小写、数字和特殊字符。修改后需要重新登录。</p><button class="primary">确认修改</button></form>`);
  $("#change-password-form").onsubmit=async event=>{event.preventDefault();try{await api("/api/v1/auth/change-password",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});closeModal();toast("密码已修改，请重新登录");setTimeout(showLogin,800);}catch(error){toast(error.message,true);}};
});
$("#nav").addEventListener("click", event => { const button=event.target.closest("button[data-page]"); if(button) navigate(button.dataset.page); });

async function navigate(page){
  if ((page==="users"||page==="audit")&&!isAdmin()) return;
  $$("#nav button").forEach(button=>button.classList.toggle("active",button.dataset.page===page));
  $("#page-title").textContent=pages[page][0]; $("#page-subtitle").textContent=pages[page][1]; $("#content").innerHTML='<div class="empty">正在加载…</div>';
  try { await ({home:renderHome,knowledge:renderKnowledge,agents:renderAgents,connections:renderConnections,users:renderUsers,audit:renderAudit}[page])(); }
  catch(error){ $("#content").innerHTML=`<div class="card empty"><strong>加载失败</strong>${esc(error.message)}</div>`; }
}

async function loadBase(){ [state.knowledgeBases,state.agents]=await Promise.all([api("/api/v1/knowledge-bases"),api("/api/v1/agents")]); }
async function renderHome(){
  await loadBase(); let docs=[]; for(const kb of state.knowledgeBases) docs.push(...await api(`/api/v1/documents?knowledge_base_id=${kb.id}&limit=10`));
  const done=docs.filter(x=>x.job_status==="succeeded").length, processing=docs.filter(x=>["queued","running"].includes(x.job_status)).length;
  $("#content").innerHTML=`<div class="stats"><div class="stat"><small>可访问知识库</small><strong>${state.knowledgeBases.length}</strong><span>按部门授权</span></div><div class="stat"><small>知识文档</small><strong>${docs.length}</strong><span>${done} 份已入库</span></div><div class="stat"><small>可用智能体</small><strong>${state.agents.length}</strong><span>权限自动继承</span></div><div class="stat"><small>处理任务</small><strong>${processing}</strong><span>后台异步执行</span></div></div>
  <div class="grid-2"><div class="card"><div class="card-header"><h2>知识处理链路</h2><span class="badge success">完整启用</span></div><div class="pipeline"><div><b>01</b>对象存储</div><div><b>02</b>解析/OCR</div><div><b>03</b>语义切片</div><div><b>04</b>向量索引</div><div><b>05</b>混合检索</div><div><b>06</b>重排序</div></div></div><div class="card"><div class="card-header"><h2>快速开始</h2></div><p class="muted">上传制度资料后，由后台自动完成解析、切片、向量化与全文索引。</p><div class="actions"><button class="primary" onclick="navigate('knowledge')">管理知识库</button><button class="secondary" onclick="navigate('agents')">开始问答</button></div></div></div>`;
}

async function renderKnowledge(selectedId=null){
  state.knowledgeBases=await api("/api/v1/knowledge-bases");
  state.selectedKb=state.knowledgeBases.find(x=>x.id===(selectedId||state.selectedKb?.id))||state.knowledgeBases[0]||null;
  const manage=canManageKb();
  $("#content").innerHTML=`<div class="section-head"><div><h2>知识库空间</h2><p>不同部门的数据在服务端强制隔离</p></div>${manage?'<button class="primary" id="new-kb">＋ 新建知识库</button>':''}</div><div class="kb-layout"><div class="card"><div class="kb-list">${state.knowledgeBases.map(k=>`<button class="kb-item ${state.selectedKb?.id===k.id?'active':''}" data-kb="${k.id}"><strong>${esc(k.name)}</strong><small>${esc(k.owner_department_name)} · ${k.document_count} 份资料</small></button>`).join("")||'<div class="empty">暂无可访问知识库</div>'}</div></div><div id="kb-detail"></div></div>`;
  $$(".kb-item").forEach(button=>button.onclick=()=>renderKnowledge(Number(button.dataset.kb)));
  if(manage) $("#new-kb").onclick=openCreateKb;
  if(state.selectedKb) await renderKbDetail(manage);
}
async function renderKbDetail(manage){
  const kb=state.selectedKb, docs=await api(`/api/v1/documents?knowledge_base_id=${kb.id}`);
  $("#kb-detail").innerHTML=`<div class="card"><div class="card-header"><div><h2>${esc(kb.name)}</h2><p class="muted">${esc(kb.description||"暂无描述")} · ${esc(kb.code)}</p></div>${manage?`<button class="danger" id="archive-kb">归档知识库</button>`:''}</div>
  ${manage?`<form id="upload-form" class="upload-zone"><label class="upload-field file-field"><span>选择资料文件</span><input type="file" name="file" required></label><input type="hidden" name="knowledge_base_id" value="${kb.id}"><label class="upload-field"><span>资料标题（可选）</span><input name="title" placeholder="不填写则使用文件名"></label><button class="primary upload-button">上传并入库</button></form>`:''}
  <div class="table-wrap"><table><thead><tr><th>资料</th><th>大小</th><th>处理状态</th><th>切片</th><th>向量</th><th>全文</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${docs.map(d=>`<tr><td><strong>${esc(d.title)}</strong><br><small class="muted">${esc(d.original_filename)}</small></td><td>${size(d.file_size_bytes)}</td><td>${badge(d.job_status||d.extraction_status)}${d.job_error?`<br><small class="error" title="${esc(d.job_error)}">处理失败</small>`:''}</td><td>${d.chunk_count}</td><td>${d.vector_count}/${d.chunk_count}</td><td>${d.fulltext_count}/${d.chunk_count}</td><td>${fmt(d.updated_at)}</td><td><div class="actions"><button class="secondary chunks" data-id="${d.id}">切片</button><button class="ghost download" data-id="${d.id}">下载</button>${manage?`<button class="ghost reindex" data-id="${d.id}">重建</button><button class="danger del-doc" data-id="${d.id}">删除</button>`:''}</div></td></tr>`).join("")||'<tr><td colspan="8"><div class="empty">尚未上传资料</div></td></tr>'}</tbody></table></div></div>`;
  if(manage){ $("#upload-form").onsubmit=upload; $("#archive-kb").onclick=archiveKb; }
  $$(".chunks").forEach(x=>x.onclick=()=>showChunks(x.dataset.id)); $$(".download").forEach(x=>x.onclick=()=>location.href=`/api/v1/documents/${x.dataset.id}/download`);
  $$(".reindex").forEach(x=>x.onclick=()=>documentAction(x.dataset.id,"reindex")); $$(".del-doc").forEach(x=>x.onclick=()=>documentAction(x.dataset.id,"delete"));
}
async function upload(event){ event.preventDefault(); const button=event.submitter; button.disabled=true; button.textContent="上传中…"; try{await api("/api/v1/documents",{method:"POST",body:new FormData(event.target)}); toast("已上传，正在后台解析和建立索引"); await renderKnowledge(state.selectedKb.id);}catch(error){toast(error.message,true);button.disabled=false;button.textContent="上传并入库";} }
async function documentAction(id,action){ if(action==="delete"&&!confirm("确定删除该资料？原文件、切片和检索索引将由后台清理。")) return; try{await api(`/api/v1/documents/${id}${action==="reindex"?"/reindex":""}`,{method:action==="delete"?"DELETE":"POST"});toast(action==="delete"?"删除任务已提交":"重建索引任务已提交");await renderKnowledge(state.selectedKb.id);}catch(e){toast(e.message,true);} }
async function showChunks(id){ try{const chunks=await api(`/api/v1/documents/${id}/chunks`);modal("切片与索引状态",`<div class="form-stack">${chunks.map(c=>`<div class="card"><div class="card-header"><strong>切片 ${c.sequence_no}${c.page_start?` · 第 ${c.page_start} 页`:''}</strong><span>${badge(c.vector_status)} ${badge(c.fulltext_status)}</span></div><p>${esc(c.content_text)}</p></div>`).join("")||'<div class="empty">暂无切片</div>'}</div>`);}catch(e){toast(e.message,true);} }
async function archiveKb(){if(!confirm("确定归档整个知识库？其中资料会进入后台删除队列。"))return;try{await api(`/api/v1/knowledge-bases/${state.selectedKb.id}`,{method:"DELETE"});toast("知识库已归档");state.selectedKb=null;await renderKnowledge();}catch(e){toast(e.message,true);} }
async function openCreateKb(){ state.departments=await api("/api/v1/departments"); modal("新建知识库",`<form id="create-kb" class="form-stack"><label>知识库名称<input name="name" required></label><label>唯一编码<input name="code" pattern="[A-Z][A-Z0-9_]+" placeholder="例如 QUALITY_POLICY" required></label><label>所属部门<select name="owner_department_id">${state.departments.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select></label><label>密级<select name="security_level"><option value="internal">内部</option><option value="confidential">机密</option><option value="secret">秘密</option><option value="public">公开</option></select></label><label>说明<textarea name="description"></textarea></label><button class="primary">创建知识库</button></form>`); $("#create-kb").onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.owner_department_id=Number(data.owner_department_id);try{await api("/api/v1/knowledge-bases",{method:"POST",body:JSON.stringify(data)});closeModal();toast("知识库已创建");await renderKnowledge();}catch(err){toast(err.message,true);}}; }

async function renderAgents(selectedId=null){
  state.agents=await api("/api/v1/agents");
  if(selectedId){
    const agent=state.agents.find(item=>item.id===Number(selectedId));
    if(!agent){toast("该智能体不存在或无权访问",true);return renderAgents();}
    return renderAgentChat(agent);
  }
  state.selectedAgent=null;state.sessionId=null;
  $("#content").innerHTML=`<div class="section-head"><div><h2>可用智能体</h2><p>只展示当前账号有权限使用的智能体，选择后进入独立对话空间</p></div><span class="badge success">${state.agents.length} 个可用</span></div>${state.agents.length?`<div class="agents-grid">${state.agents.map(agent=>`<article class="card agent-list-card"><div class="agent-symbol">✦</div><div class="agent-list-content"><div class="card-header"><div><span class="eyebrow">RAG AGENT</span><h2>${esc(agent.name)}</h2></div>${badge(agent.status)}</div><p>${esc(agent.description||"企业知识问答智能体")}</p><div class="agent-scope"><small>授权知识范围</small><strong>${esc(agent.knowledge_bases||"暂无关联知识库")}</strong></div><button class="primary open-agent" data-id="${agent.id}">进入智能体 <span>→</span></button></div></article>`).join("")}</div>`:`<div class="card empty"><strong>暂无可用智能体</strong>您的部门尚未被授权访问智能体关联的知识库。</div>`}`;
  $$(".open-agent").forEach(button=>button.onclick=()=>renderAgents(Number(button.dataset.id)));
}
function renderAgentChat(agent){
  state.selectedAgent=agent;state.sessionId=null;
  $("#content").innerHTML=`<div class="agent-chat-head"><button id="back-agents" class="secondary">← 返回智能体列表</button><div><h2>${esc(agent.name)}</h2><p>${esc(agent.knowledge_bases)}</p></div></div><div class="chat-layout"><div class="agent-card"><div class="agent-symbol light">✦</div><span class="eyebrow">RAG AGENT</span><h2>${esc(agent.name)}</h2><p>${esc(agent.description)}</p><div class="agent-scope dark"><small>授权知识范围</small><strong>${esc(agent.knowledge_bases)}</strong></div><span class="badge success">已启用</span></div><div class="card chat-box"><div id="messages" class="messages"><div class="message assistant">您好，我会仅根据您有权访问的企业资料回答，并提供引用来源。</div></div><form id="chat-form" class="chat-input"><textarea name="question" placeholder="请输入您想查询的问题…" required></textarea><button class="primary">发送</button></form></div></div>`;
  $("#back-agents").onclick=()=>renderAgents();
  $("#chat-form").onsubmit=async e=>{e.preventDefault();const input=e.target.question,q=input.value.trim();if(!q)return;appendMessage(q,"user");input.value="";const button=e.submitter;button.disabled=true;try{const result=await api(`/api/v1/agents/${agent.id}/chat`,{method:"POST",body:JSON.stringify({question:q,session_id:state.sessionId})});state.sessionId=result.session_id;appendMessage(result.answer,"assistant",result.citations,result.retrieval_method);}catch(err){appendMessage(`请求失败：${err.message}`,"assistant");}finally{button.disabled=false;}};
}
function appendMessage(text,role,citations=[],method=null){const box=$("#messages"),el=document.createElement("div");el.className=`message ${role}`;el.textContent=text;if(citations.length){const c=document.createElement("div");c.className="citation";c.innerHTML=`引用：${citations.map(x=>`《${esc(x.title)}》${x.page?`第 ${x.page} 页`:''}`).join("；")}<br>检索：向量 + 关键词 / RRF 融合 / ${method?.rerank==="model"?"模型":"本地"}重排序`;el.append(c);}box.append(el);box.scrollTop=box.scrollHeight;}

async function renderConnections(){const data=await api("/api/v1/connectors");$("#content").innerHTML=`<div class="section-head"><div><h2>企业系统连接</h2><p>连接器当前仅预留架构，不会主动访问生产系统</p></div></div><div class="connector-grid">${data.planned_types.map(x=>`<div class="card connector"><span class="icon">${x.type==='erp'?'▦':x.type==='plm'?'◇':'⌁'}</span><h2>${x.name}</h2><p>${esc(x.description)}</p><span class="coming">待配置接入</span></div>`).join("")}</div><div class="card empty" style="margin-top:18px"><strong>后续接入建议</strong>采用只读服务账号、字段级映射、增量同步、失败重试和完整审计；凭据不存放在普通配置表中。</div>`;}

async function renderUsers(){
  [state.departments]=await Promise.all([api("/api/v1/departments")]);const users=await api("/api/v1/users");
  $("#content").innerHTML=`<div class="section-head"><div><h2>账号与部门权限</h2><p>普通账号只能由平台管理员创建</p></div><div class="actions"><button class="secondary" id="new-dept">＋ 新建部门</button><button class="primary" id="new-user">＋ 创建账号</button></div></div><div class="card"><div class="table-wrap"><table><thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>角色</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${esc(u.departments||"—")}</td><td>${esc(roleNames(u.roles))}</td><td>${badge(u.status)}</td><td>${fmt(u.last_login_at)}</td><td><div class="actions"><button class="secondary reset-pass" data-id="${u.id}">重置密码</button><button class="${u.status?'danger':'secondary'} user-status" data-id="${u.id}" data-status="${u.status?0:1}">${u.status?'停用':'启用'}</button></div></td></tr>`).join("")}</tbody></table></div></div>`;
  $("#new-user").onclick=openCreateUser;$("#new-dept").onclick=openCreateDept;
  $$(".user-status").forEach(x=>x.onclick=async()=>{try{await api(`/api/v1/users/${x.dataset.id}/status`,{method:"PATCH",body:JSON.stringify({status:Number(x.dataset.status)})});toast("账号状态已更新");renderUsers();}catch(e){toast(e.message,true);}});
  $$(".reset-pass").forEach(x=>x.onclick=()=>resetPassword(x.dataset.id));
}
function roleNames(value){return (value||"").split(",").map(x=>({platform_admin:"平台管理员",knowledge_base_admin:"知识库管理员",employee:"员工"}[x]||x)).join("、");}
function openCreateUser(){modal("创建企业账号",`<form id="create-user" class="form-stack"><div class="form-grid"><label>用户名<input name="username" required></label><label>姓名<input name="display_name" required></label></div><label>初始密码<input name="password" type="password" minlength="10" placeholder="至少10位，含大小写、数字和特殊字符" required></label><label>部门<select name="department_id">${state.departments.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select></label><label>角色<select name="role"><option value="employee">员工</option><option value="knowledge_base_admin">知识库管理员</option><option value="platform_admin">平台管理员</option></select></label><button class="primary">创建账号</button></form>`);$("#create-user").onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));try{await api("/api/v1/users",{method:"POST",body:JSON.stringify({username:d.username,display_name:d.display_name,password:d.password,department_ids:[Number(d.department_id)],roles:[d.role]})});closeModal();toast("账号已创建");renderUsers();}catch(err){toast(err.message,true);}};}
function openCreateDept(){modal("新建部门",`<form id="create-dept" class="form-stack"><label>部门名称<input name="name" required></label><label>部门编码<input name="code" pattern="[A-Z][A-Z0-9_]+" placeholder="例如 QUALITY" required></label><button class="primary">创建部门</button></form>`);$("#create-dept").onsubmit=async e=>{e.preventDefault();try{await api("/api/v1/departments",{method:"POST",body:JSON.stringify({...Object.fromEntries(new FormData(e.target)),parent_id:1})});closeModal();toast("部门已创建");renderUsers();}catch(err){toast(err.message,true);}};}
function resetPassword(id){modal("重置账号密码",`<form id="reset-password" class="form-stack"><label>新密码<input name="new_password" type="password" minlength="10" required></label><p class="muted">至少 10 位，且包含大写、小写、数字和特殊字符。</p><button class="primary">确认重置</button></form>`);$("#reset-password").onsubmit=async e=>{e.preventDefault();try{await api(`/api/v1/users/${id}/reset-password`,{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});closeModal();toast("密码已重置");}catch(err){toast(err.message,true);}};}
async function renderAudit(){const logs=await api("/api/v1/audit-logs?limit=200");$("#content").innerHTML=`<div class="card"><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>来源 IP</th></tr></thead><tbody>${logs.map(x=>`<tr><td>${fmt(x.created_at)}</td><td>${esc(x.display_name||x.username||"系统")}</td><td>${esc(x.action)}</td><td>${esc(x.resource_type)} #${esc(x.resource_id||"—")}</td><td>${esc(x.ip_address||"—")}</td></tr>`).join("")||'<tr><td colspan="5"><div class="empty">暂无日志</div></td></tr>'}</tbody></table></div></div>`;}

(async()=>{try{state.user=await api("/api/v1/auth/me");showApp();}catch{showLogin();}})();

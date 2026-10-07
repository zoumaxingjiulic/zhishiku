# 遗留功能与数据清理实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 逐任务实现；每步用复选框跟踪。测试先失败，再改生产代码。

**目标：** 移除已取消的工作流及确定无用的兼容入口和表，保留问答、评测、检索与权限。

**架构：** 先把仍有效的旧检索策略迁到唯一配置源；API/Worker/Vue 停止引用旧结构；经备份恢复演练和生产核对后执行退役迁移。

**技术栈：** FastAPI/Pydantic/PyMySQL，Vue 3/Vitest，MySQL 8.4，Docker Compose。

**规格：** `docs/superpowers/specs/2026-10-07-legacy-cleanup-design.md`

## 全局约束

- 不改写 `001`–`014`，不删除 `document_department_acl`、文档、对象、向量、全文索引或审计。
- 备份需可恢复，旧 ACL 缺失回填数必须为 0，工作流数据必须为 0；条件不满足即停止物理删表。
- 不能把明文凭据写入 Git、日志或测试快照。生产删表后不能只回滚应用。
- 不触碰当前 `main` 的用户未跟踪目录。

## 文件职责

- `services/api/app/domains/agents/{schemas,router,service,repository}.py`：智能体输入、会话、配置与持久化。
- `services/api/app/domains/studio/{router,service,repository,schemas}.py`：保留评测和知识库处理配置，移除工作流。
- `services/api/app/runtime/{chat,chat_tasks,workflows}.py`：保留异步聊天和评测，移除同步聊天和工作流执行。
- `services/frontend/src/pages/{StudioPage,AgentsPage}.vue`、`components/WorkflowRun.vue`：移除工作流配置与运行 UI。
- `database/mysql/015_*.sql`、`016_*.sql`：先搬迁有效策略，后删除旧结构。
- `deploy/*`、`database/mysql/README.md`、`README.md`：预检、备份、升级顺序与烟测。
- `tests/api/*`、`tests/test_compose_contract.py`、`services/frontend/src/pages/__tests__/*`：保护对外行为。

---

### 任务 1：断开工作流与同步问答 API

- [ ] 在 API 路由测试新增真实 FastAPI 请求：`launch_mode=workflow` 返回 422，四个旧路由返回 404；运行确认先失败。
- [ ] 限制 `AgentWrite.launch_mode` 为 `chat`，删除步骤/输入校验；移除工作流及同步聊天路由和仅其使用的 Service/Repository 方法。
- [ ] 更新创建/更新智能体及旧版本恢复路径，使历史工作流快照不能被重新发布；运行相关 API 测试。
- [ ] 只暂存本任务文件，验证后提交。

### 任务 2：后台只运行聊天与评测

- [ ] 写 runner 回归测试：恢复和领取只触及 `chat_task`、`evaluation_run`；评测仍能结束并写结果，确认旧实现测试失败。
- [ ] 从混合 runner 中移除工作流逻辑并按评测职责命名；删去工作流队列领取、恢复、状态持久化方法和专属测试。
- [ ] 保留评测事务边界、授权复核及审计；运行评测、聊天 worker、质量测试；验证后提交。

### 任务 3：前端移除旧工作流界面

- [ ] 写页面测试：工作室只可配置问答智能体，列表不提供工作流操作；先运行确认失败。
- [ ] 删除 `WorkflowRun.vue`，工作室步骤编辑/兼容文案和智能体页面运行分支；保留问答、版本及评测界面。
- [ ] 更新前端 API/类型和测试，运行 Vitest、typecheck、lint、build；验证后提交。

### 任务 4：收敛检索与模型配置

- [ ] 写检索策略测试：新 `settings_json.retrieval` 优先，未迁旧值的有效策略可被 `015` 无损迁入；先确认测试失败。
- [ ] 新增只迁数据的 `015`：在唯一旧配置可确定时搬迁到 `settings_json.retrieval`；冲突或无效值时停止；写升级前后策略对比脚本/测试。
- [ ] 移除 `retrieval_config_json` 回退和无值的 `agent.llm_model` 读取；`agent.agent_type` 使用处改由 `launch_mode` 推导；运行 Python 测试；验证后提交。

### 任务 5：安全退役数据库结构与文档

- [ ] 写迁移验收测试：空/非空工作流、ACL 缺失、未迁策略分别阻断退役；先确认失败。
- [ ] 新增 `016` 预检并删除 `workflow_run`、`user_role`、`app_role`、`agent_department_acl`、`document_asset` 及三个旧列；保留活跃 ACL、对话/评测/知识库表。
- [ ] 增加受限 `mysqldump` 备份、校验和、临时库恢复演练及升级脚本/说明；更新已过时的工作流文档和历史验收脚本。
- [ ] 新库冷启动、旧库升级演练、Python 全量和前端全量验证；验证后提交。

### 任务 6：生产发布与删表门槛

- [ ] 只读重查版本、行数、外键、权限回填和有效检索策略；记录结果，不输出凭据。
- [ ] 在维护窗口生成整库备份并于临时库恢复核对；失败即停止。
- [ ] 按顺序运行 `015`、策略对账、发布 API/Worker/前端并烟测，然后运行 `016`；逐步检查退出码和业务健康。
- [ ] 最后核对目标表/列已消失、保留表/资料计数不变、问答及评测可用；报告实际结果和回退位置。

## 计划自检

规格中的代码、前端、数据和部署门槛分别由任务 1–6 覆盖。`015` 必须在运行时代码切换前执行，`016` 必须在切换和备份验证后执行；任务顺序不能颠倒。

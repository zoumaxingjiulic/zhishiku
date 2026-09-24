# 用户级能力授权与智能体临时委托实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。每项功能必须先写失败测试，再写最小实现。

**目标：** 将知识库、MCP 工具和专业智能体授权改造成用户级永久授权与智能体会话级临时委托，并从企业总助手问答链路彻底移除专业智能体。

**架构：** 账号领域维护用户永久 ACL；智能体领域维护用户分发和运行时委托上下文；企业总助手只构建永久权限能力目录。通用知识库与连接器 API 不接受委托权限，只有已授权专业智能体的内部运行时可以消费临时上下文。

**技术栈：** FastAPI、Pydantic、PyMySQL/MySQL 8.4、Vue 3、TypeScript、Vitest、Pytest、Docker Compose。

**规格：** `docs/superpowers/specs/2026-09-24-user-scoped-capability-delegation-design.md`

## 全局约束

- 不实现部门级智能体分发、授权模式、必需/可选能力或受限模式。
- 不允许用户自行申请或扩大本轮授权。
- MCP 第一版只授权并执行 `readOnlyHint=true` 的工具。
- 不修改知识文档、切片、Milvus、OpenSearch、MinIO 或模型服务数据。
- `agent_department_acl` 仅用于 014 一次性回填，运行时代码不再读写。
- 每个任务完成后运行定向测试；合并前运行全部后端、前端和仓库契约测试。

---

### 任务 1：新增用户级 ACL 数据契约

**文件：**
- 创建：`database/mysql/014_user_scoped_capabilities.sql`
- 修改：`database/mysql/README.md`
- 测试：`tests/test_compose_contract.py`

- [x] 先增加迁移契约测试，断言三张新表、外键、唯一键、只允许一次性回填且不改知识数据。
- [x] 运行测试并确认失败。
- [x] 创建 `user_knowledge_base_acl`、`user_connector_tool_acl`、`user_agent_acl`。
- [x] 通过 `agent_department_acl -> user_department -> app_user` 一次性回填现有智能体用户授权。
- [x] 在迁移 README 登记兼容表退役策略与生产执行顺序。
- [x] 运行迁移契约和仓库卫生测试。

### 任务 2：账号领域维护知识库与工具直授

**文件：**
- 修改：`services/api/app/domains/users/schemas.py`
- 修改：`services/api/app/domains/users/repository.py`
- 修改：`services/api/app/domains/users/service.py`
- 修改：`services/api/app/domains/users/router.py`
- 测试：`tests/api/test_user_transactions.py`
- 新增或修改：`tests/api/test_user_permissions.py`

- [x] 编写账号创建、编辑、回滚、无效知识库、非只读工具、继承权限展示测试。
- [x] 运行测试并确认失败。
- [x] 增加 `knowledge_base_grants` 与 `tool_ids` 写入模型，以及账号权限详情读取模型。
- [x] 在一个事务中更新账号、主部门和两类直授 ACL。
- [x] 增加管理员账号权限详情接口；列表接口不加载大授权集合。
- [x] 服务端验证对象存在、启用状态、知识库权限值和工具只读属性。
- [x] 记录授权增删审计，不记录凭据。

### 任务 3：通用知识库权限合并用户直授

**文件：**
- 修改：`services/api/app/domains/knowledge/service.py`
- 修改：`services/api/app/domains/documents/service.py`
- 修改相关仓储权限查询
- 测试：知识库与文档授权测试

- [x] 编写部门继承、用户只读直授、用户管理直授、越权写入与部门变更测试。
- [x] 运行测试并确认失败。
- [x] 把有效权限计算统一为“部门 ACL 与用户 ACL 合并，manage 优先”。
- [x] 保证 `read` 只能浏览/检索，`manage` 才能上传、重建、目录维护和删除。
- [x] 避免知识库、文档和检索领域各自复制不一致 SQL。

### 任务 4：智能体改为用户分发并建立临时委托

**文件：**
- 修改：`services/api/app/domains/agents/schemas.py`
- 修改：`services/api/app/domains/agents/repository.py`
- 修改：`services/api/app/domains/agents/service.py`
- 修改：`services/api/app/runtime/chat.py`
- 修改智能体检索与 MCP 内部适配器
- 测试：智能体授权、检索、工具和会话运行测试

- [x] 编写仅 `user_agent_acl` 可见、管理员可测试、撤权即时生效测试。
- [x] 编写“用户无永久 KB 权限，但在已分发智能体内可检索绑定 KB”的失败测试。
- [x] 编写“委托权限不能用于直接文档/API/MCP 访问”的失败测试。
- [x] 将 `AgentWrite.department_ids` 改为 `user_ids`，保存时替换 `user_agent_acl`。
- [x] `authorize_agent` 返回全部有效智能体绑定，而不是和用户永久 KB 权限求交集。
- [x] 增加不可跨会话复用的内部委托上下文，在每次检索/工具调用前重新校验用户、智能体、绑定和任务归属。
- [x] 只允许委托调用绑定的只读 MCP 工具。

### 任务 5：企业总助手仅使用用户永久能力

**文件：**
- 修改：`services/api/app/domains/assistant/repository.py`
- 修改：`services/api/app/domains/assistant/capabilities.py`
- 修改：`services/api/app/domains/assistant/schemas.py`
- 修改：`services/api/app/domains/assistant/intent.py`
- 修改：`services/api/app/domains/assistant/orchestrator.py`
- 修改：`services/api/app/domains/assistant/provenance.py`
- 测试：`tests/api/test_assistant_capabilities.py`
- 测试：`tests/api/test_assistant_intent.py`
- 测试：`tests/api/test_assistant_runtime.py`

- [x] 编写能力目录不含智能体、工具仅来自用户直授、知识库为部门与直授并集的测试。
- [x] 编写模型返回 `agent_ids` 时被拒绝或清洗为澄清、且编排器绝不调用智能体的测试。
- [x] 编写依赖智能体的 Skill 不进入能力目录、也不能间接委托的测试。
- [x] 从总助手公开选择模型移除 `agent_ids` 和 `agent_task`，同时兼容读取历史意图快照。
- [x] 删除总助手编排器中的智能体执行分支和工具的间接智能体授权链。
- [x] 保留一般聊天、知识检索、用户直授只读工具与合规 Skill。

### 任务 6：账号和智能体配置前端

**文件：**
- 修改：`services/frontend/src/pages/UsersPage.vue`
- 修改：`services/frontend/src/pages/StudioPage.vue`
- 修改：`services/frontend/src/api.ts`
- 修改：`services/frontend/src/shared/types/*`
- 测试：对应页面和 API 类型测试

- [x] 编写账号表单展示部门继承权限、编辑额外 KB 权限与只读 MCP 工具的测试。
- [x] 编写智能体工作室按用户分发、不再显示部门分发的测试。
- [x] 运行测试并确认失败。
- [x] 实现分组、搜索、空状态和来源标签；继承项只读且解释清晰。
- [x] 知识库额外授权支持 `read/manage`；工具只展示启用的只读工具。
- [x] 智能体工作室保存 `user_ids`，继续单独维护绑定 KB 和工具。

### 任务 7：集成验证、文档与上线

**文件：**
- 修改：`README.md`
- 修改：API 契约 fixture 与版本
- 新增：端到端权限矩阵测试

- [x] 覆盖人资部门继承、跨部门直授、智能体临时问答、撤权、企业总助手隔离和直接 API 拒绝。
- [x] 运行后端全部测试、前端全部测试、构建、Compose 配置检查和仓库卫生检查。
- [x] 请求代码审查并修复必须项。
- [x] 合并前再次执行完整验证，记录测试数量与构建结果。
- [x] 先备份 MySQL，再应用 014，随后更新 API/worker/chat-runner/frontend；验证健康检查和真实权限矩阵。
- [ ] 稳定运行一个兼容周期后，另立迁移删除 `agent_department_acl`。

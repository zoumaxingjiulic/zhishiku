# 企业总助手、意图识别与前端升级实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不破坏现有知识库、MCP、智能体和历史工作流数据的前提下，交付具备权限过滤、显式意图识别、持久化会话和能力调度的企业总助手，并统一升级 Vue 前端。

**架构：** 新增 `domains/assistant` 作为平台编排边界，先从现有权限数据构造授权能力目录，再通过结构化意图路由选择知识检索、只读 MCP、声明式 Skill 或专业智能体。总助手复用现有 `chat_session`、`chat_message`、`chat_task` 和后台任务框架，以专用平台智能体保存会话；前端复用现有 Vue Router，同时引入轻量设计系统和总助手专用状态组合函数。

**技术栈：** FastAPI、Pydantic、PyMySQL/MySQL 8.4、现有 OpenAI 兼容模型网关、Vue 3、Vue Router、TypeScript、Vitest、Testing Library、Pytest、Docker Compose。

**规格：** `docs/superpowers/specs/2026-09-22-enterprise-assistant-ui-design.md`

## 全局约束

- 单点登录本轮不做。
- 不开发流程画布、通用节点市场和新的低代码执行引擎。
- 已有 `workflow_run`、相关 API、历史记录和运行代码保留为兼容层，不做破坏性删除。
- 第一版只自动执行只读系统工具。
- 不引入 Kubernetes、微服务拆分、通用消息中间件或强制 A2A。
- Skill 不允许执行 Shell、原始 SQL 或任意 URL。
- 总助手必须先过滤用户权限，再进行意图识别，执行前再次校验权限。
- 知识检索继续复用现有混合检索、RRF、重排序和引用链路。
- 页面切换、刷新和重新登录后，正在回答和已完成内容必须从服务端恢复。
- 数据库迁移只新增表和种子记录，不修改知识库、文档、切片、Milvus 集合或 OpenSearch 索引。
- 保持 `/api/v1/agents` 等现有 URL 的兼容性；新增接口使用 `/api/v1/assistant` 和 `/api/v1/admin/skills`。
- 每个任务完成后只提交该任务涉及的文件，不提交 `outputs/`、`tmp/` 或本地 `.env`。

---

## 计划文件结构

### 后端与迁移

- 创建 `database/mysql/013_enterprise_assistant.sql`：总助手种子、意图决策、Skill 及绑定关系。
- 修改 `database/mysql/README.md`：登记 013 迁移作用和生产执行边界。
- 创建 `services/api/app/domains/assistant/__init__.py`：领域模块声明。
- 创建 `services/api/app/domains/assistant/schemas.py`：能力、意图、Skill 和总助手 API 模型。
- 创建 `services/api/app/domains/assistant/repository.py`：能力目录、Skill、意图决策和平台智能体查询。
- 创建 `services/api/app/domains/assistant/capabilities.py`：授权能力目录构建与二次校验。
- 创建 `services/api/app/domains/assistant/intent.py`：候选召回、结构化模型调用和确定性回退。
- 创建 `services/api/app/domains/assistant/orchestrator.py`：检索、MCP、Skill、专业智能体和普通对话的统一调度。
- 创建 `services/api/app/domains/assistant/service.py`：会话、任务、能力目录和 Skill 用例。
- 创建 `services/api/app/domains/assistant/router.py`：总助手及管理员 Skill HTTP 接口。
- 修改 `services/api/app/application.py`：注册 assistant router 并升级 API 版本。
- 修改 `services/api/app/runtime/chat_tasks.py`：让后台 worker 分派总助手任务并恢复运行中任务。
- 修改 `services/api/app/domains/agents/repository.py`：抽取可复用的会话/任务持久化方法，不复制 SQL。
- 修改 `services/api/app/agent_runtime.py`：暴露现有只读 MCP 执行适配器供总助手复用。

### 前端

- 创建 `services/frontend/src/shared/types/assistant.ts`：总助手 API 类型。
- 创建 `services/frontend/src/composables/useAssistantChat.ts`：会话、消息、任务轮询和页面恢复。
- 创建 `services/frontend/src/components/base/BaseButton.vue`、`BaseCard.vue`、`BaseBadge.vue`、`BaseEmptyState.vue`、`BaseSkeleton.vue`、`BaseIcon.vue`：统一基础组件。
- 创建 `services/frontend/src/components/assistant/AssistantConversationList.vue`：总助手会话列表。
- 创建 `services/frontend/src/components/assistant/AssistantMessages.vue`：消息、引用、能力标签和执行时间线。
- 创建 `services/frontend/src/components/assistant/AssistantComposer.vue`：输入、发送、停止和快捷问题。
- 创建 `services/frontend/src/components/assistant/CapabilityPanel.vue`：可用能力、最近任务和系统状态。
- 重写 `services/frontend/src/pages/DashboardPage.vue`：把首页改为企业总助手工作台。
- 修改 `services/frontend/src/App.vue`：统一导航、顶部栏、窄屏侧栏和页面容器。
- 修改 `services/frontend/src/router.ts`：首页路由保持 `/`，页面组件改为懒加载。
- 修改 `services/frontend/src/pages/StudioPage.vue`：移除新建工作流智能体入口，只保留兼容记录展示。
- 修改 `services/frontend/src/pages/AgentsPage.vue`：统一卡片、聊天布局和空/错/加载状态。
- 修改 `services/frontend/src/pages/KnowledgePage.vue`：知识库选择置顶并重排目录、上传和资料表格。
- 修改其余管理页面：统一筛选栏、表格、表单、详情和危险操作。
- 重构 `services/frontend/src/style.css`：设计变量、响应式布局和基础组件样式。

### 测试与文档

- 创建 `tests/api/test_assistant_capabilities.py`：权限能力目录测试。
- 创建 `tests/api/test_assistant_intent.py`：意图识别与越权候选测试。
- 创建 `tests/api/test_assistant_routes.py`：总助手和 Skill API 契约测试。
- 创建 `tests/api/test_assistant_runtime.py`：运行调度、澄清、失败和审计测试。
- 创建 `services/frontend/src/composables/__tests__/useAssistantChat.spec.ts`：持久化状态测试。
- 重写 `services/frontend/src/pages/__tests__/DashboardPage.spec.ts`：总助手首页测试。
- 创建 `services/frontend/src/components/assistant/__tests__/AssistantMessages.spec.ts`：来源和时间线渲染测试。
- 修改 `tests/fixtures/api_v1_routes.json`、`tests/api/test_application_factory.py`：更新 API 契约。
- 修改 `README.md`：说明总助手、意图路由、Skill、安全边界和部署步骤。

---

### 任务 1：冻结低代码入口并建立数据契约

**文件：**
- 创建：`database/mysql/013_enterprise_assistant.sql`
- 修改：`database/mysql/README.md`
- 修改：`services/frontend/src/pages/StudioPage.vue`
- 测试：`tests/test_compose_contract.py`
- 测试：`services/frontend/src/pages/__tests__/AgentsPage.spec.ts`

- [ ] **步骤 1：编写迁移契约和工作流入口失败测试**

在 `tests/test_compose_contract.py` 增加断言，要求迁移文件存在并包含四个核心对象：

```python
def test_enterprise_assistant_migration_is_registered():
    sql = (ROOT / "database/mysql/013_enterprise_assistant.sql").read_text(encoding="utf-8")
    assert "ENTERPRISE_ASSISTANT" in sql
    assert "CREATE TABLE assistant_intent_decision" in sql
    assert "CREATE TABLE assistant_skill" in sql
    assert "CREATE TABLE assistant_skill_department" in sql
```

在前端测试中渲染 `StudioPage`，断言“运行方式”不再出现 `workflow` 新建选项，但已存在的工作流记录仍能只读显示“兼容模式”。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_compose_contract.py -q
Set-Location services/frontend; npm test -- src/pages/__tests__/AgentsPage.spec.ts
```

预期：迁移文件不存在，且工作室仍显示“步骤式工作流”创建选项。

- [ ] **步骤 3：编写 013 迁移**

迁移必须使用 `SET NAMES utf8mb4;`，以幂等种子方式插入 `agent.code='ENTERPRISE_ASSISTANT'`。新增表的核心结构固定为：

```sql
CREATE TABLE assistant_intent_decision (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 task_id CHAR(36) NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 intent_type VARCHAR(32) NOT NULL,
 confidence DECIMAL(5,4) NOT NULL,
 capability_snapshot JSON NOT NULL,
 decision_json JSON NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 UNIQUE KEY uk_assistant_intent_task(task_id),
 FOREIGN KEY(task_id) REFERENCES chat_task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(64) NOT NULL UNIQUE,
 name VARCHAR(128) NOT NULL,
 description VARCHAR(1000) NULL,
 instruction MEDIUMTEXT NOT NULL,
 input_schema_json JSON NOT NULL,
 trigger_examples_json JSON NOT NULL,
 version INT NOT NULL DEFAULT 1,
 status VARCHAR(20) NOT NULL DEFAULT 'active',
 created_by BIGINT UNSIGNED NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

另外建立 `assistant_skill_department`、`assistant_skill_knowledge_base`、`assistant_skill_tool`、`assistant_skill_agent` 四张外键映射表；所有映射使用联合主键和 `ON DELETE CASCADE`。不要修改任何已有表。

- [ ] **步骤 4：冻结创建入口并登记迁移**

`StudioPage.vue` 的创建表单只允许 `launch_mode='chat'`；载入既有工作流智能体时表单只读显示并提示“历史兼容模式，不能新建或扩展”。在 `database/mysql/README.md` 登记 013 不会删除工作流和知识数据。

- [ ] **步骤 5：运行测试验证通过**

运行步骤 2 的两个命令，并运行：

```powershell
python -m pytest tests/test_repository_hygiene.py -q
```

预期：全部通过，且迁移没有 `DROP`、`DELETE FROM workflow_run` 或知识索引改动。

- [ ] **步骤 6：Commit**

```powershell
git add database/mysql/013_enterprise_assistant.sql database/mysql/README.md services/frontend/src/pages/StudioPage.vue tests/test_compose_contract.py services/frontend/src/pages/__tests__/AgentsPage.spec.ts
git commit -m "feat: establish enterprise assistant schema"
```

### 任务 2：实现授权能力目录

**文件：**
- 创建：`services/api/app/domains/assistant/__init__.py`
- 创建：`services/api/app/domains/assistant/schemas.py`
- 创建：`services/api/app/domains/assistant/repository.py`
- 创建：`services/api/app/domains/assistant/capabilities.py`
- 测试：`tests/api/test_assistant_capabilities.py`

- [ ] **步骤 1：编写权限失败测试**

用内存 StubRepository 覆盖以下场景：普通员工只得到本部门知识库、明确授权智能体、只读 MCP 工具和本部门 Skill；平台管理员得到所有启用能力；`validate_selection()` 对不在快照中的 ID 抛出 `AuthorizationError`。

```python
def test_catalog_filters_every_capability_before_routing():
    catalog = CapabilityCatalog(Repository()).for_user(EMPLOYEE)
    assert [item.id for item in catalog.knowledge_bases] == [2]
    assert [item.id for item in catalog.tools] == [11]
    assert [item.id for item in catalog.agents] == [7]
    assert [item.id for item in catalog.skills] == [5]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/api/test_assistant_capabilities.py -q`

预期：FAIL，`app.domains.assistant` 不存在。

- [ ] **步骤 3：定义稳定类型**

在 `schemas.py` 定义 `CapabilityRef`、`CapabilityCatalogSnapshot` 和 `CapabilitySelection`。所有候选 ID 使用整数数组，工具额外包含 `connector_id`、`read_only` 和 JSON Schema 摘要；序列化快照不包含 Token、模型密钥或连接器凭据。

```python
class CapabilitySelection(BaseModel):
    knowledge_base_ids: list[int] = Field(default_factory=list)
    tool_ids: list[int] = Field(default_factory=list)
    agent_ids: list[int] = Field(default_factory=list)
    skill_ids: list[int] = Field(default_factory=list)
```

- [ ] **步骤 4：实现仓储查询和双重校验**

`AssistantRepository` 只查询启用对象，并把现有知识库、`agent_department_acl`、MCP 授权及 Skill 部门映射应用在 SQL 中。`CapabilityCatalog.for_user()` 生成不可变快照；`validate_selection(user, snapshot, selection)` 必须重新查询当前授权，防止路由后权限被撤销。

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
python -m pytest tests/api/test_assistant_capabilities.py tests/api/test_knowledge_permissions.py tests/api/test_agent_authorization.py tests/api/test_connector_security.py -q
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/app/domains/assistant tests/api/test_assistant_capabilities.py
git commit -m "feat: build authorized assistant capability catalog"
```

### 任务 3：实现结构化意图识别

**文件：**
- 创建：`services/api/app/domains/assistant/intent.py`
- 修改：`services/api/app/domains/assistant/schemas.py`
- 测试：`tests/api/test_assistant_intent.py`

- [ ] **步骤 1：编写路由失败测试**

测试固定覆盖：制度问题 → `knowledge_query`；库存查询缺组织 → `clarification`；明确的 ERP 查询 → `system_query`；标书分析 → `agent_task`；越权候选被清空；模型超时回退为安全的 `general_chat` 或 `clarification`。

```python
def test_model_cannot_select_capability_outside_snapshot():
    decision = router.route("查工资", CATALOG, model=FakeModel(tool_ids=[999]))
    assert decision.selection.tool_ids == []
    assert decision.intent_type == "clarification"
    assert decision.needs_clarification is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/api/test_assistant_intent.py -q`

预期：FAIL，`IntentRouter` 不存在。

- [ ] **步骤 3：实现 JSON Schema 决策模型**

在 `schemas.py` 定义固定枚举和结果：

```python
IntentType = Literal[
    "general_chat", "knowledge_query", "system_query", "agent_task",
    "multi_capability", "clarification", "forbidden",
]

class IntentDecision(BaseModel):
    intent_type: IntentType
    confidence: float = Field(ge=0, le=1)
    selection: CapabilitySelection
    missing_parameters: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    risk: Literal["low", "medium", "high"] = "low"
    reason: str = Field(max_length=500)
```

`IntentRouter` 只将用户问题和能力摘要传给模型，要求 JSON 输出；解析失败、超时、未知 ID、低于 `0.65` 的置信度或缺少参数时不执行外部工具。

- [ ] **步骤 4：增加确定性保护规则**

在模型调用前识别明显写操作词（新增、删除、修改、提交、审批、入库、出库），将涉及企业系统的请求标记为 `forbidden`。该规则只阻止自动系统写操作，不阻止用户在知识库里询问制度中出现的相同词语。

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
python -m pytest tests/api/test_assistant_intent.py tests/test_mcp_runtime.py tests/api/test_outbound_policy.py -q
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/app/domains/assistant/intent.py services/api/app/domains/assistant/schemas.py tests/api/test_assistant_intent.py
git commit -m "feat: add guarded assistant intent routing"
```

### 任务 4：实现总助手编排、持久化任务和 API

**文件：**
- 创建：`services/api/app/domains/assistant/orchestrator.py`
- 创建：`services/api/app/domains/assistant/service.py`
- 创建：`services/api/app/domains/assistant/router.py`
- 修改：`services/api/app/domains/assistant/repository.py`
- 修改：`services/api/app/domains/agents/repository.py`
- 修改：`services/api/app/agent_runtime.py`
- 修改：`services/api/app/runtime/chat_tasks.py`
- 修改：`services/api/app/application.py`
- 测试：`tests/api/test_assistant_runtime.py`
- 测试：`tests/api/test_assistant_routes.py`
- 修改：`tests/fixtures/api_v1_routes.json`
- 修改：`tests/api/test_application_factory.py`

- [ ] **步骤 1：编写 API 与运行时失败测试**

路由测试必须覆盖能力列表、会话增删改查、提交消息、查询任务、停止任务和跨用户 404。运行时测试使用注入的 fake retrieval/tool/agent/model adapters，分别断言五条执行路径和审计内容。

```python
def test_assistant_task_persists_decision_before_external_execution():
    result = orchestrator.run(TASK, EMPLOYEE)
    assert result.answer == "库存 12 件"
    assert repository.events[:2] == ["capability_snapshot", "intent_decision"]
    assert tool.calls == [(11, {"material_code": "123", "organization": "默认组织"})]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/api/test_assistant_runtime.py tests/api/test_assistant_routes.py -q`

预期：FAIL，总助手 router 和 orchestrator 不存在。

- [ ] **步骤 3：抽取会话与任务持久化复用点**

在 `AgentRepository` 中保持现有方法兼容，并新增不绕过所有权约束的通用方法：

```python
def create_owned_session(self, session_id: str, agent_id: int, user_id: int) -> None: ...
def get_owned_session(self, session_id: str, agent_id: int, user_id: int, *, for_update=False) -> dict | None: ...
def enqueue_chat_task(self, *, task_id: str, session_id: str, agent_id: int,
                      user_id: int, user_message_id: int, request_key: str,
                      request: dict) -> dict: ...
```

旧方法委托给这些方法，保证原智能体测试不变。

- [ ] **步骤 4：实现编排器和后台任务**

`AssistantOrchestrator.run()` 的顺序固定为：重新加载用户 → 能力快照 → 意图路由 → 保存决策 → 当前权限复核 → 执行 → 保存回答/来源/工具摘要 → 完成任务。澄清和禁止请求直接生成助手消息，不调用外部系统。

`chat_tasks.py` 根据 `agent.code` 选择 `run_chat_task` 或 `run_assistant_task`，并继续使用原来的最大 4 并发、取消和重启失败语义。不要新增第二个轮询进程。

- [ ] **步骤 5：实现总助手接口并注册 router**

API 使用规格中的七组路径；`PATCH session` 只允许 1–100 个字符标题；删除运行中会话返回 409；提交消息沿用 `request_key` 幂等约束。`application.py` 注册 router，并把 `API_VERSION` 升为 `1.2.0`。

- [ ] **步骤 6：更新 OpenAPI 基线并运行回归**

运行：

```powershell
python -m pytest tests/api/test_assistant_runtime.py tests/api/test_assistant_routes.py tests/api/test_conversation_routes.py tests/api/test_conversation_task_status.py tests/api/test_openapi_contract.py tests/api/test_application_factory.py -q
```

预期：全部通过；路由数量与新基线一致，原接口 operation ID 不变。

- [ ] **步骤 7：Commit**

```powershell
git add services/api/app tests/api/test_assistant_runtime.py tests/api/test_assistant_routes.py tests/fixtures/api_v1_routes.json
git commit -m "feat: add durable enterprise assistant runtime"
```

### 任务 5：实现声明式 Skill 管理

**文件：**
- 修改：`services/api/app/domains/assistant/schemas.py`
- 修改：`services/api/app/domains/assistant/repository.py`
- 修改：`services/api/app/domains/assistant/service.py`
- 修改：`services/api/app/domains/assistant/router.py`
- 创建：`tests/api/test_assistant_skills.py`
- 创建：`services/frontend/src/pages/SkillsPage.vue`
- 创建：`services/frontend/src/pages/__tests__/SkillsPage.spec.ts`
- 修改：`services/frontend/src/router.ts`
- 修改：`services/frontend/src/App.vue`

- [ ] **步骤 1：编写 Skill 权限和校验失败测试**

后端测试：普通员工访问管理接口返回 403；管理员可以创建、更新、停用和查看版本；Schema 拒绝 URL、Shell 和原始 SQL 类型；所有知识库、工具、智能体和部门引用必须存在且启用。前端测试：只有管理员看见“能力配置”，保存成功后列表刷新。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/api/test_assistant_skills.py -q
Set-Location services/frontend; npm test -- src/pages/__tests__/SkillsPage.spec.ts
```

预期：管理接口和页面不存在。

- [ ] **步骤 3：实现版本化 Skill CRUD**

创建和更新请求只接受 `code`、`name`、`description`、`instruction`、`input_schema`、`trigger_examples`、四类绑定 ID、`status` 和 `version`。更新使用乐观锁：版本不一致返回 409，成功后 `version + 1`。删除采用 `status='disabled'`，不物理删除运行引用。

- [ ] **步骤 4：实现管理员页面**

页面采用列表 + 详情表单布局，支持过滤、创建、编辑、启停；绑定项从现有知识库、MCP 工具、智能体和部门接口加载。页面文案明确“Skill 是声明式业务能力，不执行任意代码”。

- [ ] **步骤 5：运行测试验证通过**

运行步骤 2 命令，并增加：

```powershell
python -m pytest tests/api/test_assistant_capabilities.py tests/api/test_assistant_intent.py -q
Set-Location services/frontend; npm run typecheck; npm run lint
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/app/domains/assistant tests/api/test_assistant_skills.py services/frontend/src/pages/SkillsPage.vue services/frontend/src/pages/__tests__/SkillsPage.spec.ts services/frontend/src/router.ts services/frontend/src/App.vue
git commit -m "feat: manage declarative assistant skills"
```

### 任务 6：建立前端设计系统和应用外壳

**文件：**
- 创建：`services/frontend/src/components/base/BaseButton.vue`
- 创建：`services/frontend/src/components/base/BaseCard.vue`
- 创建：`services/frontend/src/components/base/BaseBadge.vue`
- 创建：`services/frontend/src/components/base/BaseEmptyState.vue`
- 创建：`services/frontend/src/components/base/BaseSkeleton.vue`
- 创建：`services/frontend/src/components/base/BaseIcon.vue`
- 修改：`services/frontend/src/App.vue`
- 修改：`services/frontend/src/router.ts`
- 修改：`services/frontend/src/style.css`
- 创建：`services/frontend/src/components/base/__tests__/BaseComponents.spec.ts`

- [ ] **步骤 1：编写组件和应用外壳失败测试**

测试 Button 的 `loading/disabled/danger` 状态、Modal 焦点关闭行为、EmptyState 插槽、Skeleton 可访问标签；应用外壳测试窄屏侧栏按钮、管理员菜单和普通员工菜单。

- [ ] **步骤 2：运行测试验证失败**

运行：`Set-Location services/frontend; npm test -- src/components/base/__tests__/BaseComponents.spec.ts`

预期：基础组件不存在。

- [ ] **步骤 3：实现设计变量和基础组件**

在 `:root` 定义 `--color-primary`、`--color-nav`、`--color-surface`、`--color-text`、`--space-1` 至 `--space-8`、`--radius-sm/md/lg`、`--shadow-sm/md`。基础组件禁止内联业务颜色；`BaseIcon` 使用项目内 SVG path 映射，不添加字符图标依赖。

- [ ] **步骤 4：重构应用外壳和懒加载路由**

侧栏按“工作、资源、管理”分组；当前路由显示稳定选中态；小于 1024px 时侧栏变为抽屉；顶部栏保留账户和修改密码。路由改用 `() => import('./pages/XPage.vue')`，登录守卫逻辑保持不变。

- [ ] **步骤 5：运行质量检查**

运行：

```powershell
Set-Location services/frontend
npm test -- src/components/base/__tests__/BaseComponents.spec.ts
npm run typecheck
npm run lint
npm run build
```

预期：全部通过，无 TypeScript 和 ESLint 错误。

- [ ] **步骤 6：Commit**

```powershell
git add services/frontend/src/components/base services/frontend/src/App.vue services/frontend/src/router.ts services/frontend/src/style.css
git commit -m "refactor: establish frontend design system"
```

### 任务 7：把首页改造成企业总助手

**文件：**
- 创建：`services/frontend/src/shared/types/assistant.ts`
- 创建：`services/frontend/src/composables/useAssistantChat.ts`
- 创建：`services/frontend/src/composables/__tests__/useAssistantChat.spec.ts`
- 创建：`services/frontend/src/components/assistant/AssistantConversationList.vue`
- 创建：`services/frontend/src/components/assistant/AssistantMessages.vue`
- 创建：`services/frontend/src/components/assistant/AssistantComposer.vue`
- 创建：`services/frontend/src/components/assistant/CapabilityPanel.vue`
- 创建：`services/frontend/src/components/assistant/__tests__/AssistantMessages.spec.ts`
- 重写：`services/frontend/src/pages/DashboardPage.vue`
- 重写：`services/frontend/src/pages/__tests__/DashboardPage.spec.ts`

- [ ] **步骤 1：编写会话恢复和首页失败测试**

覆盖：首次进入创建会话；提交问题后立即显示用户消息；页面卸载再挂载时从 `/task` 恢复 `queued/running`；完成后刷新消息；新建、重命名、删除；引用、工具、智能体和 Skill 标签；折叠执行时间线；无能力和 API 错误状态。

```typescript
it("restores a running answer after remount", async () => {
  apiMock.mockResolvedValueOnce([{ id: "s1", latest_task_status: "running" }]);
  const first = renderComposable();
  first.unmount();
  const second = renderComposable();
  await waitFor(() => expect(second.result.isAwaitingAnswer.value).toBe(true));
  expect(apiMock).toHaveBeenCalledWith("/api/v1/assistant/tasks/t1");
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
Set-Location services/frontend
npm test -- src/composables/__tests__/useAssistantChat.spec.ts src/pages/__tests__/DashboardPage.spec.ts src/components/assistant/__tests__/AssistantMessages.spec.ts
```

预期：总助手组件和组合函数不存在。

- [ ] **步骤 3：实现类型和状态组合函数**

`useAssistantChat` 是唯一拥有轮询定时器的模块；按 session ID 保存待处理任务；组件卸载只停止浏览器轮询，不取消服务端任务；再次挂载查询会话的 `latest_task_status` 和任务接口恢复。所有请求使用现有 `api()`，不另建 HTTP 客户端。

- [ ] **步骤 4：实现总助手首页**

桌面布局为会话列、主对话区、能力侧栏；窄屏时会话和能力侧栏通过抽屉展示。消息区显示回答正文、引用来源、能力标签和默认折叠的执行摘要。输入区支持发送、停止、常用问题，运行中禁止同一会话重复提交，但允许新建和切换其他会话。

- [ ] **步骤 5：运行测试与构建**

运行步骤 2 的测试，并运行：

```powershell
npm run typecheck
npm run lint
npm run build
```

预期：全部通过；生成产物不包含旧统计型首页文案。

- [ ] **步骤 6：Commit**

```powershell
git add services/frontend/src/shared/types/assistant.ts services/frontend/src/composables services/frontend/src/components/assistant services/frontend/src/pages/DashboardPage.vue services/frontend/src/pages/__tests__/DashboardPage.spec.ts
git commit -m "feat: make enterprise assistant the home workspace"
```

### 任务 8：统一业务页面布局和交互状态

**文件：**
- 修改：`services/frontend/src/pages/AgentsPage.vue`
- 修改：`services/frontend/src/pages/KnowledgePage.vue`
- 修改：`services/frontend/src/pages/ConnectionsPage.vue`
- 修改：`services/frontend/src/pages/ModelGatewayPage.vue`
- 修改：`services/frontend/src/pages/PromptTemplatesPage.vue`
- 修改：`services/frontend/src/pages/AgentRequestsPage.vue`
- 修改：`services/frontend/src/pages/UsersPage.vue`
- 修改：`services/frontend/src/pages/AuditPage.vue`
- 修改：`services/frontend/src/pages/ObservabilityPage.vue`
- 修改：`services/frontend/src/pages/StudioPage.vue`
- 修改：`services/frontend/src/style.css`
- 修改：`services/frontend/src/pages/__tests__/AgentsPage.spec.ts`
- 修改：`services/frontend/src/pages/__tests__/UsersPage.spec.ts`
- 创建：`services/frontend/src/pages/__tests__/KnowledgePage.spec.ts`

- [ ] **步骤 1：编写关键回归和响应式失败测试**

测试智能体列表使用响应式网格且聊天会话状态恢复；知识库选择位于目录/上传/资料区域上方；上传按钮不折行；用户临时密码复制失败时提供选中文本回退；所有管理页有加载、空数据和错误状态。

- [ ] **步骤 2：运行页面测试验证失败**

运行：

```powershell
Set-Location services/frontend
npm test -- src/pages/__tests__/AgentsPage.spec.ts src/pages/__tests__/UsersPage.spec.ts src/pages/__tests__/KnowledgePage.spec.ts
```

预期：至少知识库布局和统一状态断言失败。

- [ ] **步骤 3：按统一模式迁移页面**

每页固定结构为 `page-header → toolbar/filter → content-card → empty/loading/error`。智能体卡片网格使用 `repeat(auto-fill,minmax(240px,1fr))`；知识库使用顶部选择器和下方双列目录/内容布局；管理表格在窄屏允许容器横向滚动，不压缩操作按钮成多行。

- [ ] **步骤 4：移除冗余页面样式**

把重复按钮、卡片、徽章、弹窗和空状态样式替换为基础组件；删除确认无引用的 CSS 选择器。不得改变权限条件、API 路径和业务字段。

- [ ] **步骤 5：运行完整前端验证**

运行：

```powershell
Set-Location services/frontend
npm test
npm run typecheck
npm run lint
npm run build
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```powershell
git add services/frontend/src/pages services/frontend/src/style.css
git commit -m "refactor: unify enterprise platform pages"
```

### 任务 9：端到端回归、文档和部署验证

**文件：**
- 修改：`README.md`
- 修改：`tests/README.md`
- 修改：`tests/test_process_health.py`
- 修改：`tests/test_quality_platform.py`
- 修改：`deploy/docker-compose.yml`（只有在健康检查需要新路径时修改）

- [ ] **步骤 1：补充系统级失败测试**

增加断言：API 版本为 `1.2.0`；assistant router 已注册；worker 能分派总助手任务；前端健康检查不依赖页面文案；仓库中不存在新的低代码画布依赖和明文凭据。

- [ ] **步骤 2：运行全量测试建立失败清单**

运行：

```powershell
python -m pytest -q
Set-Location services/frontend; npm test; npm run typecheck; npm run lint; npm run build
```

预期：新增系统断言在文档或版本未更新处失败，记录具体失败，不跳过。

- [ ] **步骤 3：更新 README 和运维说明**

README 明确：总助手执行链、意图类型、只读工具边界、Skill 定义、现有工作流兼容状态、013 迁移命令、带模型 compose 文件的启动命令、健康检查和回滚方式。Token 只写环境变量名，不写真实值。

- [ ] **步骤 4：运行全量本地验证**

再次运行步骤 2 的全部命令，预期全部通过。

- [ ] **步骤 5：在服务器执行部署验收**

服务器按顺序执行：备份 MySQL → 应用 013 → 构建 API/前端 → 带 `deploy/docker-compose.models.yml` 启动 → 检查 `/healthz`、`/readyz` 和容器状态。使用管理员和两个不同部门普通账号验证：权限能力不同、总助手会话隔离、知识问答、ERP/OA/纵横标书只读调用、专业智能体委托、页面切换恢复、越权拒绝和审计记录。

- [ ] **步骤 6：记录验收证据**

在部署结果中记录 Git 提交、迁移执行时间、容器镜像、测试命令结果、关键接口状态和未通过项目。不得以“页面可打开”替代完整业务验证。

- [ ] **步骤 7：Commit**

```powershell
git add README.md tests/README.md tests/test_process_health.py tests/test_quality_platform.py deploy/docker-compose.yml
git commit -m "docs: document enterprise assistant operations"
```

---

## 最终验收标准

- 首页打开即为“企业总助手”，员工不需要先选择专业智能体。
- 总助手只看见并调用当前员工权限内的知识库、只读 MCP、Skill 和专业智能体。
- 意图决策有结构化记录；低置信度、缺参数、越权和系统写操作不会自动执行。
- 多员工并发会话、任务和运行记录严格隔离。
- 页面切换和刷新不会丢失问题、回答中状态或最终回答。
- 已有知识库资料、向量、全文索引、智能体对话和工作流历史保持不变。
- 工作室不能新建低代码工作流，系统也没有引入新的流程画布依赖。
- Vue 页面在桌面与窄屏下信息层级一致，按钮不折行遮挡，加载/空/错/禁用状态齐全。
- Python 全量测试与前端 test/typecheck/lint/build 全部通过。
- 服务器部署完成后，健康检查、权限、知识问答、MCP、专业智能体、总助手和审计流程全部验证通过。

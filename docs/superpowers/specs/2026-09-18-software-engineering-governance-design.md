# 企业智能体平台软件工程治理设计

日期：2026-09-18

适用基线：`cb855fd` 及其后仅包含本规格的提交

治理方式：渐进式模块化重构，保持现有业务数据和已公开接口可控迁移

## 1. 背景与目标

平台已经具备知识库、文档入库、混合检索、智能体问答、工作流、模型网关、MCP 连接器、部门权限、评测和审计等功能。当前主要风险来自模块职责混杂、数据库迁移缺乏台账、跨存储操作缺少完整恢复协议、前端类型与测试薄弱，以及少量已经确认的重复或无效实现。

本次治理目标是：

1. 在不丢失现有文档、索引、账号、会话、审计和连接器配置的前提下重构代码。
2. 让每个业务模块具有明确的路由、输入输出、业务服务、数据访问和权限边界。
3. 消除已证明无效或重复的代码、配置、依赖和部署服务。
4. 对数据库对象采用“先迁移并停用、观察一个版本、再物理删除”的策略。
5. 建立自动化测试、迁移校验、构建检查和部署验收门禁。
6. 保持现有办公网入口与用户核心操作流程不变；发生兼容性变化时提供明确迁移窗口。

本次不以功能数量扩张为目标，不引入微服务、Kubernetes、消息中间件、ORM 或低代码工作流引擎。只有现有规模出现可测量瓶颈时才增加相应基础设施。

## 2. 已确认的问题

### 2.1 后端

- `services/api/app/main.py` 同时承担应用创建、数据模型、79 个接口、鉴权、权限、事务、SQL、MinIO、模型、MCP 和审计逻辑。
- `platform.install(app, main_module)`、`tasks.install(app, main_module)` 通过主模块回注依赖，形成隐式耦合和反向导入。
- 权限检查与写事务多次使用不同数据库连接，存在校验后权限变化的时间窗口。
- API 与 Worker 重复实现连接配置和本地测试向量逻辑。
- 同步问答接口与持久化后台任务接口并存；前端已经使用后台任务接口。
- 入库和删除横跨 MySQL、MinIO、Milvus、OpenSearch，部分失败路径无法可靠重试或清理。

### 2.2 前端

- `AgentsPage.vue`、`KnowledgePage.vue`、`StudioPage.vue` 和 `App.vue` 承担过多职责。
- 业务模型大量使用 `any`，接口字段与状态枚举缺少编译期约束。
- 多个页面重复请求基础资源，重复处理异步状态、Toast、日期和状态标签。
- 用户编辑流程可能重新展示上一次创建或重置账号时产生的临时密码。
- 工作台通过每个知识库最多 10 条文档计算文档总数，统计结果不准确且产生 N+1 请求。
- 轮询缺少统一的停止、退避、页面可见性和取消策略。
- 缺少组件测试、前端 lint、独立 typecheck 以及路由元信息类型。

### 2.3 数据库与部署

- MySQL 初始化目录会执行其中全部 SQL，与“全新环境只执行 001”的文档说法冲突；历史乱码修复脚本可能误作用于新库。
- 迁移没有版本台账、校验和、统一执行入口或部分失败诊断。
- 旧角色表仍有历史数据，但运行时权限已经由部门决定。
- 文档 ACL 当前与所属知识库 ACL 完全一致，形成重复权限事实来源。
- Redis 没有业务代码引用，却是 API 和 Worker 的强制启动依赖。
- API、chat-runner 和 Worker 共享一个数据库账号，缺少最小权限隔离。
- `/readyz` 在依赖降级时仍返回成功状态，应用和模型服务缺少有效健康检查。
- `.env.example` 默认启用本地验收模式，容易将测试能力误认为生产模型能力。
- 缺少 Docker 构建忽略文件，镜像构建可能携带宿主机 `node_modules`、`dist` 和缓存。

## 3. 方案选择

采用渐进式模块化治理。拒绝以下两种方向：

- 只整理目录而不改变职责边界：无法解决事务、迁移和测试问题。
- 整体重写：会同时改变业务行为、数据迁移和技术结构，回归风险不可接受。

所有重构以外部行为测试为保护。单个阶段必须可以独立部署和回退，不允许积累多个未经验证的大改动后一次上线。

## 4. 目标架构

### 4.1 后端

```text
services/api/app/
  main.py                       # 应用工厂、生命周期、路由注册
  core/
    config.py                   # 强类型配置与启动校验
    database.py                 # 连接、事务、Unit of Work
    security.py                 # 密码、令牌、Cookie
    audit.py                    # 统一审计接口
    dependencies.py             # current_user、管理员与事务依赖
  domains/
    auth/
    users/
    knowledge/
    documents/
    agents/
    connectors/
    studio/
    observability/
      router.py                 # HTTP 边界
      schemas.py                # 请求与响应模型
      service.py                # 用例编排与业务规则
      repository.py             # SQL 与持久化细节
  runtime/
    retrieval.py
    model_gateway.py
    mcp.py
    chat_tasks.py
    workflows.py
    evaluations.py
```

规则：

- Router 只负责 HTTP 解析、依赖注入和响应，不直接编写业务 SQL。
- Service 在一个 Unit of Work 中完成权限校验和数据写入。
- Repository 集中管理 SQL，不包含 HTTP 异常或界面文案。
- Runtime 只通过显式接口依赖领域服务，不再反向导入 `main`。
- `main.py` 不定义业务模型和业务路由，目标控制在 100 行以内。
- 领域之间通过 ID、DTO 或协议接口协作，不直接读取其他领域内部表结构。

共享代码只提取真正存在的重复实现。API 与 Worker 共用的配置、embedding 客户端和任务协议放入可安装的 `shared/python/enterprise_kb` 包；纯业务专用代码保留在各服务内部。

### 4.2 入库 Worker

```text
services/worker/app/
  main.py                       # 进程入口与循环
  jobs.py                       # 任务领取、续约和最终状态
  pipeline.py                   # 解析→切片→索引编排
  parsing/
    common.py
    pdf.py
    docx.py
    spreadsheet.py
    presentation.py
    drawing.py
    image.py
  stores/
    metadata.py
    objects.py
    vectors.py
    fulltext.py
```

入库采用可恢复状态机：

1. MySQL 记录目标文档版本和幂等任务键。
2. 在不删除旧活动索引的情况下生成并写入新切片。
3. 每个外部写操作以文档版本和切片 ID 为幂等键。
4. 所有新索引完成后切换活动版本。
5. 异步清理旧版本；清理失败保留 tombstone 和可重试任务，不标记为完全成功。
6. 删除任务只有在对象、向量、全文索引完成清理或进入明确可重试状态后，才更新最终状态。

本次不引入额外消息队列。MySQL 任务表仍为事实来源，但增加领取租约、重试计数、阶段状态和错误分类。

### 4.3 前端

```text
services/frontend/src/
  app/
    AppShell.vue
    router.ts
    navigation.ts
    route-meta.d.ts
  shared/
    api/
    auth/
    composables/
    types/
    ui/
    styles/
  features/
    dashboard/
    knowledge/
    agents/
    studio/
    connections/
    model-gateway/
    prompts/
    agent-requests/
    users/
    audit/
    observability/
```

规则：

- 每个 Feature 包含自己的 API、类型、页面和局部组件。
- 导航由路由元数据生成，避免路由与菜单重复维护。
- 使用有类型的 API DTO，逐步消除 `any`。
- 抽取 `useAsyncAction`、`usePolling`、Toast、复制回退和统一错误处理。
- 暂不引入 Pinia；只有出现跨页面可变缓存需求时再评估。
- 页面按路由懒加载；样式拆为 tokens、base、utilities 和 Feature 局部样式。
- Modal 满足基本键盘和无障碍要求：dialog 语义、标题关联、Escape、焦点圈定和关闭后焦点恢复。

## 5. 数据模型治理

### 5.1 唯一事实来源

| 领域 | 唯一事实来源 | 处理方式 |
|---|---|---|
| 用户权限 | `department`、`user_department` | 旧角色表停止写入 |
| 知识库授权 | `knowledge_base_department_acl` | 文档继承知识库权限 |
| 知识库归属 | `knowledge_base.owner_department_id` | 表示业务所有者，不替代授权表 |
| 智能体可用部门 | `agent_department_acl` | 管理员为平台级例外 |
| 智能体知识范围 | `agent_knowledge_base` | 只保存绑定关系，不再存第二份全局检索策略 |
| 检索策略 | 已发布的智能体配置快照 | 版本化并可审计 |
| 模型路由 | `llm_gateway_profile` 或明确的系统默认 | 遗留模型字段停止写入 |
| 文档处理策略 | `knowledge_base.processing_config_json` | 只作用于新上传与手动重建 |

### 5.2 迁移治理

- 已执行的 `001`–`012` 永久保留，禁止修改、重排或删除。
- 新增 `schema_migration` 台账，记录版本、文件名、SHA-256、执行时间、执行者和成功状态。
- 新环境使用独立 `schema_snapshot.sql` 或迁移工具从零构建；历史数据修复脚本不再放入 Docker 自动初始化目录。
- 迁移程序在执行前检查顺序和校验和；发现已执行文件内容改变时直接失败。
- 每个迁移提供前置检查、数据影响说明、验证 SQL 和回退方式。MySQL DDL 无法完整事务回滚时必须先演练和备份。
- 应用启动只校验 schema 版本，不自动执行 DDL。

### 5.3 停用与删除

第一阶段：

- 停止写入 `app_role`、`user_role`，保留只读观察。
- 权限查询改为直接使用知识库 ACL，停止新增和同步 `document_department_acl`。
- 停止读取遗留 `agent.llm_model` 和 `agent_knowledge_base.retrieval_config_json`，将有效配置迁入新事实来源。
- 对候选对象增加运行时指标或审计，确认没有应用、脚本和人工报表访问。

观察一个已部署版本后，满足以下条件才能物理删除：

1. 已完成数据库备份和恢复演练。
2. 应用、脚本、报表和外部调用方确认无引用。
3. 数据比对无语义差异，观察期没有访问记录。
4. 新代码已不再写入，旧版本回退不依赖这些对象。

候选物理删除对象为 `app_role`、`user_role`、`document_department_acl` 及确认迁移完成的遗留配置列。`document_asset` 暂不列入自动删除：它需要先决定是否作为页图、OCR 区域、预览和 CAD 派生资产的正式模型；若最终不用，再按同样流程删除。

永久保留：文档版本、审计日志、智能体运行、配置版本、评测、反馈、任务历史及全部历史迁移文件。它们承担追溯、合规、恢复或质量复现职责。

## 6. API 兼容策略

- 当前 Vue 前端使用 `/api/v1/agents/{id}/runs` 作为持久化问答入口。
- 旧同步 `/api/v1/agents/{id}/chat` 在第一阶段标记为 deprecated，记录调用量并从仓库验收脚本移除。
- 一个观察版本内没有外部调用后，在下一主版本删除旧同步接口。
- 其他现有 `/api/v1` 路径、请求字段和响应语义在模块拆分期间保持不变。
- 新增准确的聚合统计接口供工作台使用，旧文档列表接口保持兼容。
- OpenAPI 是接口契约来源；关键响应增加显式 Pydantic 模型。

## 7. 部署与安全治理

- 移除业务未使用的 Redis 服务、环境变量和 `depends_on`；先停止容器但不删除数据目录，观察一个版本后再清理 Redis 数据。
- API 与 chat-runner 复用同一个明确命名的镜像，避免重复构建。
- 为 API、Worker、chat-runner、Frontend 和 Infinity 增加真实健康检查；`/readyz` 依赖不满足时返回 503。
- 生产示例默认 `LOCAL_TEST_MODE=false`，本地测试模式必须显式启用。
- MySQL 连接改为独立参数或正确编码的 DSN，支持强密码保留字符。
- API、Worker、Runner 使用按职责分离的数据库账号；迁移账号只在迁移命令中使用。
- 增加 `.dockerignore`，固定关键镜像版本；基础镜像 digest 在兼容性验证后逐步锁定。
- Nginx 增加安全响应头、静态资源长缓存、`index.html` no-cache 和合理压缩。
- 凭据继续只存服务器环境与密文列，不进入仓库、日志、测试输出或前端响应。

## 8. 测试与质量门禁

### 8.1 测试层次

1. 纯单元测试：权限决策、检索合并、配置校验、工作流引用、解析与任务状态转换。
2. Repository 集成测试：使用隔离 MySQL，验证事务、并发版本、权限矩阵和 SQL。
3. API 契约测试：FastAPI TestClient 覆盖认证、部门隔离、知识库、文档、智能体和管理员接口。
4. 迁移测试：空库执行、从生产结构快照升级、重复执行拒绝、校验和改变拒绝。
5. Worker 故障注入：分别模拟 MinIO、Milvus、OpenSearch 失败，验证任务可恢复且旧版本继续可用。
6. 前端组件测试：账号临时密码、统计、权限导航、轮询停止、知识库和智能体关键交互。
7. 浏览器验收：登录、上传、检索问答、切换页面恢复、工作流确认、管理员配置和越权拒绝。

### 8.2 CI 门禁

每次提交至少执行：

- Python 格式、静态检查、依赖检查和完整 pytest。
- 前端 lint、typecheck、组件测试和生产构建。
- SQL 迁移空库演练和版本校验。
- Dockerfile 构建及 Compose 配置解析。
- 敏感文件名与已跟踪密钥模式检查。

生产部署前额外执行隔离环境集成测试。测试脚本只能清理自己创建且带唯一标识的数据。

## 9. 清理清单

可在第一阶段删除：

- 无引用的 Python 导入和未调用的旧回答实现。
- 已确认无模板引用的死 CSS 和重复样式入口。
- Worker 未使用的 `cryptography` 运行依赖；测试专用依赖移入 dev/test requirements。
- 过时的 `upgrade-v05.sh`。
- 被统一验收脚本替代的 `smoke-test.sh` 与 `e2e-admin.py`。
- 可重建的 `dist`、pytest/TypeScript/Python 缓存以及不再用于部署的 `tmp` bundle。

需要保留或先归档：

- `outputs/` 中的历史调研与方案文档，迁移到明确的 `docs/archive/` 或加入忽略规则，不作为垃圾直接删除。
- 生产数据库备份和部署 bundle，保留期结束后由运维策略清理，不由应用仓库脚本自动删除。
- 当前仍参与恢复、审计和数据追溯的数据库表。

## 10. 实施顺序与回滚点

### 阶段一：基线与明确缺陷

增加质量工具和测试，修复临时密码、统计、健康检查、默认配置、DSN、Docker 上下文和文档漂移。该阶段不改变领域结构或生产数据库。

### 阶段二：后端模块化

按领域逐一迁移路由、服务、Repository 和 DTO；每迁移一个领域即运行契约测试。旧入口只负责路由注册，行为保持兼容。

### 阶段三：前端模块化

先建立类型、Feature API 和公共 Composable，再按 Users、Knowledge、Agents、Studio 顺序拆分。视觉和 URL 保持稳定。

### 阶段四：迁移与任务一致性

引入迁移台账、schema snapshot、任务租约和可恢复入库协议。先在隔离数据副本演练，再部署生产。

### 阶段五：停用部署与观察

停止 Redis 和候选冗余表写入，保留数据与回滚配置；发布一个观察版本并收集调用证据。

### 阶段六：物理清理

满足第 5.3 节门槛后，在维护窗口执行新增清理迁移。迁移前备份，迁移后进行权限、问答、索引和审计回归。

任何阶段失败都只回退该阶段的应用镜像和新增迁移；不得通过覆盖数据目录、重置数据库或删除已有索引来回退。

## 11. 完成标准

- `main.py` 只负责应用装配，业务接口按领域组织，运行时不再反向导入主模块。
- 所有权限校验与相应写操作在同一个事务上下文完成。
- 新环境与升级环境均由有台账的迁移流程构建，历史乱码修复不会作用于新数据。
- 前端核心 DTO 不再使用 `any`，四个大页面拆为可独立测试的组件。
- 临时密码不会跨账号或编辑流程残留；工作台统计来自后端聚合。
- Redis 不再是运行依赖；停用期间数据目录保留且有回退说明。
- 入库、重建和删除在单个外部存储失败时可以重试，不会丢失仍在使用的旧索引。
- 自动化门禁覆盖后端、前端、迁移、构建和关键权限矩阵。
- 全量业务验收通过，原有文档、索引、账号、知识库、智能体、连接器和审计数量经前后对账无非预期变化。
- 每项物理删除都有独立迁移、备份、恢复证据和观察期记录。

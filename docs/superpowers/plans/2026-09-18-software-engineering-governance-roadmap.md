# 企业智能体平台软件工程治理路线图

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此路线图引用的阶段计划。每个阶段在独立计划中以复选框跟踪，阶段之间以可部署、可回滚的提交为边界。

**目标：** 在不破坏现有业务数据和 `/api/v1` 契约的前提下，把平台渐进重构为可测试、可迁移、可恢复的模块化单体，并清理已证明无效的代码、依赖和部署服务。

**架构：** 后端按领域拆成 Router、Schema、Service、Repository，并以显式运行时接口连接检索、模型、MCP 和任务执行；前端按 App、Shared、Feature 分包。数据库与异构存储采用先建立台账和恢复协议、再停写观察、最后物理清理的顺序。

**技术栈：** FastAPI、PyMySQL、Pydantic、Vue 3、Vue Router、TypeScript、Vitest、Vue Test Utils、MySQL 8.4、MinIO、Milvus、OpenSearch、Infinity、Docker Compose、GitHub Actions。

**规格：** `docs/superpowers/specs/2026-09-18-software-engineering-governance-design.md`

## 全局约束

- 已执行的 `database/mysql/001_initial_schema.sql` 至 `database/mysql/012_platform_quality_runtime.sql` 永久保留且内容不修改。
- 不引入微服务、Kubernetes、消息中间件、ORM 或低代码工作流引擎。
- 模块化期间保持现有 `/api/v1` URL、请求字段和响应语义；旧同步问答接口只做弃用观测，不在本轮直接删除。
- 不删除生产文档、对象、索引、账号、会话、审计、连接器、配置版本、评测、反馈或任务历史。
- 候选冗余数据库对象必须先停止写入并观察一个已部署版本，满足备份、恢复演练、无引用和数据对账条件后才可物理删除。
- 所有功能修改和缺陷修复遵循测试先行的红—绿—重构循环。
- 凭据只存在服务器环境变量或加密列，不进入 Git、日志、测试输出和前端响应。
- 每个阶段必须能独立部署、验证和回退；不得通过删除数据目录或重置数据库回退。

---

## 阶段与任务序列

### 阶段一：工程基础与明确缺陷

执行计划：`docs/superpowers/plans/2026-09-18-governance-foundation.md`

1. 建立前端组件测试、独立 typecheck 和 lint 门禁，修复临时密码跨流程残留。
2. 新增后端工作台聚合统计 API，前端改为一次有类型请求，消除统计截断和 N+1。
3. 建立强类型运行配置与安全 DSN 构造，生产示例默认关闭本地测试模式。
4. 修正 readiness 语义和应用容器健康检查，统一部署端口文档。
5. 缩小 Docker 构建上下文、复用 API 镜像、增加前后端 CI 门禁并删除已被新验收脚本替代的旧脚本。

阶段出口：完整 pytest、前端 lint/typecheck/test/build、Compose 配置解析均通过；不改生产表结构。

### 阶段二：后端模块化

阶段计划在阶段一合并接口后生成，固定使用以下任务边界：

1. 创建 `services/api/app/core/`，迁移配置、数据库事务、安全、审计和依赖注入；为 Unit of Work 编写事务提交与回滚集成测试。
2. 迁移 `auth` 与 `users` 领域，保证权限判断和写入使用同一事务连接；增加登录、改密、账号部门隔离契约测试。
3. 迁移 `knowledge` 与 `documents` 领域，集中 ACL、文件夹、上传、删除和重建的 Repository；增加越权和继承授权测试。
4. 迁移 `agents`、`connectors` 与 `studio` 领域，移除 `platform.install(app, main_module)` 和 `tasks.install(app, main_module)` 反向注入。
5. 将检索、模型网关、MCP、聊天任务、工作流和评测迁入 `runtime/` 显式接口；标记同步 `/chat` 为 deprecated 并记录调用量。
6. 收敛 `main.py` 为应用工厂、生命周期和路由注册，目标少于 100 行；生成 OpenAPI 契约快照并运行全部 API 回归。

阶段出口：领域 Router 不直接执行 SQL，Service 用一个 Unit of Work 完成权限检查和写入，运行时不反向导入 `main`。

### 阶段三：前端模块化

阶段计划在后端 OpenAPI 契约稳定后生成，固定使用以下任务边界：

1. 创建 `src/app/` 和 `src/shared/`，集中路由元信息、导航、认证、API 错误、Toast、复制回退、异步动作和轮询。
2. 迁移 Users Feature，使用显式 DTO 和可访问 Modal；覆盖创建、编辑、重置密码与焦点恢复测试。
3. 迁移 Knowledge Feature，拆分知识库选择、目录树、上传、处理设置和文档表格；保持 URL 与视觉行为稳定。
4. 迁移 Agents Feature，拆分目录、会话、运行状态和工作流确认；验证导航切换期间任务状态恢复。
5. 迁移 Studio、Connections、Model Gateway、Prompts、Agent Requests、Audit 和 Observability Feature。
6. 将页面按路由懒加载，删除无引用样式与旧入口，核心 DTO 消除 `any`，运行组件测试和浏览器验收。

阶段出口：导航由路由元数据生成，四个大页面拆为可独立测试的组件，核心业务 API 不再使用 `any`。

### 阶段四：迁移与入库一致性

阶段计划在领域 Repository 稳定后生成，固定使用以下任务边界：

1. 新增 `schema_migration` 台账、SHA-256 校验和、顺序检查和独立迁移命令；应用启动只校验版本。
2. 生成新环境 `schema_snapshot.sql`，将历史数据修复脚本移出 Docker 自动初始化目录；测试空库和生产结构快照升级。
3. 为文档任务增加幂等键、领取租约、重试计数、阶段状态和错误分类。
4. 重构 Worker 为 jobs、pipeline、parsing 和 stores；用文档版本与切片 ID 作为外部写入幂等键。
5. 实现“写新索引—切换活动版本—异步清理旧版本”的重建协议，并对 MinIO、Milvus、OpenSearch 分别做故障注入。
6. 实现删除 tombstone 与可重试清理任务，确保外部存储失败不会被错误标记为完全成功。

阶段出口：迁移可验证且不可静默篡改；重建失败时旧索引继续可用；删除失败有明确可恢复状态。

### 阶段五：停用部署与观察

1. 停止写入 `app_role`、`user_role` 与 `document_department_acl`，所有权限读取使用部门、知识库 ACL 和智能体 ACL 的唯一事实来源。
2. 迁移并停止读取 `agent.llm_model` 与 `agent_knowledge_base.retrieval_config_json` 的遗留配置。
3. 从运行时、Compose 和环境示例移除 Redis 依赖，但保留 Redis 数据目录和回退说明一个发布周期。
4. 为候选对象和旧同步 `/chat` 记录调用审计，发布观察版本并完成数据计数、权限矩阵和业务验收对账。

阶段出口：观察期内无旧对象访问、无权限差异、无旧同步问答调用，且恢复演练完成。

### 阶段六：物理清理

1. 在维护窗口前备份 MySQL、对象元数据和索引清单，并验证恢复。
2. 通过独立迁移删除已满足门槛的角色表、文档 ACL 表和遗留配置列；`document_asset` 仅在明确不承载页图、OCR、预览或 CAD 派生资产后单独处理。
3. 清理 Redis 数据目录、归档仓库外历史产物，执行权限、上传、问答、索引、审计和数据计数回归。

阶段出口：每个物理删除都有迁移、备份、恢复、观察和验收证据。

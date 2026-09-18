# 后端模块化重构实施计划

> **执行方式：** 在隔离分支 `codex/software-engineering-governance` 中按任务顺序执行。每个任务先写失败测试，再做最小实现，通过任务级审查后提交。全部任务完成前不部署生产环境，不删除数据库对象，不改变现有 `/api/v1` 外部契约。

**目标：** 把当前约 2200 行的 FastAPI 单文件和 `platform.install(app, main_module)`、`tasks.core()` 反向依赖，重构为可测试的模块化单体。权限判断与业务写入进入同一事务边界，路由只负责 HTTP 适配，领域服务负责业务规则，仓储负责 SQL，运行时组件负责模型、MCP 和异步聊天执行。

**保留边界：** 保留现有 MySQL、MinIO、Milvus、OpenSearch、Redis、Infinity、Worker、Chat Runner 进程和数据；保留数据库迁移 `001` 至 `012`；保留现有 URL、请求字段、响应字段和权限语义；旧模块只允许在迁移期间作为兼容转发层存在，并在任务 8 确认无引用后删除。

**不在本阶段引入：** 微服务拆分、Kubernetes、消息中间件、ORM、低代码工作流引擎、数据库表删除、历史迁移改写。

## 目标目录

```text
services/api/app/
├── main.py
├── core/
│   ├── config.py
│   ├── database.py
│   ├── errors.py
│   ├── security.py
│   ├── audit.py
│   └── dependencies.py
├── domains/
│   ├── auth/
│   ├── users/
│   ├── knowledge/
│   ├── documents/
│   ├── agents/
│   ├── prompts/
│   ├── requests/
│   ├── model_gateway/
│   ├── connectors/
│   ├── studio/
│   └── observability/
├── infrastructure/
│   ├── object_store.py
│   ├── vector_store.py
│   └── search_index.py
└── runtime/
    ├── chat.py
    ├── chat_tasks.py
    ├── mcp.py
    └── workflows.py
```

每个业务域按需包含 `schemas.py`、`repository.py`、`service.py`、`router.py`。只在该域确实需要时创建文件，避免空壳层。

## 全局验收标准

- 所有 API 路径与 HTTP 方法和重构前快照一致；有意废弃的同步聊天接口仍可用并在 OpenAPI 标记 `deprecated`。
- 权限校验与受保护写入使用同一个 `UnitOfWork`、同一个数据库连接。
- Repository 不导入 FastAPI，不抛 `HTTPException`；Router 不包含 SQL。
- `services/api/app/main.py` 最终不超过 100 行，只负责应用创建、异常处理器、生命周期和路由注册。
- `services/api/app/platform.py`、`services/api/app/tasks.py` 以及旧的顶层配置/数据库/安全兼容模块在无引用后删除。
- 全量 Python 测试、前端质量门、Compose 静态契约、仓库卫生检查全部通过。
- Docker 守护进程可用时再完成镜像构建和空卷运行验证；守护进程不可用时明确记录为部署前验证项，不能写成已通过。

---

## 任务 1：锁定 HTTP 契约并建立事务核心

**新增文件**

- `tests/api/test_openapi_contract.py`
- `tests/api/test_unit_of_work.py`
- `tests/fixtures/api_v1_routes.json`
- `services/api/app/core/__init__.py`
- `services/api/app/core/database.py`
- `services/api/app/core/errors.py`
- `services/api/app/core/dependencies.py`

**修改文件**

- `services/api/app/database.py`
- `services/api/app/main.py`

### 1.1 先固化重构前契约

从当前 `app.openapi()` 导出所有 `/api/v1` 路径、HTTP 方法、操作 ID、弃用标记，保存为 `tests/fixtures/api_v1_routes.json`。测试只比较稳定字段，不快照服务器地址、描述文案和 Schema 展开顺序。

```python
def route_contract(schema: dict) -> list[dict]:
    rows = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            rows.append({
                "path": path,
                "method": method.upper(),
                "operation_id": operation["operationId"],
                "deprecated": bool(operation.get("deprecated", False)),
            })
    return sorted(rows, key=lambda row: (row["path"], row["method"]))
```

运行并确认测试通过后，后面的路由迁移均以该快照为兼容门。

### 1.2 测试事务生命周期

覆盖以下行为：进入上下文只创建一个连接和游标；显式 `commit()` 只提交一次；异常自动回滚；未显式提交的正常退出也回滚；游标和连接始终关闭；重复提交或回滚不会二次执行。

### 1.3 实现 `UnitOfWork`

`UnitOfWork` 接受连接工厂以便测试，公开 `connection`、`cursor`、`commit()`、`rollback()`，默认使用当前 MySQL 连接工厂。依赖函数 `get_uow()` 使用 `yield` 保证清理。业务服务必须显式提交，默认回滚可防止遗漏提交。

`core/errors.py` 定义不依赖 FastAPI 的 `ApplicationError`、`AuthenticationError`、`AuthorizationError`、`NotFoundError`、`ConflictError`、`ValidationError`。HTTP 状态映射统一放到最终应用异常处理器。

### 1.4 保持兼容

当前 `app.database` 暂时只重导出 `core.database` 的公开接口。`main.py` 尚未迁移的代码继续运行；新代码只允许导入 `app.core.database`。

### 1.5 验证并提交

```bash
python -m pytest tests/api/test_openapi_contract.py tests/api/test_unit_of_work.py -q
python -m pytest -q
git diff --check
git add services/api/app/core services/api/app/database.py services/api/app/main.py tests/api tests/fixtures/api_v1_routes.json
git commit -m "refactor: establish API transaction foundation"
```

---

## 任务 2：迁移认证、部门和用户管理

**新增文件**

- `services/api/app/domains/auth/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/domains/users/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `tests/api/test_auth_routes.py`
- `tests/api/test_user_routes.py`
- `tests/api/test_user_transactions.py`

**修改文件**

- `services/api/app/main.py`
- `services/api/app/core/dependencies.py`

### 2.1 先写路由和事务测试

覆盖登录成功/失败、`/auth/me`、修改密码、退出、部门列表、账号创建/编辑/重置密码/启停/删除。覆盖普通部门无法访问管理员接口，平台管理员可访问。事务测试必须证明“管理员权限查询”和“用户写入”共享同一连接，并在写入异常时整体回滚。

### 2.2 迁移认证

Repository 只读取会话和账号数据；Service 处理密码验证、令牌签发/失效和账号状态；Router 处理 Cookie/Header 与响应模型。保留原 Cookie 名、过期时间和响应字段。

### 2.3 迁移用户与部门

把平台管理员部门判定封装到 users Service。临时密码仍只在创建或重置响应中出现一次，数据库只保存哈希；列表和详情不得返回密码或哈希。编辑、启停、删除均在同一 `UnitOfWork` 中完成授权与写入。

### 2.4 删除 `main.py` 对应代码段并验证

删除已迁移的 Schema、SQL 辅助函数和路由，注册两个 Router。运行契约快照，确保 operation ID 不变；若 FastAPI 因函数移动改变默认 operation ID，则在路由装饰器显式填写旧值。

```bash
python -m pytest tests/api/test_auth_routes.py tests/api/test_user_routes.py tests/api/test_user_transactions.py tests/api/test_openapi_contract.py -q
python -m pytest -q
git diff --check
git commit -am "refactor: modularize authentication and users"
```

---

## 任务 3：迁移知识库、授权和目录树

**新增文件**

- `services/api/app/domains/knowledge/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `tests/api/test_knowledge_routes.py`
- `tests/api/test_knowledge_permissions.py`
- `tests/api/test_knowledge_transactions.py`

**修改文件**

- `services/api/app/main.py`
- `services/api/app/core/dependencies.py`

### 3.1 写特征和权限测试

覆盖知识库列表/创建/编辑/归档、授权范围读取与修改、目录树读取/创建/改名/移动/删除。覆盖平台管理员全局访问、普通部门只见已授权知识库、跨部门读写被拒绝、非空目录删除被拒绝。

### 3.2 迁移业务规则

Repository 承担知识库、授权表和目录表 SQL；Service 统一处理密级、部门授权、目录父子关系和循环移动检查；Router 只做参数解析与响应转换。知识库授权校验和写入必须在同一 `UnitOfWork` 完成。

### 3.3 保持索引语义

目录只作为文档组织和检索过滤元数据，不创建新的 Milvus 集合或 OpenSearch 索引。知识库归档不删除文档、切片、对象或索引数据。

### 3.4 验证并提交

```bash
python -m pytest tests/api/test_knowledge_routes.py tests/api/test_knowledge_permissions.py tests/api/test_knowledge_transactions.py tests/api/test_openapi_contract.py -q
python -m pytest -q
git diff --check
git commit -am "refactor: modularize knowledge management"
```

---

## 任务 4：迁移文档、版本、摄取任务和外部存储边界

**新增文件**

- `services/api/app/domains/documents/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/infrastructure/{__init__.py,object_store.py,vector_store.py,search_index.py}`
- `tests/api/test_document_routes.py`
- `tests/api/test_document_lifecycle.py`
- `tests/api/test_ingestion_job_routes.py`

**修改文件**

- `services/api/app/main.py`
- `services/api/app/core/dependencies.py`

### 4.1 写生命周期测试

覆盖多文件上传、文件类型和大小校验、文档列表/详情/版本、处理状态、重建、删除与任务查询。用伪对象存储断言上传失败会回滚元数据并清理已写对象；删除流程断言数据库状态先进入可恢复状态，再调用对象/向量/全文索引删除适配器；任一外部删除失败必须保留可重试状态和错误记录。

### 4.2 提取基础设施适配器

将 MinIO、Milvus、OpenSearch 操作包装为明确接口。Service 只依赖接口，不直接构造客户端；生产依赖由 `core.dependencies` 注入，测试使用内存伪实现。

### 4.3 迁移文档事务

文档、版本、摄取任务和审计记录的数据库写入使用同一事务。外部系统操作不能伪装成数据库原子事务，采用“数据库状态 + 幂等任务 + 可重试错误”保证最终一致性。保留现有 Worker 消费格式。

### 4.4 验证并提交

```bash
python -m pytest tests/api/test_document_routes.py tests/api/test_document_lifecycle.py tests/api/test_ingestion_job_routes.py tests/api/test_openapi_contract.py -q
python -m pytest -q
git diff --check
git commit -am "refactor: modularize document lifecycle"
```

---

## 任务 5：迁移智能体、会话和聊天运行时

**新增文件**

- `services/api/app/domains/agents/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/runtime/{__init__.py,chat.py,chat_tasks.py}`
- `tests/api/test_agent_routes.py`
- `tests/api/test_conversation_routes.py`
- `tests/api/test_chat_task_routes.py`
- `tests/api/test_agent_authorization.py`

**修改文件**

- `services/api/app/main.py`
- `services/api/app/tasks.py`
- `services/api/app/chat_worker.py`

### 5.1 写会话和异步状态测试

覆盖智能体列表/详情/管理、部门授权、知识库绑定、会话新建/列表/消息/删除、异步任务提交/轮询/取消。覆盖只有任务所属用户可读取任务；页面离开后返回仍能读取正在运行状态；并发会话互不污染。

### 5.2 迁移智能体领域

Repository 管理智能体配置、部门授权、知识范围、会话和消息；Service 计算最终授权知识范围并验证调用权限。Router 不再接受客户端临时扩大检索范围。

### 5.3 消除聊天反向导入

把聊天执行函数迁到 `runtime/chat.py`，异步任务迁到 `runtime/chat_tasks.py`。`chat_worker.py` 直接调用公开运行时接口，不再通过 `tasks.core()` 动态导入 `main`。

同步聊天接口保持兼容，显式标记 `deprecated=True`，调用时写审计动作 `agent.chat.sync_deprecated`，为观察实际使用量提供证据。

### 5.4 验证并提交

```bash
python -m pytest tests/api/test_agent_routes.py tests/api/test_conversation_routes.py tests/api/test_chat_task_routes.py tests/api/test_agent_authorization.py tests/api/test_openapi_contract.py -q
python -m pytest -q
git diff --check
git commit -am "refactor: modularize agents and chat runtime"
```

---

## 任务 6：迁移提示词、智能体申请、模型网关和系统连接

**新增文件**

- `services/api/app/domains/prompts/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/domains/requests/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/domains/model_gateway/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/domains/connectors/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/runtime/mcp.py`
- `tests/api/test_prompt_routes.py`
- `tests/api/test_agent_request_routes.py`
- `tests/api/test_model_gateway_routes.py`
- `tests/api/test_connector_routes.py`
- `tests/api/test_connector_security.py`

**修改文件**

- `services/api/app/main.py`

### 6.1 写权限和密钥测试

覆盖个人提示词隔离、申请创建/查看/处理、模型供应商配置、智能体模型绑定、连接器创建/编辑/探测/工具列表/调用。断言 API 密钥、Bearer Token、加密材料和哈希不会出现在列表、日志、异常详情或审计详情中。

### 6.2 迁移管理域

各域独立 Repository 和 Service；模型网关和连接器写操作仅平台管理员可用。个人提示词严格按 `owner_user_id` 隔离；申请人只能读自己的申请，管理员可处理全部申请。

### 6.3 提取 MCP 运行时

`runtime/mcp.py` 负责 Streamable HTTP 协议、鉴权头、超时、错误标准化和工具调用。连接器 Service 负责权限、配置选择与审计；Token 只从加密存储解密到进程内短生命周期变量。

### 6.4 验证并提交

```bash
python -m pytest tests/api/test_prompt_routes.py tests/api/test_agent_request_routes.py tests/api/test_model_gateway_routes.py tests/api/test_connector_routes.py tests/api/test_connector_security.py tests/api/test_openapi_contract.py -q
python -m pytest -q
git diff --check
git commit -am "refactor: modularize platform administration"
```

---

## 任务 7：迁移工作流、评测和可观测性

**新增文件**

- `services/api/app/domains/studio/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/domains/observability/{__init__.py,schemas.py,repository.py,service.py,router.py}`
- `services/api/app/runtime/workflows.py`
- `tests/api/test_studio_routes.py`
- `tests/api/test_evaluation_routes.py`
- `tests/api/test_observability_routes.py`
- `tests/api/test_dashboard_routes.py`

**修改文件**

- `services/api/app/platform.py`
- `services/api/app/main.py`

### 7.1 写行为测试

覆盖工具目录、工作流定义/运行、评测数据集/用例/执行/结果、审计日志、调用追踪、检索反馈和 Dashboard 统计。断言普通用户只能读取授权范围，平台管理员才可进行全局运维操作。

### 7.2 迁移 Studio 和运行时

工作流定义与评测管理进入 Studio 域；实际节点执行进入 `runtime/workflows.py`。通过显式构造参数传入模型、知识检索和 MCP 工具能力，不再把 `main` 模块作为服务定位器。

### 7.3 迁移可观测性

统一审计、追踪、反馈和 Dashboard 查询入口。Dashboard 保持单次聚合查询；日志与追踪响应继续进行敏感字段脱敏。

### 7.4 移除 `platform.install(app, main_module)`

完成路由注册后删除 `platform.py`，全仓检索确认不存在 `platform.install`、`main_module` 服务定位器或运行时反向导入。

### 7.5 验证并提交

```bash
python -m pytest tests/api/test_studio_routes.py tests/api/test_evaluation_routes.py tests/api/test_observability_routes.py tests/api/test_dashboard_routes.py tests/api/test_openapi_contract.py -q
python -m pytest -q
rg "platform\.install|main_module|tasks\.core\(" services tests
git diff --check
git commit -am "refactor: modularize studio and observability"
```

预期 `rg` 无匹配；若只剩测试中的否定断言，测试文件中的匹配可保留。

---

## 任务 8：应用装配、兼容层清理和阶段验收

**新增文件**

- `services/api/app/application.py`
- `tests/api/test_application_factory.py`
- `tests/api/test_layer_boundaries.py`

**修改文件**

- `services/api/app/main.py`
- `services/api/app/chat_worker.py`
- `services/api/Dockerfile`
- `README.md`
- `docs/architecture.md`

**删除文件**

- `services/api/app/config.py`
- `services/api/app/database.py`
- `services/api/app/security.py`
- `services/api/app/audit.py`
- `services/api/app/tasks.py`

删除动作只有在 `rg` 证明所有生产代码均已迁移到 `core`、`domains`、`infrastructure`、`runtime` 后执行；如果某个旧模块仍有调用方，先迁移调用方，不保留双实现。

### 8.1 创建应用工厂

`create_app()` 负责：创建 FastAPI、注册统一异常处理器、注册生命周期、注册所有 Router。`main.py` 仅导入并暴露 `app = create_app()`。测试可传入依赖覆盖，不连接真实数据库或外部服务。

### 8.2 添加层级约束测试

AST 测试禁止以下依赖：Repository 导入 `fastapi`；Domain 导入 `app.main`；Runtime 导入 `app.main`；Router 直接导入 MySQL 驱动；生产代码出现 `tasks.core()`、`platform.install()`。同时断言 `main.py` 不超过 100 行。

### 8.3 完成兼容清理

对比路由快照；检查旧模块零引用后删除兼容层。数据库迁移和表结构不做删除。更新 README 的架构图、开发命令、事务规则、依赖方向和扩展新业务域的步骤。

### 8.4 全量静态与自动化验证

```bash
python -m pytest -q
npm --prefix services/frontend run lint
npm --prefix services/frontend run typecheck
npm --prefix services/frontend run test -- --run
npm --prefix services/frontend run build
docker compose --env-file .env.example -f deploy/docker-compose.yml config
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config
python scripts/check_repository_hygiene.py
git diff --check b431c23..HEAD
```

### 8.5 Docker 守护进程可用时的运行验收

在独立测试数据目录执行，不挂载生产数据：

```bash
docker compose --env-file .env.test -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml build api worker chat-runner frontend
docker compose --env-file .env.test -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml up -d
curl --fail http://127.0.0.1:18000/healthz
curl --fail http://127.0.0.1:18000/readyz
docker compose --env-file .env.test -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml ps
```

验证首次空卷自动创建 MinIO 桶；重复启动 `minio-init` 仍成功；Infinity 慢启动期间 API 不提前进入 Ready；MySQL、MinIO、Milvus、OpenSearch、Embedding、Rerank 分别异常时 `/readyz` 返回 503 且不泄漏凭据。

### 8.6 最终审查与提交

完成一次全分支代码审查，只修复本阶段引入的问题。记录 Docker 实测证据或明确的环境阻塞。最后提交：

```bash
git add services/api tests README.md docs
git commit -m "refactor: complete backend modular monolith"
git status --short
```

---

## 迁移顺序与回滚原则

1. 每个域在原路由仍存在时先补测试，再移动代码，最后删除原代码段。
2. 每个任务独立提交；发现回归时优先回退单个任务提交，不回滚数据库。
3. 本阶段不新增破坏性迁移，因此回滚代码不会要求恢复知识库数据。
4. 外部存储操作失败通过状态和幂等任务恢复，禁止在异常处理中直接硬删除数据库历史记录。
5. 阶段二全部通过后才进入前端状态管理和页面结构治理阶段。

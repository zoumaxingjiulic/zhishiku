# 工程基础与明确缺陷实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 建立可持续的前后端质量门禁并修复已经确认的临时密码、工作台统计、配置、健康检查和 Docker 构建问题，同时保持现有业务接口兼容且不改变生产数据库结构。

**架构：** 本阶段只增加边界测试和小型基础设施抽取，不开始大规模领域拆分。新增行为通过窄接口落地：工作台聚合为只读 API，配置通过显式构造函数加载，readiness 通过可注入检查器返回正确 HTTP 状态。

**技术栈：** FastAPI、PyMySQL、Pydantic、pytest、Vue 3、TypeScript、Vitest、Vue Test Utils、ESLint、Docker Compose、GitHub Actions。

**规格：** `docs/superpowers/specs/2026-09-18-software-engineering-governance-design.md`

## 全局约束

- 不修改 `database/mysql/001_initial_schema.sql` 至 `database/mysql/012_platform_quality_runtime.sql`。
- 不改变现有 `/api/v1` 路径、请求字段和既有响应语义；本阶段只新增 `/api/v1/dashboard/stats`。
- 不引入 ORM、微服务、Kubernetes、消息中间件或低代码工作流引擎。
- 不执行生产数据库 DDL，不删除生产数据、索引或数据目录。
- `LOCAL_TEST_MODE` 和 `EMBEDDING_PROVIDER=local_hash` 仅用于显式本地验收，生产示例默认禁用。
- 凭据不得出现在 Git、测试输出、日志或前端响应。
- 每个行为修改必须先看到相关测试因缺少该行为而失败，再用最少实现使其通过。

---

## 文件结构

- `services/frontend/src/shared/types/users.ts`：用户与部门 DTO。
- `services/frontend/src/features/users/temporaryPassword.ts`：临时密码一次性状态机，防止跨流程复用。
- `services/frontend/src/pages/UsersPage.vue`：调用一次性状态机并保留现有页面契约。
- `services/frontend/src/pages/__tests__/UsersPage.spec.ts`：用户创建、编辑、重置密码的组件回归测试。
- `services/frontend/src/test/setup.ts`：Vitest DOM 测试初始化。
- `services/frontend/eslint.config.js`：Vue 与 TypeScript lint 配置。
- `services/api/app/dashboard.py`：工作台统计查询与响应模型。
- `tests/test_dashboard.py`：聚合统计 SQL 和 API 序列化测试。
- `services/frontend/src/shared/types/dashboard.ts`：工作台统计 DTO。
- `services/frontend/src/pages/DashboardPage.vue`：一次请求渲染准确统计。
- `services/frontend/src/pages/__tests__/DashboardPage.spec.ts`：工作台统计组件测试。
- `shared/python/enterprise_kb/config.py`：环境读取、生产安全校验和数据库连接参数。
- `shared/python/enterprise_kb/__init__.py`：共享包导出。
- `services/api/app/config.py`、`services/worker/app/main.py`：使用共享配置构造函数。
- `tests/test_config.py`：保留字符密码、布尔配置和生产保护测试。
- `services/api/app/readiness.py`：依赖检查、状态码和诊断结果。
- `tests/test_readiness.py`：全健康和单依赖失败语义测试。
- `services/api/app/main.py`：注册聚合统计和 readiness 路由，保持其他行为不变。
- `services/api/Dockerfile`、`services/worker/Dockerfile`、`services/frontend/Dockerfile`：容器内健康检查所需工具和稳定构建。
- `.dockerignore`、`services/api/.dockerignore`、`services/worker/.dockerignore`、`services/frontend/.dockerignore`：排除缓存、依赖、产物和本地凭据。
- `deploy/docker-compose.yml`：API/Runner 镜像复用和应用健康检查。
- `services/frontend/nginx.conf`：安全响应头、静态资源缓存、入口 no-cache 与压缩。
- `.github/workflows/quality.yml`：后端、前端和 Compose 基础门禁。
- `.env.example`、`README.md`、`deploy/README.md`：安全默认值、统一端口和验证命令。

### 任务 1：前端质量工具与临时密码一次性状态

**文件：**
- 修改：`services/frontend/package.json`
- 修改：`services/frontend/package-lock.json`
- 修改：`services/frontend/vite.config.ts`
- 创建：`services/frontend/eslint.config.js`
- 创建：`services/frontend/src/test/setup.ts`
- 创建：`services/frontend/src/shared/types/users.ts`
- 创建：`services/frontend/src/features/users/temporaryPassword.ts`
- 创建：`services/frontend/src/pages/__tests__/UsersPage.spec.ts`
- 修改：`services/frontend/src/pages/UsersPage.vue`

- [ ] **步骤 1：安装测试与 lint 工具并注册脚本**

运行：

```powershell
cd services/frontend
npm install --save-dev vitest @vue/test-utils jsdom @testing-library/vue @testing-library/jest-dom eslint @eslint/js typescript-eslint eslint-plugin-vue
```

在 `package.json` 中注册：

```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "vue-tsc -b",
    "lint": "eslint src --max-warnings=0",
    "build": "vue-tsc -b && vite build"
  }
}
```

- [ ] **步骤 2：编写临时密码残留的失败测试**

测试必须真实挂载 `UsersPage.vue`，使用完整用户、部门和创建响应 fixture。场景：创建账号显示临时密码，关闭后打开现有账号编辑并保存，密码弹窗不得再次出现；重置密码后只展示当前账号的新临时密码。

关键断言：

```ts
expect(screen.queryByText("临时密码（仅显示一次）")).not.toBeInTheDocument();
expect(screen.queryByText("FIRST-TEMP-PASSWORD")).not.toBeInTheDocument();
expect(screen.getByText("SECOND-TEMP-PASSWORD")).toBeInTheDocument();
```

- [ ] **步骤 3：运行测试验证红灯**

运行：`npm test -- UsersPage.spec.ts`

预期：编辑已有用户后错误地重新出现 `FIRST-TEMP-PASSWORD`，测试以行为断言失败。

- [ ] **步骤 4：实现最小的一次性临时密码状态机和 DTO**

`temporaryPassword.ts` 提供以下接口：

```ts
export interface TemporaryPasswordNotice {
  username: string;
  temporaryPassword: string;
}

export function useTemporaryPassword() {
  const notice = ref<TemporaryPasswordNotice | null>(null);
  function show(username: string, temporaryPassword: string): void;
  function clear(): void;
  return { notice: readonly(notice), show, clear };
}
```

`openUser`、打开部门弹窗、关闭密码弹窗和保存编辑账号前都清空旧通知；只有创建或重置接口返回新密码时调用 `show`。模板只在 `notice` 非空时渲染密码弹窗。

- [ ] **步骤 5：运行前端聚焦与完整门禁**

运行：

```powershell
npm test -- UsersPage.spec.ts
npm run lint
npm run typecheck
npm run build
```

预期：测试、lint、类型检查和构建全部退出码为 0。

- [ ] **步骤 6：提交**

```powershell
git add services/frontend
git commit -m "test: add frontend quality gate and secure temporary passwords"
```

### 任务 2：准确的工作台聚合统计

**文件：**
- 创建：`services/api/app/dashboard.py`
- 修改：`services/api/app/main.py`
- 创建：`tests/test_dashboard.py`
- 创建：`services/frontend/src/shared/types/dashboard.ts`
- 创建：`services/frontend/src/pages/__tests__/DashboardPage.spec.ts`
- 修改：`services/frontend/src/pages/DashboardPage.vue`

- [ ] **步骤 1：编写后端聚合统计失败测试**

测试构造一个记录 25 份可访问文档、3 个知识库、2 个智能体和状态分布的受控 cursor，调用纯函数 `load_dashboard_stats(cursor, user)`，断言精确字面量：

```python
assert result == {
    "knowledge_bases": 3,
    "documents": 25,
    "agents": 2,
    "processing": 4,
    "succeeded": 19,
    "failed": 2,
}
```

同时断言普通用户查询参数包含其 `department_id`，平台管理员走管理员聚合分支。

- [ ] **步骤 2：运行后端测试验证红灯**

运行：`E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q`

预期：因 `app.dashboard` 尚不存在而失败。

- [ ] **步骤 3：实现一次数据库往返的统计查询与 API**

`dashboard.py` 定义显式 Pydantic 响应模型 `DashboardStats` 和 `load_dashboard_stats`。SQL 使用条件聚合，文档统计不经过列表分页；`main.py` 新增：

```python
@app.get("/api/v1/dashboard/stats", response_model=DashboardStats, tags=["dashboard"])
def dashboard_stats(user: dict = Depends(current_user)) -> DashboardStats:
    with connect() as conn, conn.cursor() as cursor:
        return load_dashboard_stats(cursor, user)
```

- [ ] **步骤 4：编写前端失败测试**

挂载 `DashboardPage.vue`，拦截唯一一次 `/api/v1/dashboard/stats` 请求并返回固定 DTO；断言 25 份文档、19 份已入库和 4 个处理中任务正确渲染，同时断言不再请求 `/api/v1/documents`。

- [ ] **步骤 5：运行前端测试验证红灯**

运行：`npm test -- DashboardPage.spec.ts`

预期：现有页面请求知识库、智能体和每个知识库的文档列表，唯一聚合请求断言失败。

- [ ] **步骤 6：实现有类型的一次请求**

定义：

```ts
export interface DashboardStats {
  knowledge_bases: number;
  documents: number;
  agents: number;
  processing: number;
  succeeded: number;
  failed: number;
}
```

页面通过 `api<DashboardStats>("/api/v1/dashboard/stats")` 加载，不再按知识库遍历文档接口。

- [ ] **步骤 7：验证并提交**

运行：

```powershell
E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q
cd services/frontend
npm test -- DashboardPage.spec.ts
npm run typecheck
```

提交：

```powershell
git add services/api/app/dashboard.py services/api/app/main.py tests/test_dashboard.py services/frontend/src/shared/types/dashboard.ts services/frontend/src/pages/DashboardPage.vue services/frontend/src/pages/__tests__/DashboardPage.spec.ts
git commit -m "feat: add accurate dashboard statistics"
```

### 任务 3：安全配置与数据库连接参数

**文件：**
- 创建：`shared/python/enterprise_kb/__init__.py`
- 创建：`shared/python/enterprise_kb/config.py`
- 修改：`services/api/app/config.py`
- 修改：`services/api/app/database.py`
- 修改：`services/worker/app/main.py`
- 创建：`tests/test_config.py`
- 修改：`.env.example`
- 修改：`deploy/docker-compose.yml`

- [ ] **步骤 1：编写配置失败测试**

覆盖三个真实边界：

```python
def test_mysql_connection_params_preserve_reserved_password_characters():
    env = {"MYSQL_HOST": "mysql", "MYSQL_PORT": "3306", "MYSQL_USER": "kb_app", "MYSQL_PASSWORD": "p@ss:/?#%", "MYSQL_DATABASE": "enterprise_kb"}
    assert mysql_connection_params(env)["password"] == "p@ss:/?#%"

def test_production_rejects_local_test_mode(): ...
def test_bool_parser_rejects_unknown_text(): ...
```

生产校验必须在 `APP_ENV=production` 且 `LOCAL_TEST_MODE=true` 或 `EMBEDDING_PROVIDER=local_hash` 时抛出不包含凭据的 `ConfigurationError`。

- [ ] **步骤 2：运行测试验证红灯**

运行：`E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_config.py -q`

预期：共享配置模块不存在。

- [ ] **步骤 3：实现显式环境加载与连接参数**

`config.py` 只接收映射并返回不可变配置；数据库参数使用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`，不把密码拼入 URL。兼容期仅在缺少独立参数时解析 `MYSQL_DSN`。

API `connect()` 和 Worker `connect()` 都调用同一个 `mysql_connection_params`；异常文本只能列出缺失变量名，不能打印值。

- [ ] **步骤 4：更新 Compose 与安全默认值**

Compose 向 API、Runner 和 Worker 传递五个独立 `MYSQL_*` 环境变量；`.env.example` 设置：

```dotenv
APP_ENV=production
EMBEDDING_PROVIDER=openai_compatible
LOCAL_TEST_MODE=false
```

外部 embedding 未配置时应用 readiness 失败，不得静默回退到 local hash。

- [ ] **步骤 5：验证并提交**

运行：

```powershell
E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config --quiet
```

提交：

```powershell
git add shared/python services/api/app/config.py services/api/app/database.py services/worker/app/main.py tests/test_config.py .env.example deploy/docker-compose.yml
git commit -m "refactor: centralize safe runtime configuration"
```

### 任务 4：真实 readiness 与应用健康检查

**文件：**
- 创建：`services/api/app/readiness.py`
- 创建：`tests/test_readiness.py`
- 修改：`services/api/app/main.py`
- 修改：`services/api/Dockerfile`
- 修改：`services/worker/Dockerfile`
- 修改：`services/frontend/Dockerfile`
- 修改：`deploy/docker-compose.yml`
- 修改：`deploy/docker-compose.models.yml`
- 修改：`services/frontend/nginx.conf`
- 修改：`deploy/README.md`

- [ ] **步骤 1：编写 readiness 失败测试**

使用真实 `ReadinessResult` 和可注入检查函数，覆盖：全部依赖成功返回 `(200, "ready")`；任一必需依赖失败返回 `(503, "degraded")` 且保留各依赖布尔结果；异常不得泄露连接字符串或密钥。

- [ ] **步骤 2：运行测试验证红灯**

运行：`E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_readiness.py -q`

预期：现有 `/readyz` 无法返回 503，测试失败。

- [ ] **步骤 3：实现检查器与路由状态码**

`readiness.py` 分别检查 MySQL、MinIO、Milvus、OpenSearch、embedding 和 rerank；若 rerank 未配置则结果为 `not_configured` 且不阻塞，已配置但不可用则阻塞。路由使用：

```python
result = check_readiness()
return JSONResponse(status_code=200 if result.ready else 503, content=result.model_dump())
```

`/healthz` 仍只表示进程存活，不访问外部依赖。

- [ ] **步骤 4：增加 Compose 健康检查与 Nginx 策略**

API 健康检查调用容器内 `http://localhost:8000/readyz`；Frontend 调用 `http://localhost/healthz`；Infinity 调用 `/models`；Worker 与 chat-runner 使用进程级健康脚本而非只检查 PID。Frontend `depends_on.api.condition` 改为 `service_healthy`。

Nginx 为动态 HTML 设置 `Cache-Control: no-store`，哈希静态资源设置一年 immutable 缓存，并添加 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` 和 gzip。

- [ ] **步骤 5：验证并提交**

运行：

```powershell
E:\zhishiku\.venv\Scripts\python.exe -m pytest tests/test_readiness.py -q
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config --quiet
```

提交：

```powershell
git add services/api/app/readiness.py services/api/app/main.py tests/test_readiness.py services/api/Dockerfile services/worker/Dockerfile services/frontend/Dockerfile deploy/docker-compose.yml deploy/docker-compose.models.yml services/frontend/nginx.conf deploy/README.md
git commit -m "fix: enforce dependency-aware readiness"
```

### 任务 5：Docker 构建、镜像复用、CI 与第一批清理

**文件：**
- 创建：`.dockerignore`
- 创建：`services/api/.dockerignore`
- 创建：`services/worker/.dockerignore`
- 创建：`services/frontend/.dockerignore`
- 创建：`.github/workflows/quality.yml`
- 修改：`deploy/docker-compose.yml`
- 修改：`services/worker/requirements.txt`
- 修改：`README.md`
- 修改：`deploy/README.md`
- 删除：`deploy/upgrade-v05.sh`
- 删除：`deploy/smoke-test.sh`
- 删除：`deploy/e2e-admin.py`

- [ ] **步骤 1：建立可执行的构建上下文和 Compose 检查**

在 CI 中运行 `docker compose ... config --quiet` 并使用 `docker build --check` 检查三个 Dockerfile。每个 `.dockerignore` 至少排除 `.git`、`.env`、`node_modules`、`dist`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、测试缓存和本地数据目录。

- [ ] **步骤 2：复用 API 镜像**

Compose 中 API 指定稳定本地镜像名：

```yaml
api:
  image: enterprise-kb-api:${APP_IMAGE_TAG:-local}
  build:
    context: ../services/api

chat-runner:
  image: enterprise-kb-api:${APP_IMAGE_TAG:-local}
```

`chat-runner` 不再重复声明 `build`，仍保留独立命令和健康检查。

- [ ] **步骤 3：删除已确认无效的依赖和旧脚本**

先用 `rg` 确认 Worker 运行代码没有导入 `cryptography`，再从 `services/worker/requirements.txt` 移除；删除只服务旧版本升级或已被 `verify-platform-v11.py` 覆盖的三个脚本。README 只保留当前迁移与验收入口。

- [ ] **步骤 4：配置 CI 门禁**

GitHub Actions 使用 Python 3.12 和 Node 22，依次执行：

```yaml
- run: python -m pytest -q
- run: npm ci
  working-directory: services/frontend
- run: npm run lint
  working-directory: services/frontend
- run: npm run typecheck
  working-directory: services/frontend
- run: npm test
  working-directory: services/frontend
- run: npm run build
  working-directory: services/frontend
- run: docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config --quiet
```

- [ ] **步骤 5：运行阶段一全量验证**

运行：

```powershell
E:\zhishiku\.venv\Scripts\python.exe -m pytest -q
cd services/frontend
npm run lint
npm run typecheck
npm test
npm run build
cd ../..
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config --quiet
git diff --check
```

预期：所有命令退出码为 0；pytest 保持或超过 42 个通过，前端至少包含 Users 与 Dashboard 组件回归测试。

- [ ] **步骤 6：提交**

```powershell
git add .dockerignore services/api/.dockerignore services/worker/.dockerignore services/frontend/.dockerignore .github/workflows/quality.yml deploy/docker-compose.yml services/worker/requirements.txt README.md deploy/README.md deploy/upgrade-v05.sh deploy/smoke-test.sh deploy/e2e-admin.py
git commit -m "chore: add repeatable quality and container gates"
```

## 阶段一验收

- [ ] 运行完整 pytest 并记录通过数与告警。
- [ ] 运行前端 lint、typecheck、组件测试和生产构建。
- [ ] 解析基础与模型 Compose 配置。
- [ ] 对照规格核验：无数据库 DDL、无现有 API 破坏、无凭据输出。
- [ ] 生成整个阶段 diff 的独立代码审查，并修复 Critical/Important 发现。
- [ ] 更新路线图账本并进入阶段二计划编写，不直接部署服务器。


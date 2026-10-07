# 企业智能体平台与部门知识库

当前 API 版本为 `1.2.0`，提供企业总助手、专业问答智能体、知识库、MCP 系统连接、声明式 Skill、评测及运行审计。

面向企业内网的单机部署知识库与企业智能体平台。当前已具备部门隔离、资料管理、文件夹、异步入库、混合检索、多轮 AI 问答、MCP 企业系统工具、账号管理、运行追踪及审计能力。

平台的权限边界、智能体运行形态、大模型网关与系统连接器路线见 [企业智能体平台全局设计](docs/enterprise-agent-platform-design.md)。知识库内部设计保持独立演进。

后端当前采用模块化单体：各业务域在一个 FastAPI 进程内独立组织 Router、Service、Repository 与 Schema，对话、检索、评测和 MCP 能力放在 Runtime 层，数据库、对象存储和出站网络放在 Core/Infrastructure 边界。详细依赖方向、事务约束和扩展方式见 [后端架构说明](docs/architecture.md)。

办公网入口：<http://192.168.1.33:18080>

> 本仓库不保存 .env、密码、API Key、模型缓存、数据库数据或用户上传文件；它们仅保存在服务器受控目录。

## 架构

~~~text
浏览器（办公网）
  → 前端 Nginx
  → FastAPI API
       ├─ MySQL：部门、账号、权限、知识库、文件夹、文档、任务、审计
       ├─ MinIO：原始文件对象存储
       ├─ Milvus：语义向量检索
       ├─ OpenSearch：关键词/全文检索
       ├─ Infinity：本地 embedding、rerank
       ├─ DeepSeek API：最终回答与工具选择
       └─ MCP：按账号直授或按专业智能体绑定的 ERP U9、OA、纵横标书只读工具

Worker：从 MySQL ingestion_job 领取任务，执行解析/OCR、切片、向量化和全文索引。chat-runner 从 MySQL 领取总助手对话、专业智能体对话和评测任务。PLM、MOM 尚未接入。
~~~

| 组件 | 版本/用途 |
| --- | --- |
| MinIO | 原始文件，MySQL 仅保存对象路径与版本信息 |
| MySQL 8.4 | 元数据、权限、事务、审计 |
| Milvus 2.6 | 稠密向量及 document_id 等过滤字段 |
| OpenSearch 3 | 关键词召回、全文索引 |
| Infinity CPU | BAAI/bge-m3、BAAI/bge-reranker-v2-m3 |
| DeepSeek | 当前阶段的外部 LLM 生成服务 |

## 企业总助手（API 1.2.0）

首页总助手是使用账号永久权限的统一问答入口，不会调用或借用专业智能体。每次请求都按下列顺序执行，并把能力快照、意图、执行摘要和审计事件持久化：

~~~text
用户问题
→ 重新加载当前账号、部门和直授权限
→ 构造授权能力目录（部门继承/账号直授知识库、账号直授只读 MCP、可用 Skill）
→ 结构化意图识别
→ 后端校验候选 ID、参数、当前授权与预算
→ 必要时澄清；否则执行获授权的知识检索、只读工具或 Skill
→ 汇总回答、引用、来源与执行摘要
→ 再次校验权限并原子保存消息、任务结果、运行记录和审计日志
~~~

固定意图类型如下：

| 意图 | 行为 |
| --- | --- |
| `general_chat` | 不使用企业数据的普通对话 |
| `knowledge_query` | 在账号永久获授权的知识库中检索并返回引用 |
| `system_query` | 调用账号直授且声明为只读的 MCP 工具 |
| `multi_capability` | 在同一预算内组合知识库、只读工具和 Skill |
| `clarification` | 缺少必要参数、置信度不足或请求专业智能体代办时，先澄清或引导到对应入口 |
| `forbidden` | 越权或系统写操作，拒绝自动执行并记录审计 |

总助手只自动执行 `readOnlyHint=true` 且当前账号获直接授权的系统工具，不能写入企业系统，也不能通过 Skill 绕过知识库或工具权限。专业智能体须从“智能体”页面单独启动。

Skill 是管理员维护、带版本的声明式业务能力包，包含说明、触发示例、系统指令、输入 JSON Schema 以及知识库和只读工具依赖。总助手只展示依赖均在用户永久权限内、且不绑定专业智能体的 Skill。Skill 不能执行 Shell、原始 SQL、任意 URL 或未授权代码。

当前平台发布问答型专业智能体；企业总助手与专业智能体的授权和会话相互独立。

## 知识检索与专业问答链路

企业总助手先执行上节的意图识别，只在选中 `knowledge_query` 或包含知识库的 `multi_capability` 时使用账号永久授权知识库检索；纯系统工具查询不会经过 Milvus 或 OpenSearch。专业问答智能体从“智能体”入口启动，使用管理员为该智能体绑定的知识库和只读工具，不执行总助手的顶层意图分类，也不会借用用户在总助手中的账号直授工具。两种入口共用下面的知识检索能力，但授权来源和回答编排不同：

~~~text
当前入口的授权知识库范围 + 用户问题（可结合本会话历史改写检索问题）
→ 在召回前限定知识库及可访问文档
→ BGE-M3 生成检索问题向量
→ Milvus 语义召回 + OpenSearch 关键词召回
→ RRF 融合
→ BGE Reranker 重排序
→ 上下文去重与长度预算
→ 当前入口按检索切片、对话历史及允许的工具生成回答与引用
→ 再校验授权，保存回答、引用、工具事件与运行记录
~~~

- Milvus 使用 COSINE 度量。
- BAAI/bge-m3 输出 1024 维向量。
- 总助手按部门继承和账号直授权限过滤文档；专业智能体按自身绑定的知识库过滤，两者均在生成回答前限制检索范围并在发布结果前复核权限。
- 未配置模型 rerank 时系统会降级为本地词项重排序；当前已使用模型 rerank。
- 每个智能体的检索参数统一保存于 `agent.settings_json.retrieval`，包括 `candidate_k`、`top_k`、`score_threshold`、`context_max_chars` 和 `history_messages`。
- MCP 工具必须在服务端明确声明 `readOnlyHint=true`。企业总助手只使用账号直授工具；专业智能体只使用自身绑定工具，两者都在执行时重新校验当前授权。

## MCP 企业系统连接

平台支持 MCP Streamable HTTP + Bearer Token。Token 使用与大模型网关相同的 `MODEL_CREDENTIAL_KEY` 做 Fernet 加密，数据库和 API 均不返回明文。连接流程为：

所有 MCP 和模型网关出站访问默认拒绝。部署时必须配置精确主机允许列表；私网 MCP 和模型服务还必须同时配置各自允许网段。例如当前 ERP/OA 可配置 `MCP_ALLOWED_HOSTS=192.168.1.33` 与 `MCP_ALLOWED_CIDRS=192.168.1.0/24`；同时使用 DashScope 和本地 Infinity 时，示例为 `MODEL_ALLOWED_HOSTS=dashscope.aliyuncs.com,infinity` 与 `MODEL_ALLOWED_CIDRS=172.16.0.0/12`（生产按下文收窄到实际 Docker 子网）。平台会在实际建立 TCP 连接时重新解析并校验全部 DNS 地址，只连接本次校验通过的具体 IP，同时保留原主机名用于 HTTP Host 与 TLS SNI/证书验证；连接不跨解析结果复用，并拒绝 loopback、link-local、multicast、unspecified、reserved、云 metadata 地址及重定向。应用层策略仍应配合容器/宿主机防火墙或云 NSG 的出站 ACL，形成纵深防御。

安全传输层显式锁定 `httpcore==1.0.9`，因为 DNS pinning 使用其 `NetworkBackend`/`ConnectionPool` 接口。升级 HTTPX/httpcore 前必须先运行出站策略、IPv4/IPv6、Host/SNI 和总时限兼容测试。

~~~text
配置连接地址和 Token
→ MCP initialize
→ tools/list 发现工具与 JSON Schema
→ 管理员将只读工具直授给账号，或绑定到指定专业智能体
→ 模型按问题选择工具
→ 后端再次校验绑定关系并调用 MCP
→ 只保存工具名、成功状态、耗时和追踪 ID，不保存业务结果
~~~

当前部署已配置 ERP U9、OA 和纵横标书三个 MCP 连接器，并发现各自的只读工具；“已连接/已发现”不等于已向每个账号授权，也不代表每项业务查询都已完成端到端验收。系统连接页面可以查看连接状态和重新发现工具；账号直授权限在“用户与部门”维护，专业智能体绑定在“智能体工作室”维护。敏感写操作默认不接入；未来接入写工具时必须增加参数校验、人工确认、幂等键和审批审计。

## 权限与资料模型

### 账号、部门与能力权限

平台采用“部门默认权限 + 账号直授权限 + 智能体会话临时权限”三层模型，不以业务角色作为运行时授权来源。

| 来源 | 生效范围 |
| --- | --- |
| 平台管理员部门（`PLATFORM_ADMIN`） | 全局管理账号、知识库、连接器、智能体和审计 |
| 部门知识库 ACL | 用户自动继承所属部门的知识库 `read/manage` 权限；`manage` 可管理本部门资料 |
| 账号知识库直授（`user_knowledge_base_acl`） | 补充跨部门知识库的 `read/manage` 权限，与部门继承权限取较高者 |
| 账号工具直授（`user_connector_tool_acl`） | 仅供企业总助手调用已启用且标记 `readOnlyHint=true` 的 MCP 工具 |
| 账号智能体分发（`user_agent_acl`） | 决定用户能否看到和启动专业智能体，不再按部门分发 |
| 专业智能体绑定 | 用户启动已分发智能体后，仅在该智能体会话中临时获得其绑定知识库和只读工具；不会扩展总助手、知识库管理或其他智能体权限 |

- 平台管理员可在创建、编辑账号时同时维护所属部门、账号直授知识库和账号直授只读 MCP 工具。
- 专业智能体由管理员配置使用账号、知识库和只读工具；撤销用户分发或能力绑定后，执行中任务在工具调用和最终发布前会再次校验并拒绝越权结果。
- 平台管理员可创建、编辑、启停、重置密码和软删除账号。
- 密码只保存哈希；管理员不能读取历史明文密码。
- 创建/重置密码仅返回一次临时密码。
- 不可删除或停用当前登录管理员，且必须至少保留一名启用的平台管理员。

### 知识库与文件夹

~~~text
部门
 └─ 知识库（权限、管理与检索边界）
     └─ 文件夹树（资料整理）
         └─ 文档 → 文档版本 → 内容切片
~~~

- 知识库不是文件夹：知识库承担权限与检索边界，文件夹仅整理资料；当前问答检索不按文件夹过滤。
- 文件夹由 knowledge_folder 保存；非空文件夹不可删除。
- 移动文件夹或文档只改 MySQL 元数据，不重新 OCR、切片或向量化。
- document 是逻辑资料；document_version 记录原文件版本、对象路径、校验信息和处理状态。
- 删除文档会清理 MinIO 对象、Milvus 向量、OpenSearch 文档及关联数据，同时保留审计生命周期记录。

## 文件处理范围

| 类型 | 当前处理方式 |
| --- | --- |
| PDF | 带坐标文本/线框表格提取；保守识别跨页续段和重复表头续表；重复页眉页脚清理；逐页 OCR 回退 |
| DOCX | 保留正文、标题、表格原始顺序，标题路径和表头随切片保留 |
| XLSX / XLSM | 每个表格切片携带工作表、表头和数据行号，支持常规合并多级表头 |
| TXT / MD / CSV | 直接读取 |
| PNG/JPG/JPEG/TIF/TIFF/BMP | Tesseract 中文+英文 OCR |
| CAD/复杂工程图 | 暂不支持；后续加入预览、图框识别、OCR 和多模态检索 |

切片、向量化和全文索引均由 Worker 异步执行，前端显示处理、切片、向量和全文状态。

解析器 `builtin-structured 1.1.0` 的规则与限制见 [文档解析说明](docs/document-parsing.md)。1.1 父子切片与处理配置需要迁移 012；已有文档不会自动重建。跨页切片保留起止页码，问答引用显示页码范围。

## 目录

~~~text
deploy/
  docker-compose.yml              基础服务 Compose
  docker-compose.models.yml       本地模型覆盖文件
  verify-platform.py              平台集成与权限隔离验收
  apply-mysql-migration.sh        单个迁移执行器
  queue-reindex.py                既有文档重建索引任务
database/mysql/                   001~016 MySQL 初始化与增量迁移
services/api/                     FastAPI 管理、检索、问答、审计
  app/application.py              应用工厂、生命周期、异常处理和路由装配
  app/core/                       配置、事务、安全、审计和出站策略
  app/domains/                    按业务域拆分的 Router/Service/Repository/Schema
  app/infrastructure/             对象存储等基础设施适配器
  app/runtime/                    对话、检索、评测和 MCP 运行时
services/worker/                  解析、OCR、切片、Embedding、索引
services/frontend/                管理与问答前端
~~~

## 本地开发与质量门禁

后端测试使用仓库根目录依赖，前端命令在 `services/frontend` 执行：

~~~bash
python -m pip install -r requirements-test.txt
python -m pip install ./shared/python
python -m pytest -q
npm --prefix services/frontend ci
npm --prefix services/frontend run lint
npm --prefix services/frontend run typecheck
npm --prefix services/frontend run test -- --run
npm --prefix services/frontend run build
docker compose --env-file .env.example -f deploy/docker-compose.yml config
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config
python tools/check_repository_hygiene.py
~~~

Docker Compose 的 `config` 只验证配置展开；只有守护进程可用、使用隔离测试数据目录完成 build/up、健康检查和故障注入后，才算完成容器运行验收。

## 服务器、数据与网络

当前服务器项目目录：

~~~text
/home/ai/zhishiku
~~~

DATA_ROOT 当前通常为：

~~~text
/home/ai/zhishiku/data
~~~

~~~text
data/minio/                 MinIO 对象数据
data/mysql/                 MySQL 数据
data/milvus/                Milvus 持久化数据
data/opensearch/            OpenSearch 索引
data/models/infinity/       Infinity 缓存
data/models/source/         本地 BGE 模型权重
~~~

不要删除 data/，除非已验证备份并明确需要重置环境。

- 前端：FRONTEND_BIND_IP=192.168.1.33，FRONTEND_PORT=18080。
- API：HOST_BIND_IP=127.0.0.1，不直接对办公网开放。
- MinIO、MySQL、Milvus、OpenSearch、Infinity 仅在 Docker 内部网络 enterprise-kb-internal 通信。
- 正式推广前应补充 HTTPS、AUTH_COOKIE_SECURE=true、SSO、备份、监控和告警。

## 初次部署

以下复制步骤仅用于新部署，已有 `.env` 不会自动继承 `.env.example`，也不要用示例覆盖现有凭据。启用本地模型前，核对下节的 Infinity 精确主机与 Docker 网段允许列表；自定义 Docker 地址池时必须按实际子网调整。

~~~bash
cd /home/ai/zhishiku
cp .env.example .env
chmod 600 .env
nano .env
mkdir -p data
sysctl -w vm.max_map_count=262144
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  up -d --build
~~~

vm.max_map_count=262144 是 OpenSearch 必需内核参数，应在生产系统配置中持久化。

## 本地 embedding 与 rerank

当前本地模型：

| 能力 | 模型 | 接口 |
| --- | --- | --- |
| 文档/问题向量化 | BAAI/bge-m3，1024 维 | http://infinity:7997/embeddings |
| 候选切片重排 | BAAI/bge-reranker-v2-m3 | http://infinity:7997/rerank |
| 最终回答 | DeepSeek API | LLM_BASE_URL |

ModelScope 用于下载模型文件；Infinity 从本地加载权重并提供 HTTP 推理服务。

模型服务配置以 [本地模型 Compose 文件](deploy/docker-compose.models.yml) 为准，避免在 README 里维护第二份 YAML。模型权重目录只读挂载，当前 Infinity 限额为 16 核、24 GB 内存；这是上限，不代表启动即占满。Infinity 不开放宿主机端口，仅供内部 Docker 网络调用。

本地模型环境配置示例（部署时以服务器实际 `.env` 为准）：

~~~dotenv
MODEL_ALLOWED_HOSTS=dashscope.aliyuncs.com,infinity
MODEL_ALLOWED_CIDRS=172.16.0.0/12
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://infinity:7997
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
MILVUS_COLLECTION=kb_content_units_bge_m3_v1
RERANK_BASE_URL=http://infinity:7997
RERANK_API_KEY=
RERANK_MODEL=BAAI/bge-reranker-v2-m3
~~~

模型请求（包括企业总助手的绝对 deadline 检索路径）同样执行 fail-closed 出站策略。已有 `.env` 不会自动继承 `.env.example`；升级时务必在重启前把 `infinity` 加入现有 `MODEL_ALLOWED_HOSTS`，并配置 `MODEL_ALLOWED_CIDRS`，保留仍在使用的其他精确模型主机。仅配置模型 URL 不足以授权私网访问。

示例的 `172.16.0.0/12` 覆盖常见 Docker 动态 bridge 的 `172.17.*` 至 `172.31.*` 地址分配；它仅与精确主机允许列表同时生效，不会放行其他私网主机。生产应查询 `enterprise-kb-internal` 实际子网，将 `MODEL_ALLOWED_CIDRS` 收窄为该子网（例如 `172.18.0.0/16`）。若服务器的自定义地址池、地址池耗尽后的分配或 IPv6 子网不在示例范围内，必须显式调整，不能假定示例覆盖所有服务器。禁止配置全网通配 CIDR 或放行 metadata、loopback；这些地址类别仍由策略强制拒绝。

以下命令只输出网络子网，不打印 `.env`、容器环境或任何凭据；更改 `COMPOSE_PROJECT_NAME` 时替换网络名。首次部署网络尚未创建时可先使用经核对的地址池范围，创建后收窄，并在重新创建 API 前更新 `.env`。

~~~bash
docker network inspect enterprise-kb-internal --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'
~~~

## 日常运维

Compose 会在 MinIO healthy 后运行一次性 `minio-init`，按 `MINIO_BUCKET` 幂等创建桶；API 等待初始化成功退出，readiness 保持只读。加载本地模型覆盖文件时，API 还会等待 Infinity healthy，再由 Frontend 等待 API healthy 后启动。

本地模型在独立 Compose 文件中定义。因此 .env 配置为 Infinity 后，**整套服务启动、停止、更新都必须带上两个 Compose 文件**：

~~~bash
cd /home/ai/zhishiku
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  up -d
~~~

只使用 docker-compose.yml 会遗漏 Infinity，导致上传无法向量化，问答也无法生成查询向量或 rerank。

~~~bash
# 查看状态、健康检查和日志
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml ps
read_dotenv_value() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'
}
api_health_host="${HOST_BIND_IP:-$(read_dotenv_value HOST_BIND_IP)}"
api_health_port="${API_PORT:-$(read_dotenv_value API_PORT)}"
api_health_host="${api_health_host:-127.0.0.1}"
api_health_port="${api_health_port:-8000}"
curl -fsS "http://${api_health_host}:${api_health_port}/healthz"
curl -fsS "http://${api_health_host}:${api_health_port}/readyz"
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  logs --tail=100 api worker infinity

# 修改模型相关 .env 后，仅重建 API 与 Worker
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  up -d --force-recreate api worker

# 停止容器但保留数据、模型
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml down

# 更新代码
git pull --ff-only
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml up -d --build
~~~

验证模型服务：

~~~bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T api python - <<'PY'
import httpx
print(httpx.get("http://infinity:7997/models", timeout=30).json())
PY
~~~

结果应有 BAAI/bge-m3（embed）和 BAAI/bge-reranker-v2-m3（rerank）。

不要执行 docker compose down -v，也不要对生产数据目录执行 rm -rf，除非已完成备份并明确需要清库。

## 数据库初始化与升级

全新 MySQL 数据目录由镜像按文件名顺序执行 001–016 初始化脚本；已有数据目录不会重新执行初始化 SQL。升级已有库前必须备份并在隔离环境恢复验证，核对已应用迁移和当前表结构后按 [部署说明](deploy/README.md#遗留结构退役) 与 [MySQL 迁移说明](database/mysql/README.md) 执行，不能直接重放初始化脚本。

当前生产环境已完成 015–016，不应重新执行。备份和核对记录留在服务器受控目录，不提交仓库。

## 验收和排查

迁移步骤见上一节和 [MySQL 迁移说明](database/mysql/README.md)；已执行的迁移不能重写或重复执行。

完成迁移后，在已确认的验收环境运行当前平台集成验收（会创建并清理临时业务数据）：

~~~bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T api python - < deploy/verify-platform.py
~~~

它验证部门与文档权限、上传和父子切片、双路索引、混合检索与 rerank、配置版本、评测、持久化对话与幂等、模型回答与引用、反馈所有权及任务取消。脚本清理本次创建的资料与智能体，停用临时账号和部门、归档临时知识库并保留审计记录。MCP 连接发现与工具授权另在系统连接页面验证；不要对真实业务数据调用有副作用的工具。

本地质量门禁与容器构建检查见 [部署说明](deploy/README.md#本地质量门禁与镜像构建)。API 与 chat-runner 复用 `enterprise-kb-api:${APP_IMAGE_TAG:-local}` 镜像，更新时一起重建、重建容器以保持版本一致。

| 现象 | 优先检查 |
| --- | --- |
| 页面不可访问 | docker compose ps；前端是否监听 192.168.1.33:18080 |
| 上传持续处理中 | logs worker；检查 MySQL ingestion_job、MinIO、文件类型、OCR |
| 问答无结果 | logs api；检查 embedding/rerank 地址与 Milvus 集合 |
| 本地模型不可达 | logs infinity；请求 http://infinity:7997/models |
| OpenSearch 起不来 | 检查 vm.max_map_count 是否至少 262144 |
| 中文乱码 | 检查浏览器缓存、MySQL utf8mb4、迁移状态 |

## 备份、扩展与模型切换

至少备份 MySQL 逻辑数据、MinIO 对象、Milvus 数据、OpenSearch 快照、加密保存的 .env、已验收模型权重及版本/校验记录；必须定期做恢复演练。

当前是单机 MVP。全公司推广前应规划独立数据库/索引/模型节点、HTTPS/SSO、数据分级、监控告警、对象存储版本化、异地备份、图纸处理流水线和 GPU 推理节点。

不同 embedding 模型的向量不能混写到同一 Milvus 集合，即使维度相同。模型替换遵循：

~~~text
新集合 → 全量重向量化 → 抽样验收 → 切换查询 → 保留旧集合回滚 → 最终清理
~~~

当前检索集合为 `kb_content_units_bge_m3_v1`。日后更换 embedding 模型时，应按上面的步骤新建集合并重建已有资料索引，不能仅因新旧模型都是 1024 维就复用向量。

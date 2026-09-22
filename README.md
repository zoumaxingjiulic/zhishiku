# 企业智能体平台与部门知识库

当前 API 版本为 `1.2.0`。1.2 在 1.1 的智能体、评测和持久化任务能力上增加企业总助手、显式意图识别、声明式 Skill 与统一工作台；1.1 的使用方法和历史验证范围仍见 [平台 1.1 说明](docs/platform-v11.md) 与 [部署验收记录](docs/verification-v11.md)。

面向企业内网的单机部署知识库与企业智能体平台。当前已具备部门隔离、资料管理、文件夹、异步入库、混合检索、多轮 AI 问答、MCP 企业系统工具、账号管理、运行追踪及审计能力。

平台正在从知识问答 MVP 演进为统一企业智能体平台；全局模块、权限边界、智能体运行形态、大模型网关与系统连接器路线见 [企业智能体平台全局设计](docs/enterprise-agent-platform-design.md)。知识库内部设计仍保持独立演进。

后端当前采用模块化单体：各业务域在一个 FastAPI 进程内独立组织 Router、Service、Repository 与 Schema，聊天、工作流和 MCP 等长流程放在 Runtime 层，数据库、对象存储和出站网络放在 Core/Infrastructure 边界。详细依赖方向、事务约束和扩展方式见 [后端架构说明](docs/architecture.md)。

办公网入口：<http://192.168.1.33:18080>

> 本仓库不保存 .env、密码、API Key、模型缓存、数据库数据或用户上传文件；它们仅保存在服务器受控目录。

## 架构

~~~text
浏览器（办公网）
  → 前端 Nginx
  → FastAPI API
       ├─ MySQL：部门、账号、权限、知识库、文件夹、文档、任务、审计
       ├─ Redis：预留缓存基础服务（当前任务队列使用 MySQL）
       ├─ MinIO：原始文件对象存储
       ├─ Milvus：语义向量检索
       ├─ OpenSearch：关键词/全文检索
       ├─ Infinity：本地 embedding、rerank
       ├─ DeepSeek API：最终回答与工具选择
       └─ MCP：按智能体授权的 ERP、OA、PLM、MOM 只读工具

Worker：从 MySQL ingestion_job 领取任务，执行解析/OCR、切片、向量化和全文索引。chat-runner 从 MySQL 领取总助手对话、专业智能体对话、兼容工作流和评测任务。
~~~

| 组件 | 版本/用途 |
| --- | --- |
| MinIO | 原始文件，MySQL 仅保存对象路径与版本信息 |
| MySQL 8.4 | 元数据、权限、事务、审计 |
| Milvus 2.6 | 稠密向量及 document_id 等过滤字段 |
| OpenSearch 3 | 关键词召回、全文索引 |
| Redis 7 | 预留缓存基础设施；当前任务持久化在 MySQL |
| Infinity CPU | BAAI/bge-m3、BAAI/bge-reranker-v2-m3 |
| DeepSeek | 当前阶段的外部 LLM 生成服务 |

## 企业总助手（API 1.2.0）

首页总助手是统一调度入口，不替代专业智能体。每次请求都按下列顺序执行，并把能力快照、意图、执行摘要和审计事件持久化：

~~~text
用户问题
→ 重新加载当前用户与部门权限
→ 构造授权能力目录（知识库、只读 MCP、Skill、专业智能体）
→ 结构化意图识别
→ 后端校验候选 ID、参数、当前授权与预算
→ 必要时澄清；否则执行获授权能力
→ 汇总回答、引用、来源与执行摘要
→ 原子保存消息、任务结果、运行记录和审计日志
~~~

固定意图类型如下：

| 意图 | 行为 |
| --- | --- |
| `general_chat` | 不使用企业数据的普通对话 |
| `knowledge_query` | 在获授权知识库中检索并返回引用 |
| `system_query` | 调用获授权且声明为只读的 MCP 工具 |
| `agent_task` | 委托一个已发布的专业智能体 |
| `multi_capability` | 在同一预算内组合两种以上能力 |
| `clarification` | 缺少必要参数或置信度不足，先向用户澄清 |
| `forbidden` | 越权或系统写操作，拒绝自动执行并记录审计 |

第一版只自动执行 `readOnlyHint=true` 且当前用户获授权的系统工具。总助手不能写入 ERP、OA、MOM 或 PLM，不能通过 Skill 绕过部门、知识库、智能体或工具授权；执行前和发布回答前都会复核权限。专业智能体委托沿用用户身份、根运行 ID 与剩余预算，保留来源链路。

Skill 是管理员维护、带版本的声明式业务能力包，包含说明、触发示例、系统指令、输入 JSON Schema 和允许组合的知识库、只读工具、专业智能体及部门。Skill 不能执行 Shell、原始 SQL、任意 URL 或未授权代码；复杂算法与长任务仍由经过测试的专业智能体承担。

低代码建设已冻结：工作室不再提供新建工作流智能体的入口，也未引入画布、节点市场或新的工作流执行引擎。已有 `workflow_run`、历史数据、读取/API 和顺序执行代码继续作为兼容层保留；迁移 013 不删除这些内容。

## 问答链路

~~~text
用户问题
→ 身份/部门/知识库/文件夹范围校验
→ 读取同一会话最近的多轮上下文
→ BGE-M3 生成问题向量
→ Milvus 语义召回 + OpenSearch 关键词召回
→ RRF 融合
→ BGE Reranker 重排序
→ 上下文去重与长度预算
→ DeepSeek 基于最终切片生成回答与引用，或选择已授权 MCP 工具
→ 保存引用、工具事件、各阶段耗时与匿名问题哈希
~~~

- Milvus 使用 COSINE 度量。
- BAAI/bge-m3 输出 1024 维向量。
- 权限和范围过滤在检索、rerank、LLM 调用之前执行；跨部门资料不得进入候选集。
- 未配置模型 rerank 时系统会降级为本地词项重排序；当前已使用模型 rerank。
- `agent_knowledge_base.retrieval_config_json` 可配置 `candidate_k`、`top_k`、`score_threshold`、`context_max_chars` 和 `history_messages`。
- MCP 工具必须在服务端明确声明 `readOnlyHint=true`，并经平台管理员绑定到具体智能体后才会暴露给模型。

## MCP 企业系统连接

平台支持 MCP Streamable HTTP + Bearer Token。Token 使用与大模型网关相同的 `MODEL_CREDENTIAL_KEY` 做 Fernet 加密，数据库和 API 均不返回明文。连接流程为：

所有 MCP 和模型网关出站访问默认拒绝。部署时必须配置精确主机允许列表；私网 MCP 和模型服务还必须同时配置各自允许网段。例如当前 ERP/OA 可配置 `MCP_ALLOWED_HOSTS=192.168.1.33` 与 `MCP_ALLOWED_CIDRS=192.168.1.0/24`；同时使用 DashScope 和本地 Infinity 时，示例为 `MODEL_ALLOWED_HOSTS=dashscope.aliyuncs.com,infinity` 与 `MODEL_ALLOWED_CIDRS=172.16.0.0/12`（生产按下文收窄到实际 Docker 子网）。平台会在实际建立 TCP 连接时重新解析并校验全部 DNS 地址，只连接本次校验通过的具体 IP，同时保留原主机名用于 HTTP Host 与 TLS SNI/证书验证；连接不跨解析结果复用，并拒绝 loopback、link-local、multicast、unspecified、reserved、云 metadata 地址及重定向。应用层策略仍应配合容器/宿主机防火墙或云 NSG 的出站 ACL，形成纵深防御。

安全传输层显式锁定 `httpcore==1.0.9`，因为 DNS pinning 使用其 `NetworkBackend`/`ConnectionPool` 接口。升级 HTTPX/httpcore 前必须先运行出站策略、IPv4/IPv6、Host/SNI 和总时限兼容测试。

~~~text
配置连接地址和 Token
→ MCP initialize
→ tools/list 发现工具与 JSON Schema
→ 管理员按智能体授予只读工具
→ 模型按问题选择工具
→ 后端再次校验绑定关系并调用 MCP
→ 只保存工具名、成功状态、耗时和追踪 ID，不保存业务结果
~~~

当前已接入 ERP U9 料品查询与 OA 通讯录查询。系统连接页面可以查看连接状态、重新发现工具并管理智能体授权。敏感写操作默认不接入；未来接入写工具时必须增加参数校验、人工确认、幂等键和审批审计。

## 权限与资料模型

### 部门权限

权限由账号所属部门计算，不以业务角色作为运行时授权来源。

| 部门 | 权限 |
| --- | --- |
| 平台管理员（PLATFORM_ADMIN） | 管理全部部门、账号、知识库、资料、智能体和审计日志 |
| 人力资源部、技术部等业务部门 | 只能访问自身部门获得 ACL 授权的知识库和智能体 |

- 平台管理员可创建、编辑、启停、重置密码和软删除账号。
- 密码只保存哈希；管理员不能读取历史明文密码。
- 创建/重置密码仅返回一次临时密码。
- 不可删除或停用当前登录管理员，且必须至少保留一名启用的平台管理员。

### 知识库与文件夹

~~~text
部门
 └─ 知识库（权限、管理与检索边界）
     └─ 文件夹树（资料整理、范围限定）
         └─ 文档 → 文档版本 → 内容切片
~~~

- 知识库不是文件夹：知识库承担权限与检索边界，文件夹仅整理资料。
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
  docker-compose.models.yml       本地模型覆盖文件（服务器创建）
  verify-platform-v11.py          平台 1.1 集成与权限隔离验收
  apply-mysql-migration.sh        单个迁移执行器
  queue-reindex.py                既有文档重建索引任务
database/mysql/                   001~013 MySQL 初始化与增量迁移
services/api/                     FastAPI 管理、检索、问答、审计
  app/application.py              应用工厂、生命周期、异常处理和路由装配
  app/core/                       配置、事务、安全、审计和出站策略
  app/domains/                    按业务域拆分的 Router/Service/Repository/Schema
  app/infrastructure/             对象存储等基础设施适配器
  app/runtime/                    聊天、工作流、评测和 MCP 运行时
services/worker/                  解析、OCR、切片、Embedding、索引
services/frontend/                管理与问答前端
~~~

## 本地开发与质量门禁

后端测试使用仓库根目录依赖，前端命令在 `services/frontend` 执行：

~~~bash
python -m pip install -r requirements-test.txt
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
data/redis/                 Redis AOF
data/milvus/                Milvus 持久化数据
data/opensearch/            OpenSearch 索引
data/models/infinity/       Infinity 缓存
data/models/source/         本地 BGE 模型权重
~~~

不要删除 data/，除非已验证备份并明确需要重置环境。

- 前端：FRONTEND_BIND_IP=192.168.1.33，FRONTEND_PORT=18080。
- API：HOST_BIND_IP=127.0.0.1，不直接对办公网开放。
- MinIO、MySQL、Redis、Milvus、OpenSearch、Infinity 仅在 Docker 内部网络 enterprise-kb-internal 通信。
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

服务器的 deploy/docker-compose.models.yml：

~~~yaml
services:
  infinity:
    image: michaelf34/infinity:latest-cpu
    restart: unless-stopped
    command: >
      v2 --engine torch
      --model-id /models/bge-m3
      --served-model-name BAAI/bge-m3
      --model-id /models/bge-reranker-v2-m3
      --served-model-name BAAI/bge-reranker-v2-m3
      --port 7997
    environment:
      HF_HOME: /app/.cache
      HF_HUB_DISABLE_TELEMETRY: "1"
      OMP_NUM_THREADS: "6"
    volumes:
      - ${DATA_ROOT}/models/infinity:/app/.cache
      - ${DATA_ROOT}/models/source:/models:ro
    cpus: 6
    mem_limit: 24g
    networks:
      - kb-internal
~~~

- 模型目录以只读方式挂载。
- cpus: 6、mem_limit: 24g 是模型服务上限，不代表启动即占满。
- Infinity 不开放宿主机端口，仅供 API/Worker 的内部 Docker 网络调用。

当前 .env 的模型部分：

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

## 1.2 增量部署、健康检查与回滚

以下命令是生产/验收环境的可复制步骤，本地测试不会自动执行。先确认当前提交、目标环境与维护窗口；不要在未授权环境运行备份、迁移、构建或重启。

### 1. 备份并应用 013

013 只新增企业总助手种子、意图决策表、Skill 表和绑定表。它不修改或重建知识库、文档、切片、MinIO 对象、Milvus 集合、OpenSearch 索引，也不删除历史工作流。既有环境只能执行一次；已执行的迁移文件不得修改。

先用同版本、同配置的完整备份在隔离环境做恢复演练，确认可以登录并核对关键表；只有恢复演练通过后才能把 `BACKUP_RESTORE_VERIFIED` 设为 `yes`。下面脚本启用 fail-fast：转储先写入权限受限的临时文件，只有命令成功、文件非空且包含 MySQL 转储头和完成标记时才原子改名；任一步失败或恢复验证闸门未打开都不会执行 013。首次不设置闸门运行会安全停在迁移前，可用打印出的最终文件做恢复演练；通过后设置闸门重新运行，脚本会再生成并保留一份迁移前有效备份。

~~~bash
cd /home/ai/zhishiku
set -euo pipefail
umask 077

backup_dir=backup
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
backup_stamp="$(date +%Y%m%d-%H%M%S)"
backup_tmp="$(mktemp "${backup_dir}/.mysql-before-013-${backup_stamp}.XXXXXX")"
backup_final="${backup_dir}/mysql-before-013-${backup_stamp}.sql"

cleanup_backup() { rm -f -- "$backup_tmp"; }
trap cleanup_backup EXIT HUP INT TERM

docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T mysql sh -ec \
  'exec mysqldump --single-transaction --routines --triggers -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  > "$backup_tmp"

test -s "$backup_tmp"
grep -aFq -- '-- MySQL dump ' "$backup_tmp"
grep -aFq -- '-- Dump completed on ' "$backup_tmp"
chmod 600 "$backup_tmp"
test ! -e "$backup_final"
mv -- "$backup_tmp" "$backup_final"
trap - EXIT HUP INT TERM
printf 'backup ready: %s\n' "$backup_final"

test -s "$backup_final"
grep -aFq -- '-- Dump completed on ' "$backup_final"
test "${BACKUP_RESTORE_VERIFIED:-no}" = yes
bash deploy/apply-mysql-migration.sh database/mysql/013_enterprise_assistant.sql
~~~

不要删除或覆盖打印出的 `backup_final`；至少保留到 013、应用回滚窗口和真实账号验收全部结束。恢复演练必须使用隔离数据库/环境，不得覆盖生产库；记录备份文件名、校验值和演练结果，但不要记录密码或业务数据。`MYSQL_ROOT_PASSWORD` 只在 MySQL 容器内由 `.env` 注入并展开，不出现在宿主机命令参数或日志中。

迁移后应检查新表和保留数据，不输出凭据或业务正文：

~~~bash
docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql sh -c \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e \
  "SHOW TABLES LIKE '\''assistant_%'\''; SELECT id,code,status FROM agent WHERE code='\''ENTERPRISE_ASSISTANT'\''; SELECT COUNT(*) AS workflow_runs FROM workflow_run;"'
~~~

### 2. 构建并使用双 Compose 文件启动

重启前先更新现有 `.env`：它不会自动继承新版 `.env.example`。本地模型必须在 `MODEL_ALLOWED_HOSTS` 中包含 `infinity`，并将 `MODEL_ALLOWED_CIDRS` 配置为网络实际子网；核对命令为 `docker network inspect enterprise-kb-internal --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'`，只输出子网、不输出秘密。示例 `172.16.0.0/12` 仅覆盖上述常见 Docker 范围；实际网络不在其中时须调整。保留现有凭据与仍在使用的精确外部模型主机，不要覆盖整个 `.env`。

本地 Infinity 只定义在 `deploy/docker-compose.models.yml`，所以构建、启动、查看状态和后续重启都必须同时带两个文件；当前仓库没有为此定义 Compose profile。

~~~bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  build api worker frontend

docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  up -d
~~~

### 3. 区分 liveness 与 readiness

~~~bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml ps

# 仅读取仓库实际使用的 HOST_BIND_IP/API_PORT；不 source 含凭据的 .env
read_dotenv_value() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'
}
api_health_host="${HOST_BIND_IP:-$(read_dotenv_value HOST_BIND_IP)}"
api_health_port="${API_PORT:-$(read_dotenv_value API_PORT)}"
api_health_host="${api_health_host:-127.0.0.1}"
api_health_port="${api_health_port:-8000}"

# API liveness：进程可响应，不代表依赖已就绪
curl -fsS "http://${api_health_host}:${api_health_port}/healthz"

# API readiness：MySQL、MinIO、Milvus、OpenSearch 与模型依赖可用
curl -fsS "http://${api_health_host}:${api_health_port}/readyz"

# 前端稳定健康端点；不依赖首页标题或其他页面文案
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T frontend wget -qO- http://127.0.0.1/healthz

# Infinity 模型目录，应包含 embedding 与 rerank 模型
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T api python -c \
  'import httpx; r=httpx.get("http://infinity:7997/models",timeout=30); r.raise_for_status(); print(r.json())'
~~~

API 地址使用仓库 Compose 已定义的 `HOST_BIND_IP` 与 `API_PORT`，变量未设置时分别回退到 `.env.example` 的 `127.0.0.1` 与 `8000`；脚本不会 `source` 整份 `.env`。`/healthz` 是 liveness；只有 `/readyz` 成功且前端、Infinity 与容器状态均正常，才进入账号业务验收。

### 4. 停止发布与回滚

任一迁移、readiness、权限隔离或真实账号验收失败时，立即停止发布，不把新版本加入办公网流量。先保存提交号、迁移时间、镜像标签、容器状态和仅含错误类型的日志；不要在日志中复制 Token、密码或业务结果。

013 是向后兼容的增量表结构，优先把应用回滚到上一个已验收提交/镜像，并保留 013 新表等待修复，旧版本会忽略它们：

~~~bash
git checkout <previous-tested-commit>
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  up -d --build
~~~

不要临时 `DROP` 013 表。只有确认迁移造成必须恢复的数据问题、停止写入并经过变更审批后，才在维护窗口从 `backup/mysql-before-013-*.sql` 恢复整个 MySQL 备份；恢复会覆盖备份时间之后的数据库写入，必须先评估和保全增量数据。不得执行 `docker compose down -v`，也不得删除 `data/`。

### 5. 真实账号验收清单

使用管理员和两个不同部门普通账号验证：能力目录差异、总助手会话/任务隔离、知识问答与引用、ERP/OA/纵横标书只读调用、专业智能体委托、页面切换后任务恢复、越权与写操作拒绝、运行追踪和审计记录。记录 Git 提交、迁移时间、镜像、命令结果和未通过项；“页面可打开”不等于业务验收通过。

## 数据库迁移、验收和排查

全新 MySQL 数据目录会由官方 MySQL 镜像按文件名顺序自动执行挂载目录中的全部 SQL，即当前的 `001_initial_schema.sql` 至 `013_enterprise_assistant.sql`；数据目录初始化后不会再次自动执行。既有环境的后续迁移每个文件只能执行一次，例如 1.2：

~~~bash
bash deploy/apply-mysql-migration.sh database/mysql/013_enterprise_assistant.sql
~~~

历史迁移见 [database/mysql/README.md](database/mysql/README.md)。已执行过的迁移绝不能修改或重写。

完成迁移后，在已确认的验收环境运行当前平台集成验收（会创建并清理临时业务数据）：

~~~bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.models.yml \
  exec -T api python - < deploy/verify-platform-v11.py
~~~

它验证部门与文档权限、上传和父子切片、双路索引、混合检索与 rerank、配置版本、评测、持久化对话与幂等、模型回答与引用、反馈所有权、任务取消和工作流审批。脚本清理本次创建的资料与智能体，停用临时账号和部门、归档临时知识库并保留审计记录；覆盖边界见 [平台 1.1 验收记录](docs/verification-v11.md)。MCP 连接发现与工具授权另在系统连接页面验证，验收时不要调用会产生业务副作用的工具。

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

当前没有正式资料，因此已从 kb_content_units_qwen37_v1 直接切换到 kb_content_units_bge_m3_v1，无需历史重向量化。

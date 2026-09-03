# 企业智能体平台与部门知识库

面向企业内网的单机部署知识库与智能问答 MVP。当前已具备部门隔离、资料管理、文件夹、异步入库、混合检索、AI 问答、账号管理及审计能力。首个业务智能体为人资制度问答助手。

平台正在从知识问答 MVP 演进为统一企业智能体平台；全局模块、权限边界、智能体运行形态、大模型网关与系统连接器路线见 [企业智能体平台全局设计](docs/enterprise-agent-platform-design.md)。知识库内部设计仍保持独立演进。

办公网入口：<http://192.168.1.33:18080>

> 本仓库不保存 .env、密码、API Key、模型缓存、数据库数据或用户上传文件；它们仅保存在服务器受控目录。

## 架构

~~~text
浏览器（办公网）
  → 前端 Nginx
  → FastAPI API
       ├─ MySQL：部门、账号、权限、知识库、文件夹、文档、任务、审计
       ├─ Redis：异步任务队列
       ├─ MinIO：原始文件对象存储
       ├─ Milvus：语义向量检索
       ├─ OpenSearch：关键词/全文检索
       ├─ Infinity：本地 embedding、rerank
       └─ DeepSeek API：最终回答生成

Worker：从 Redis 取任务，执行解析/OCR、切片、向量化和全文索引。
~~~

| 组件 | 版本/用途 |
| --- | --- |
| MinIO | 原始文件，MySQL 仅保存对象路径与版本信息 |
| MySQL 8.4 | 元数据、权限、事务、审计 |
| Milvus 2.6 | 稠密向量及 document_id 等过滤字段 |
| OpenSearch 3 | 关键词召回、全文索引 |
| Redis 7 | 异步任务与重试 |
| Infinity CPU | BAAI/bge-m3、BAAI/bge-reranker-v2-m3 |
| DeepSeek | 当前阶段的外部 LLM 生成服务 |

## 问答链路

~~~text
用户问题
→ 身份/部门/知识库/文件夹范围校验
→ BGE-M3 生成问题向量
→ Milvus 语义召回 + OpenSearch 关键词召回
→ RRF 融合
→ BGE Reranker 重排序
→ DeepSeek 基于最终切片生成回答与引用
~~~

- Milvus 使用 COSINE 度量。
- BAAI/bge-m3 输出 1024 维向量。
- 权限和范围过滤在检索、rerank、LLM 调用之前执行；跨部门资料不得进入候选集。
- 未配置模型 rerank 时系统会降级为本地词项重排序；当前已使用模型 rerank。

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

解析器 `builtin-structured 0.8.0` 的规则、限制、回归测试和部署方法见 [文档解析说明](docs/document-parsing.md)。无需数据库迁移；已有文档不会自动重建。跨页切片保留起止页码，问答引用显示完整页码范围。

## 目录

~~~text
deploy/
  docker-compose.yml              基础服务 Compose
  docker-compose.models.yml       本地模型覆盖文件（服务器创建）
  smoke-test.sh                   全流程/权限隔离验收
  upgrade-v05.sh                  既有环境升级
  apply-mysql-migration.sh        单个迁移执行器
  queue-reindex.py                既有文档重建索引任务
database/mysql/                   001~009 MySQL 初始化与增量迁移
services/api/                     FastAPI 管理、检索、问答、审计
services/worker/                  解析、OCR、切片、Embedding、索引
services/frontend/                管理与问答前端
~~~

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

~~~bash
cd /home/ai/zhishiku
cp .env.example .env
chmod 600 .env
nano .env
mkdir -p data
sysctl -w vm.max_map_count=262144
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
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
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://infinity:7997
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
MILVUS_COLLECTION=kb_content_units_bge_m3_v1
RERANK_BASE_URL=http://infinity:7997
RERANK_API_KEY=
RERANK_MODEL=BAAI/bge-reranker-v2-m3
~~~

## 日常运维

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
curl -s http://127.0.0.1:18000/healthz
curl -s http://127.0.0.1:18000/readyz
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

## 数据库迁移、验收和排查

全新 MySQL 数据目录自动执行 001_initial_schema.sql。既有环境的后续迁移每个文件只能执行一次：

~~~bash
bash deploy/apply-mysql-migration.sh database/mysql/009_knowledge_folders.sql
~~~

历史迁移见 [database/mysql/README.md](database/mysql/README.md)。已执行过的迁移绝不能修改或重写。

完整验收：

~~~bash
bash deploy/smoke-test.sh
~~~

它验证账号、软删除、部门隔离、跨部门 403、上传、解析、切片、Milvus、OpenSearch、RRF、rerank、LLM 回答、文件夹范围检索、文档移动不重索引，并清理临时资料和账号。

| 现象 | 优先检查 |
| --- | --- |
| 页面不可访问 | docker compose ps；前端是否监听 192.168.1.33:18080 |
| 上传持续处理中 | logs worker；检查 Redis、MinIO、文件类型、OCR |
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

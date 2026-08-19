# 企业知识库 MVP（路线 B）

这是矿用机械制造企业知识库的第一版自建工程骨架。当前范围是人资部门的 Word、Excel、PDF 和扫描件资料；目标是先验证“上传 → 解析/OCR → 切片 → 向量与全文索引 → 权限过滤 → 问答”的完整链路。

## 组件边界

| 组件 | MVP 职责 |
| --- | --- |
| MinIO | 原始文件、解析产物和页面预览等对象存储 |
| MySQL | 文档、版本、切片、权限、任务等事务型元数据 |
| Milvus | 语义向量检索 |
| OpenSearch | 关键词检索与后续混合检索 |
| Redis | 异步任务队列、缓存和分布式锁 |
| API / Worker | 登录鉴权、部门权限、上传、混合检索、问答和异步入库 |

Neo4j、原生 CAD 解析、图纸多模态检索均不在首个试点中启用。后续接入图纸时扩展解析器、派生预览图和多模态索引，不需要推翻本项目的对象、元数据或权限模型。

## 目录

```text
deploy/                 Docker Compose 与部署说明
database/mysql/         MySQL 初始化及后续迁移脚本
services/api/           同步 API（上传、检索、问答）
services/worker/        异步处理（解析、OCR、切片、索引）
services/frontend/      登录、知识库、智能体、用户、连接器及审计页面
deploy/smoke-test.sh    完整业务与越权隔离验收脚本
```

## 首次部署（Ubuntu 服务器）

不要把真实密码或模型 API Key 放进仓库。将此目录复制到 Ubuntu 服务器后执行：

```bash
cd /home/ai/zhishiku
cp .env.example .env
chmod 600 .env
nano .env
mkdir -p /home/ai/zhishiku/data
sudo sysctl -w vm.max_map_count=262144
docker compose --env-file .env -f deploy/docker-compose.yml config
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml ps
```

持久化数据在 `DATA_ROOT`，而不是代码目录。数据服务默认绑定 `127.0.0.1`；在接入 Nginx、SSO、TLS 和审计前，切勿把 MinIO、MySQL、Redis、Milvus、OpenSearch 端口直接暴露给办公网或公网。

详细说明见 [deploy/README.md](deploy/README.md)。

## 模型配置与 MVP 验证

默认 `local_hash` 向量与本地摘录回答用于验证整条工程链路，不代表生产语义质量。正式试点前，在服务器的 `.env` 中切换经批准的中文 Embedding、Rerank 和大模型接口：

```dotenv
EMBEDDING_BASE_URL=https://your-model-gateway/v1
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=your-embedding-model
LLM_BASE_URL=https://your-model-gateway/v1
LLM_API_KEY=...
LLM_MODEL=your-chat-model
# 可选；未配置时使用本地词项重排序
RERANK_BASE_URL=https://your-rerank-gateway/v1
RERANK_API_KEY=...
RERANK_MODEL=your-rerank-model
```

管理与问答页面通过 `FRONTEND_PORT`（当前 18080）提供，浏览器只访问前端和同源 API。升级当前服务器执行 `bash deploy/upgrade-v05.sh`；初始管理员密码保存在仅部署用户可读的 `.initial-admin-password`，首次登录后应修改并删除该文件。

需要从办公网访问时，只设置 `FRONTEND_BIND_IP` 为服务器内网地址（当前为 `192.168.1.33`）。`HOST_BIND_IP` 应继续保持 `127.0.0.1`，确保 API 和数据服务不直接暴露给办公网。

完整验收可执行 `bash deploy/smoke-test.sh`。脚本验证上传、解析、切片、Milvus、OpenSearch、混合检索、重排序、引用回答、普通员工禁止管理以及跨部门 403，并清理测试资料、停用临时账号。

切换 Embedding 模型后，应使用 `deploy/queue-reindex.py` 为所有有效文档排队重建索引；`deploy/verify-models.py` 和 `deploy/verify-indexes.py` 分别用于验证模型接口与索引维度/完成度。脚本不读取或输出 API Key。

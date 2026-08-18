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
| API / Worker | 后续接入上传、检索、问答和异步入库逻辑 |

Neo4j、原生 CAD 解析、图纸多模态检索均不在首个试点中启用。后续接入图纸时扩展解析器、派生预览图和多模态索引，不需要推翻本项目的对象、元数据或权限模型。

## 目录

```text
deploy/                 Docker Compose 与部署说明
database/mysql/         MySQL 初始化及后续迁移脚本
services/api/           同步 API（上传、检索、问答）
services/worker/        异步处理（解析、OCR、切片、索引）
services/frontend/      员工问答及管理端前端（待实现）
shared/                 跨服务的数据模型与公共库（待实现）
tests/                  集成与验收测试（待实现）
```

## 首次部署（Ubuntu 服务器）

不要把真实密码或模型 API Key 放进仓库。将此目录复制到 Ubuntu 服务器后执行：

```bash
cd /opt/enterprise-kb
cp .env.example .env
chmod 600 .env
nano .env
sudo mkdir -p /data/enterprise-kb
sudo sysctl -w vm.max_map_count=262144
docker compose --env-file .env -f deploy/docker-compose.yml config
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml ps
```

持久化数据在 `DATA_ROOT`，而不是代码目录。数据服务默认绑定 `127.0.0.1`；在接入 Nginx、SSO、TLS 和审计前，切勿把 MinIO、MySQL、Redis、Milvus、OpenSearch 端口直接暴露给办公网或公网。

详细说明见 [deploy/README.md](deploy/README.md)。

## 模型配置与 MVP 验证

上传和 OCR/切片不依赖模型。若要完成向量检索和问答，在服务器的 `.env` 中填写 OpenAI 兼容的模型接口：

```dotenv
EMBEDDING_BASE_URL=https://your-model-gateway/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=your-embedding-model
LLM_BASE_URL=https://your-model-gateway/v1
LLM_API_KEY=...
LLM_MODEL=your-chat-model
# 可选；未配置时以 RRF 融合结果作为最终排序
RERANK_BASE_URL=https://your-rerank-gateway/v1
RERANK_API_KEY=...
RERANK_MODEL=your-rerank-model
```

管理与问答页面通过 `FRONTEND_PORT`（默认 18080）在服务器本机提供；它仅代理到 API。首次升级已有环境时，先按 [database/mysql/README.md](database/mysql/README.md) 执行 `002_agent_platform.sql`。

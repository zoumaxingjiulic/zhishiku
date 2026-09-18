# 部署说明

本 Compose 供 Ubuntu 上的单机 MVP 使用，服务镜像均固定了版本。它不是高可用生产集群配置；生产阶段应将 MySQL、Milvus、OpenSearch 的备份、监控、容量与容灾单独设计。

## 启动前检查

```bash
cd /home/ai/zhishiku
cp .env.example .env
chmod 600 .env
nano .env
sudo sysctl -w vm.max_map_count=262144
mkdir -p /home/ai/zhishiku/data
docker compose --env-file .env -f deploy/docker-compose.yml config
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
```

首次启动时，MySQL 会自动执行 `database/mysql/001_initial_schema.sql`；该机制只针对全新的 MySQL 数据目录。后续变更必须新增编号迁移脚本并由迁移工具执行，不能修改已在生产环境执行过的脚本。

## 验证

```bash
docker compose --env-file .env -f deploy/docker-compose.yml ps
curl http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:18080/healthz
curl -k -u "admin:${OPENSEARCH_INITIAL_ADMIN_PASSWORD}" https://127.0.0.1:9200/_cluster/health
```

已有环境先核对 [迁移记录](../database/mysql/README.md)，备份后按编号执行尚未应用的迁移，每个文件仅执行一次。当前平台 1.1 的迁移与验收入口为：

```bash
bash deploy/apply-mysql-migration.sh database/mysql/012_platform_quality_runtime.sql
docker compose --env-file .env \
  -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml \
  exec -T api python - < deploy/verify-platform-v11.py
```

验收脚本会创建临时业务数据并在结束时清理或停用，仅在已确认的验收环境运行。初始管理员密码通过 `.env` 的 `ADMIN_PASSWORD` 配置；该文件不得提交。首次登录后修改密码。办公网正式开放前应增加 HTTPS 反向代理，将 `AUTH_COOKIE_SECURE` 改为 `true`，并配置备份与监控。

Compose 的一次性 `minio-init` 服务等待 MinIO healthy 后，使用同一固定版本镜像中的 `mc`，按 `MINIO_BUCKET` 幂等创建桶（`mc mb --ignore-existing`）；桶已存在时也成功退出。API 等待初始化成功退出后才启动，Worker 仍只等待 MinIO healthy。初始化失败时检查 `.env` 中的 MinIO 凭据和桶名后重新启动服务；初始化命令不输出凭据。API 启动和 readiness 探针都不创建桶。

## 健康检查与前端缓存

API `/healthz` 只表示进程存活，不访问外部依赖。`/readyz` 逐项检查 MySQL `SELECT 1`、MinIO 桶、Milvus 连接与列举集合、OpenSearch 集群，以及 embedding `/models` 是否包含配置的模型 ID。Milvus 空集合环境也可就绪，OpenSearch 接受 green/yellow，red 或请求超时会失败。embedding 必须配置可用的模型服务，开发环境的 local_hash 不替代此依赖检查。

任一必需项失败时 `/readyz` 返回 HTTP 503，成功返回 200；响应仅含 `status` 和 `checks`，不会回传异常或连接凭据。rerank 完全未配置时返回 `not_configured` 且不阻塞；填写任一 rerank 配置后即要求 URL、模型 ID 完整且模型可用。探针不执行 DDL、不创建桶或集合。

Compose 用 `/readyz` 检查 API，Frontend 等待 API `service_healthy` 后启动。使用本地模型时同时加载 `deploy/docker-compose.models.yml`，覆盖配置让 API 等待 Infinity `service_healthy`，保留基础设施及 MinIO 初始化依赖。Infinity 的 `/models` 健康检查预留 180 秒模型加载时间；更慢的硬件可调整 `start_period`。仅启动基础 Compose 时须配置可访问的外部模型服务。

Worker 和 chat-runner 执行 `python -m enterprise_kb.health`，只运行 MySQL `SELECT 1`；失败退出 1 且仅输出异常类型。该探针表示数据库可访问，不代表后台任务持续取得进展。Docker 不会因 unhealthy 自动重启服务；主进程退出由现有 `unless-stopped` 策略处理。

Frontend 的 `/healthz` 由 Nginx 直接返回 200。HTML 与非哈希资源的成功响应使用 `Cache-Control: no-store`，`/assets/` 下带构建哈希的资源使用一年 immutable 缓存；缺失静态资源返回 404。缓存头不使用 `always`，404/5xx 不携带一年缓存；安全头继续使用 `always`。保留 SPA fallback、`/api/` 代理、200 MiB 上传上限和 300 秒代理读写超时，并启用 gzip、nosniff、SAMEORIGIN 与 referrer policy。

## 本地质量门禁与镜像构建

使用 Python 3.12、Node 22 和可用的 Docker daemon，在仓库根目录执行：

```bash
python -m pip install -r requirements-test.txt
python -m pip install ./shared/python
python tools/check_repository_hygiene.py
python -m pytest -q
cd services/frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
cd ../..
docker compose --env-file .env.example -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml config --quiet
docker build --check -f services/api/Dockerfile .
docker build --check -f services/worker/Dockerfile .
docker build --check -f services/frontend/Dockerfile services/frontend
```

GitHub Actions 按以上顺序执行门禁。卫生脚本只检查 `git ls-files` 中的文件，报告路径和规则名，不回显密钥；`.env.example` 与 `CHANGE_ME` 占位符可提交，示例文件中的真实密钥模式仍会阻止通过。

API 与 Worker 保留仓库根构建上下文，以安装 `shared/python`；根与各 Dockerfile 专用忽略文件排除凭据、缓存和本地数据。Frontend 使用服务目录作为上下文。API 构建 `enterprise-kb-api:${APP_IMAGE_TAG:-local}`，chat-runner 使用同名镜像且不重复构建。更新时使用相同 `APP_IMAGE_TAG` 并同时更新两者；单独启动 chat-runner 前须先构建 API 镜像。

在有 Docker 的验收环境构建并启动前端后，从 `dist/index.html` 取实际哈希资源路径，请求该资源及一个不存在的哈希资源，例如 `/assets/missing-12345678.js`。前者应为 200 且携带 immutable，后者应为 404 且不携带长期缓存；同时检查 `/index.html` 和 SPA 路由为 no-store、错误响应仍有安全头。`build --check` 不能替代这些运行时响应头验收。

## 网络与备份边界

- 所有数据服务仅映射到 `HOST_BIND_IP`，默认 `127.0.0.1`。
- 员工访问应经过后续的 HTTPS 反向代理、统一认证和应用层权限校验。
- 备份至少应覆盖 MySQL 逻辑备份、MinIO 对象、Milvus 数据目录和 OpenSearch 快照；恢复演练与备份同等重要。
- 此 MVP 中 Milvus 使用单机内嵌 etcd 与本地持久化卷。原始文件仍只存 MinIO；后续扩容到集群时，再迁移 Milvus 的底层对象存储与协调组件。

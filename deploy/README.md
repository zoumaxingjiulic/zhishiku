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

已有环境升级到认证与权限版本：

```bash
bash deploy/upgrade-v05.sh
bash deploy/smoke-test.sh
```

初始管理员密码只写入 `.initial-admin-password`（权限 600）。首次登录后修改密码，并删除该临时文件。办公网正式开放前应增加 HTTPS 反向代理，将 `AUTH_COOKIE_SECURE` 改为 `true`，并配置备份与监控。

MinIO 桶在基础设施启动后由管理员执行初始化命令创建；后续 API 也会在启动检查中确保该桶存在。

## 健康检查与前端缓存

API `/healthz` 只表示进程存活，不访问外部依赖。`/readyz` 逐项检查 MySQL `SELECT 1`、MinIO 桶、Milvus 连接与列举集合、OpenSearch 集群，以及 embedding `/models` 是否包含配置的模型 ID。Milvus 空集合环境也可就绪，OpenSearch 接受 green/yellow，red 或请求超时会失败。embedding 必须配置可用的模型服务，开发环境的 local_hash 不替代此依赖检查。

任一必需项失败时 `/readyz` 返回 HTTP 503，成功返回 200；响应仅含 `status` 和 `checks`，不会回传异常或连接凭据。rerank 完全未配置时返回 `not_configured` 且不阻塞；填写任一 rerank 配置后即要求 URL、模型 ID 完整且模型可用。探针不执行 DDL、不创建桶或集合。

Compose 用 `/readyz` 检查 API，Frontend 等待 API `service_healthy` 后启动。使用本地模型时同时加载 `deploy/docker-compose.models.yml`，Infinity 的 `/models` 健康检查预留 180 秒模型加载时间；更慢的硬件可调整 `start_period`。仅启动基础 Compose 时须配置可访问的外部模型服务。

Worker 和 chat-runner 执行 `python -m enterprise_kb.health`，只运行 MySQL `SELECT 1`；失败退出 1 且仅输出异常类型。该探针表示数据库可访问，不代表后台任务持续取得进展。Docker 不会因 unhealthy 自动重启服务；主进程退出由现有 `unless-stopped` 策略处理。

Frontend 的 `/healthz` 由 Nginx 直接返回 200。HTML 与非哈希资源使用 `Cache-Control: no-store`，`/assets/` 下带构建哈希的资源使用一年 immutable 缓存；缺失静态资源返回 404。保留 SPA fallback、`/api/` 代理、200 MiB 上传上限和 300 秒代理读写超时，并启用 gzip、nosniff、SAMEORIGIN 与 referrer policy。

## 网络与备份边界

- 所有数据服务仅映射到 `HOST_BIND_IP`，默认 `127.0.0.1`。
- 员工访问应经过后续的 HTTPS 反向代理、统一认证和应用层权限校验。
- 备份至少应覆盖 MySQL 逻辑备份、MinIO 对象、Milvus 数据目录和 OpenSearch 快照；恢复演练与备份同等重要。
- 此 MVP 中 Milvus 使用单机内嵌 etcd 与本地持久化卷。原始文件仍只存 MinIO；后续扩容到集群时，再迁移 Milvus 的底层对象存储与协调组件。

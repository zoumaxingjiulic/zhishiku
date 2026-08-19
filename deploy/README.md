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
curl -k -u "admin:${OPENSEARCH_INITIAL_ADMIN_PASSWORD}" https://127.0.0.1:9200/_cluster/health
```

已有环境升级到认证与权限版本：

```bash
bash deploy/upgrade-v05.sh
bash deploy/smoke-test.sh
```

初始管理员密码只写入 `.initial-admin-password`（权限 600）。首次登录后修改密码，并删除该临时文件。办公网正式开放前应增加 HTTPS 反向代理，将 `AUTH_COOKIE_SECURE` 改为 `true`，并配置备份与监控。

MinIO 桶在基础设施启动后由管理员执行初始化命令创建；后续 API 也会在启动检查中确保该桶存在。

## 网络与备份边界

- 所有数据服务仅映射到 `HOST_BIND_IP`，默认 `127.0.0.1`。
- 员工访问应经过后续的 HTTPS 反向代理、统一认证和应用层权限校验。
- 备份至少应覆盖 MySQL 逻辑备份、MinIO 对象、Milvus 数据目录和 OpenSearch 快照；恢复演练与备份同等重要。
- 此 MVP 中 Milvus 使用单机内嵌 etcd 与本地持久化卷。原始文件仍只存 MinIO；后续扩容到集群时，再迁移 Milvus 的底层对象存储与协调组件。

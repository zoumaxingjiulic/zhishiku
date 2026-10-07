# MySQL migrations

When MySQL starts with an empty data directory, the official image executes every
`*.sql` file mounted in `/docker-entrypoint-initdb.d` in filename order. With this
repository's Compose file that means `001_initial_schema.sql` through
`016_bootstrap.sql`, in order. `016_bootstrap.sql` invokes the guarded retirement
script in `manual/` only for a brand-new empty database. These files are not rerun after the
data directory has been initialized.

For an already running environment, apply each later numbered migration exactly once from the project root:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql \
  sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  < database/mysql/002_agent_platform.sql
```

Then confirm the first knowledge base and agent:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml exec mysql \
  sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SELECT id, code, name FROM knowledge_base; SELECT id, code, name FROM agent;"'
```

Do not edit a migration that has been applied to any environment. Add a new numbered file for every schema change.

If `002_agent_platform.sql` was manually applied in a non-UTF-8 terminal and its seeded Chinese labels display as mojibake, apply `003_fix_platform_seed_encoding.sql` once using the same command pattern.

`005_auth_rbac_audit_connectors.sql` adds local authentication, audit logs and connector metadata. After checking the applied schema and taking a backup, apply this migration only if it has not already been applied: `bash deploy/apply-mysql-migration.sh database/mysql/005_auth_rbac_audit_connectors.sql`. The migration runner does not detect previously applied migrations; do not rerun them.

`006_fix_company_seed_tech_department.sql` repairs the company-name seed encoding and creates the technical department and its isolated knowledge base. It is idempotent.

Apply a single migration from the project root with `bash deploy/apply-mysql-migration.sh database/mysql/<migration>.sql`.

`007_department_based_permissions.sql` adds the platform-administrator department, migrates the default administrator, and adds soft deletion for accounts. Runtime authorization no longer depends on role assignments after this migration.

`008_cleanup_legacy_e2e_accounts.sql` soft-deletes legacy automated test accounts that predate automatic smoke-test cleanup.

`009_knowledge_folders.sql` adds the virtual folder tree, document folder ownership and optimistic-lock versions. Existing documents remain in the knowledge-base root directory.

`010_enterprise_agent_platform.sql` historically added encrypted model-gateway profiles, per-agent model binding, department ACLs, prompt templates, agent application records, and several launch modes. Migration 016 removes the retired department-agent ACL and workflow mode runtime support; do not use 010's schema as the current application contract.

`011_mcp_agent_runtime_observability.sql` adds encrypted MCP connector credentials, discovered tool schemas, per-agent read-only tool grants, assistant tool-call metadata, and privacy-preserving agent run traces. It seeds ERP/OA connector metadata only; bearer tokens must be configured at deployment time and are never committed.

`012_platform_quality_runtime.sql` historically added versioned agent configurations, durable chat/workflow tasks, retrieval evaluation sets/results, user feedback and optional parent text/knowledge-base processing settings. Migration 016 later removes the unused workflow table. Back up first; apply exactly once before deploying services that need this schema. It does not rebuild or delete existing documents/vectors.

`013_enterprise_assistant.sql` adds the reserved enterprise assistant, intent-decision records, the reusable Skill catalog, and permission mappings. It does not delete or alter existing workflows, workflow runs, knowledge bases, documents, vectors, or full-text indexes.

`014_user_scoped_capabilities.sql` adds direct user grants for knowledge bases, read-only MCP tools, and agents. It materializes assignments from active legacy departments into direct user-agent grants once. `agent_department_acl` was an audit/backfill source until migration 016; new code does not keep it synchronized. The migration only adds authorization metadata and does not alter or rebuild documents, chunks, vectors, or full-text indexes.

Migration 014 is safe to rerun only for recovery from an interrupted attempt: its table creation is conditional and its legacy backfill uses `INSERT IGNORE`, so an existing direct user-agent grant is never overwritten. If it fails, do not drop any table or delete ACL rows. Fix the reported cause, rerun the same migration, then confirm all three tables and that no legacy assignment is missing:

```sql
SHOW TABLES LIKE 'user\_%\_acl';
SELECT COUNT(*) AS missing_backfill
FROM (
  SELECT DISTINCT ud.user_id, ada.agent_id
  FROM agent_department_acl ada
  JOIN agent a ON a.id = ada.agent_id AND a.status = 'active'
  JOIN department d ON d.id = ada.department_id AND d.status = 1
  JOIN user_department ud ON ud.department_id = ada.department_id
  JOIN app_user u ON u.id = ud.user_id AND u.status = 1 AND u.deleted_at IS NULL
  WHERE ada.permission = 'use'
) expected
LEFT JOIN user_agent_acl actual
  ON actual.user_id = expected.user_id AND actual.agent_id = expected.agent_id
WHERE actual.user_id IS NULL;
```

`missing_backfill` must be `0`. Also inspect `SHOW CREATE TABLE` for each new ACL table before marking the migration complete. Do not routinely rerun already successful migrations.

Do not roll back application code alone after any permission edit made by the 014-era application. The legacy department table is stale by design and an older application could overgrant access. Prefer a forward fix. If rollback is unavoidable, restore the verified pre-014 database backup during a maintenance window and separately reconcile any business data created after that backup. Before the first post-014 permission edit, an application rollback is technically possible only as a temporary return to the old department-distribution semantics; it still requires explicit security review and must not be described as preserving the new user-scoped model.

## 015–016：遗留工作流与旧权限表退役

已有库不能直接运行 `016_bootstrap.sql`，也不能通过通用迁移命令运行 `manual/016_retire_legacy_schema.sql`。当前 `schema_migration` 台账从 015 开始；001–014 仍须按表结构和既有部署记录确认，不能重复执行。详细步骤见 [退役操作手册](../../deploy/README.md#遗留结构退役)。

在维护窗口先执行 `bash deploy/backup-legacy-schema.sh`。脚本对整库做一致性逻辑备份、SHA-256 校验、临时数据库恢复及关键表计数对账，并生成迁移前有效检索策略快照；只有精确命名的临时库删除成功后，才写入同时绑定备份及快照哈希的 `.verified` 标记。备份在 `backups/`，已被 Git 忽略，不得上传仓库。保存脚本打印的备份路径。

备份脚本已在备份目录生成 `retrieval-before.json`。先执行非破坏性 015，再现场核对当前有效策略：

```bash
bash deploy/apply-mysql-migration.sh database/mysql/015_consolidate_agent_retrieval.sql
docker compose --env-file .env -f deploy/docker-compose.yml exec -T api \
  python - --phase after < deploy/verify-legacy-migration.py > "$BACKUP_DIR/retrieval-after.json"
cmp "$BACKUP_DIR/retrieval-before.json" "$BACKUP_DIR/retrieval-after.json"
```

其中 `BACKUP_DIR` 应明确设置为刚完成恢复验证的备份文件所在目录；不要把示例当作自动赋值。冲突、无效旧策略或策略差异必须先处理，不能跳过。迁移 015 只合并仍有效的检索配置，不改文档、切片和索引。发布不再使用旧结构的新应用并完成登录、权限、问答、评测和健康检查后，运行：

```bash
bash deploy/retire-legacy-schema.sh "$BACKUP_DIR/enterprise_kb.sql"
```

退役脚本不接受用户传入的策略快照；它会重新读取当前生产库的有效策略，与绑定到这次备份的迁移前快照核对。016 还会检查旧 ACL 是否全部转为用户授权、旧工作流和文档资产是否为空、旧模型列是否为空及其他表是否新增外键。只有检查全部通过才删除 `workflow_run`、`user_role`、`app_role`、`agent_department_acl`、`document_asset` 和三个重复列。`document_department_acl`、现用用户 ACL、文档和索引都保留。DDL 不能作为单一事务回滚；若中途失败，不要只回滚应用镜像，保留备份并先排查数据库实际状态。

`017_align_enterprise_assistant_prompt.sql` 只修正企业总助手原始默认描述和系统提示词，不覆盖管理员自定义内容，不修改表结构。已有库在 016 完成后执行一次；新库初始化时自动执行。

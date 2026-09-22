# MySQL migrations

When MySQL starts with an empty data directory, the official image executes every
`*.sql` file mounted in `/docker-entrypoint-initdb.d` in filename order. With this
repository's Compose file that means `001_initial_schema.sql` through
`013_enterprise_assistant.sql`, in order. These files are not rerun after the
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

`010_enterprise_agent_platform.sql` adds encrypted model-gateway profiles, per-agent model binding, explicit agent department ACLs, personal prompt templates, agent application records, and launch metadata for chat, form, workflow, dashboard, and external agents.

`011_mcp_agent_runtime_observability.sql` adds encrypted MCP connector credentials, discovered tool schemas, per-agent read-only tool grants, assistant tool-call metadata, and privacy-preserving agent run traces. It seeds ERP/OA connector metadata only; bearer tokens must be configured at deployment time and are never committed.

`012_platform_quality_runtime.sql` adds versioned agent configurations, durable chat/workflow tasks, retrieval evaluation sets/results, user feedback and optional parent text/knowledge-base processing settings. Back up first; apply exactly once before deploying 1.1 services. It does not rebuild or delete existing documents/vectors.

`013_enterprise_assistant.sql` adds the reserved enterprise assistant, intent-decision records, the reusable Skill catalog, and permission mappings. It does not delete or alter existing workflows, workflow runs, knowledge bases, documents, vectors, or full-text indexes.

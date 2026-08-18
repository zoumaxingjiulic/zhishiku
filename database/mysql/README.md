# MySQL migrations

001_initial_schema.sql runs automatically only when MySQL starts with an empty data directory.

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

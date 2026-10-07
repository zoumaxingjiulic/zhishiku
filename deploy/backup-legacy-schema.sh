#!/usr/bin/env bash
set -euo pipefail

# Full logical backup plus a temporary-database restore drill. The resulting
# .verified file is consumed by retire-legacy-schema.sh; no secrets are logged.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
umask 077
BACKUP_DIR="$PROJECT_DIR/backups/legacy-cleanup-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 0700 -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/enterprise_kb.sql"
VERIFY_DB="enterprise_kb_retirement_verify_$(date -u +%Y%m%d%H%M%S)_$$"

compose=(docker compose --env-file .env -f deploy/docker-compose.yml)
created=0
drop_verify_database() {
  [[ "$VERIFY_DB" =~ ^enterprise_kb_retirement_verify_[0-9]{14}_[0-9]+$ ]] || return 1
  printf 'DROP DATABASE `%s`;\n' "$VERIFY_DB" |
    "${compose[@]}" exec -T mysql sh -c \
      'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' >/dev/null
}
cleanup() {
  if (( created == 1 )); then
    drop_verify_database || echo "WARNING: temporary restore database needs manual cleanup: $VERIFY_DB" >&2
  fi
}
trap cleanup EXIT

"${compose[@]}" exec -T mysql sh -c \
  'exec mysqldump --single-transaction --routines --triggers --events \
    --hex-blob --set-gtid-purged=OFF --default-character-set=utf8mb4 \
    -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > "$BACKUP_FILE"
test -s "$BACKUP_FILE"
sha256sum "$BACKUP_FILE" > "$BACKUP_DIR/enterprise_kb.sql.sha256"
sha256sum --check "$BACKUP_DIR/enterprise_kb.sql.sha256"

printf 'CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\n' "$VERIFY_DB" |
  "${compose[@]}" exec -T mysql sh -c \
    'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
created=1
"${compose[@]}" exec -e VERIFY_DATABASE="$VERIFY_DB" -T mysql sh -c \
  'exec mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" "$VERIFY_DATABASE"' \
  < "$BACKUP_FILE" >/dev/null

COUNT_SQL="SELECT 'app_user', COUNT(*) FROM app_user UNION ALL
SELECT 'department', COUNT(*) FROM department UNION ALL
SELECT 'knowledge_base', COUNT(*) FROM knowledge_base UNION ALL
SELECT 'document', COUNT(*) FROM document UNION ALL
SELECT 'document_version', COUNT(*) FROM document_version UNION ALL
SELECT 'content_unit', COUNT(*) FROM content_unit UNION ALL
SELECT 'agent', COUNT(*) FROM agent UNION ALL
SELECT 'user_knowledge_base_acl', COUNT(*) FROM user_knowledge_base_acl UNION ALL
SELECT 'user_connector_tool_acl', COUNT(*) FROM user_connector_tool_acl UNION ALL
SELECT 'user_agent_acl', COUNT(*) FROM user_agent_acl UNION ALL
SELECT 'chat_session', COUNT(*) FROM chat_session UNION ALL
SELECT 'chat_message', COUNT(*) FROM chat_message UNION ALL
SELECT 'evaluation_run', COUNT(*) FROM evaluation_run UNION ALL
SELECT 'workflow_run', COUNT(*) FROM workflow_run UNION ALL
SELECT 'user_role', COUNT(*) FROM user_role UNION ALL
SELECT 'app_role', COUNT(*) FROM app_role UNION ALL
SELECT 'agent_department_acl', COUNT(*) FROM agent_department_acl UNION ALL
SELECT 'document_asset', COUNT(*) FROM document_asset ORDER BY 1;"

printf '%s\n' "$COUNT_SQL" | "${compose[@]}" exec -T mysql sh -c \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" --batch --skip-column-names' \
  > "$BACKUP_DIR/source-counts.tsv"
printf '%s\n' "$COUNT_SQL" | "${compose[@]}" exec -e VERIFY_DATABASE="$VERIFY_DB" -T mysql sh -c \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$VERIFY_DATABASE" --batch --skip-column-names' \
  > "$BACKUP_DIR/restored-counts.tsv"
diff -u "$BACKUP_DIR/source-counts.tsv" "$BACKUP_DIR/restored-counts.tsv"

"${compose[@]}" exec -T api python - --phase before \
  < deploy/verify-legacy-migration.py > "$BACKUP_DIR/retrieval-before.json"
test -s "$BACKUP_DIR/retrieval-before.json"

drop_verify_database
created=0
backup_sha="$(sha256sum "$BACKUP_FILE" | cut -d ' ' -f 1)"
policy_sha="$(sha256sum "$BACKUP_DIR/retrieval-before.json" | cut -d ' ' -f 1)"
printf '%s\n%s\n' "$backup_sha" "$policy_sha" > "$BACKUP_DIR/enterprise_kb.sql.verified"
echo "Verified backup: $BACKUP_FILE"
echo "Restore drill matched key table counts; temporary database removed."

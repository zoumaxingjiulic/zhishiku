#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法: $0 <migration.sql>" >&2
  exit 2
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATION_FILE="$1"
if [[ "$MIGRATION_FILE" != /* ]]; then
  MIGRATION_FILE="$PROJECT_DIR/$MIGRATION_FILE"
fi
if [[ ! -f "$MIGRATION_FILE" ]]; then
  echo "迁移文件不存在: $MIGRATION_FILE" >&2
  exit 2
fi

cd "$PROJECT_DIR"
docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql \
  sh -c 'exec mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  < "$MIGRATION_FILE"

echo "MySQL migration applied: ${MIGRATION_FILE#$PROJECT_DIR/}"

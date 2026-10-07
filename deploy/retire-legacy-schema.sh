#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "用法: $0 <已恢复验证的备份.sql> <迁移前策略.json> <迁移后策略.json>" >&2
  exit 2
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FILE="$(realpath -- "$1")"
BEFORE_FILE="$(realpath -- "$2")"
AFTER_FILE="$(realpath -- "$3")"
BACKUP_DIR="$(dirname "$BACKUP_FILE")"
if [[ "$BACKUP_FILE" != "$PROJECT_DIR"/backups/legacy-cleanup-*/enterprise_kb.sql ]]; then
  echo "备份路径必须位于本项目的 backups/legacy-cleanup-* 目录" >&2
  exit 2
fi
test -s "$BACKUP_FILE"
test -s "$BEFORE_FILE"
test -s "$AFTER_FILE"
test -f "$BACKUP_FILE.verified"
test -f "$BACKUP_FILE.sha256"
cd "$BACKUP_DIR"
sha256sum --check "$(basename "$BACKUP_FILE").sha256"
backup_sha="$(sha256sum "$BACKUP_FILE" | cut -d ' ' -f 1)"
verified_sha="$(tr -d '\r\n' < "$BACKUP_FILE.verified")"
if [[ "$backup_sha" != "$verified_sha" ]]; then
  echo "备份哈希与恢复验证记录不一致" >&2
  exit 1
fi
cmp --silent "$BEFORE_FILE" "$AFTER_FILE" || {
  echo "015 前后有效检索策略不同，停止删表" >&2
  exit 1
}

cd "$PROJECT_DIR"
{
  printf "SET @legacy_cleanup_backup_verified=1, @legacy_cleanup_backup_sha256='%s', @legacy_cleanup_policy_verified=1;\n" "$backup_sha"
  sed -n '1,$p' database/mysql/manual/016_retire_legacy_schema.sql
} | docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql \
  sh -c 'exec mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'

echo "Legacy schema retirement applied; backup retained at $BACKUP_FILE"

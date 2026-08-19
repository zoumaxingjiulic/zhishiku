#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

ensure_env() {
  local key="$1" value="$2"
  grep -q "^${key}=" .env || printf '%s=%s\n' "$key" "$value" >> .env
}

if ! grep -q '^JWT_SECRET=.' .env; then
  set_env JWT_SECRET "$(openssl rand -hex 32)"
fi
if ! grep -q '^ADMIN_PASSWORD=.' .env; then
  initial_password="Kb!$(openssl rand -hex 16)"
  set_env ADMIN_PASSWORD "$initial_password"
  printf '%s\n' "$initial_password" > .initial-admin-password
  chmod 600 .initial-admin-password
fi

ensure_env JWT_EXPIRE_MINUTES 480
ensure_env AUTH_COOKIE_SECURE false
ensure_env ADMIN_USERNAME admin
ensure_env ADMIN_DISPLAY_NAME 平台管理员
ensure_env EMBEDDING_PROVIDER local_hash
ensure_env LOCAL_EMBEDDING_DIM 384
ensure_env LOCAL_TEST_MODE true
ensure_env MILVUS_COLLECTION kb_content_units_v1
ensure_env OPENSEARCH_INDEX kb-content-units-v1
ensure_env MAX_UPLOAD_BYTES 209715200
chmod 600 .env

column_exists="$(docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql \
  sh -c 'mysql -N -s -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=\"app_user\" AND column_name=\"password_hash\""')"
if [[ "$column_exists" == "0" ]]; then
  docker compose --env-file .env -f deploy/docker-compose.yml exec -T mysql \
    sh -c 'mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
    < database/mysql/005_auth_rbac_audit_connectors.sql
fi

docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml ps

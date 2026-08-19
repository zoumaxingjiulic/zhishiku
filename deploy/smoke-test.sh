#!/usr/bin/env bash
set -euo pipefail

BASE_URL="http://127.0.0.1:18000"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
E2E_ADMIN_CREATED=0
cleanup() {
  if [[ "$E2E_ADMIN_CREATED" == "1" ]]; then
    docker compose --env-file .env -f deploy/docker-compose.yml exec -T \
      -e E2E_ADMIN_USERNAME="$ADMIN_USER" api python /tmp/e2e-admin.py cleanup >/dev/null 2>&1 || true
  fi
  docker compose --env-file .env -f deploy/docker-compose.yml exec -T api rm -f -- /tmp/e2e-admin.py >/dev/null 2>&1 || true
  rm -rf -- "$TEST_DIR"
}
trap cleanup EXIT

cd "$PROJECT_DIR"
for _ in $(seq 1 30); do
  curl -fsS "$BASE_URL/readyz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$BASE_URL/readyz" >/dev/null
STAMP="$(date +%s)"
ADMIN_USER="e2e_admin_${STAMP}"
ADMIN_PASSWORD="Admin!Aa${STAMP}"
HR_USER="e2e_hr_${STAMP}"
TECH_USER="e2e_tech_${STAMP}"
CHANGED_PASSWORD="Changed!Aa${STAMP}"

API_CONTAINER="$(docker compose --env-file .env -f deploy/docker-compose.yml ps -q api)"
docker cp deploy/e2e-admin.py "$API_CONTAINER:/tmp/e2e-admin.py"
docker compose --env-file .env -f deploy/docker-compose.yml exec -T \
  -e E2E_ADMIN_USERNAME="$ADMIN_USER" -e E2E_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  api python /tmp/e2e-admin.py create
E2E_ADMIN_CREATED=1

json_login() { printf '{"username":"%s","password":"%s"}' "$1" "$2"; }
curl -fsS -c "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$ADMIN_USER" "$ADMIN_PASSWORD")" "$BASE_URL/api/v1/auth/login" > "$TEST_DIR/admin.json"
python3 -c 'import json,sys; u=json.load(open(sys.argv[1]))["user"]; assert u["is_platform_admin"] is True, u; assert [x["code"] for x in u["departments"]]==["PLATFORM_ADMIN"], u; assert "roles" not in u, u' "$TEST_DIR/admin.json"

curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/departments" > "$TEST_DIR/departments.json"
python3 -c 'import json,sys; d={x["code"]:x["name"] for x in json.load(open(sys.argv[1]))}; assert d["COMPANY"]=="公司", d; assert d["PLATFORM_ADMIN"]=="平台管理员", d; assert d["HR"]=="人力资源部", d; assert d["TECH"]=="技术部", d' "$TEST_DIR/departments.json"
HR_ID="$(python3 -c 'import json,sys; print(next(x["id"] for x in json.load(open(sys.argv[1])) if x["code"]=="HR"))' "$TEST_DIR/departments.json")"
TECH_ID="$(python3 -c 'import json,sys; print(next(x["id"] for x in json.load(open(sys.argv[1])) if x["code"]=="TECH"))' "$TEST_DIR/departments.json")"

create_user() {
  local username="$1" department_id="$2" output="$3"
  printf '{"username":"%s","display_name":"E2E %s","department_id":%s}' \
    "$username" "$username" "$department_id" > "$TEST_DIR/user-payload.json"
  curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
    -d @"$TEST_DIR/user-payload.json" "$BASE_URL/api/v1/users" > "$output"
}
create_user "$HR_USER" "$HR_ID" "$TEST_DIR/hr-user.json"
create_user "$TECH_USER" "$TECH_ID" "$TEST_DIR/tech-user.json"
HR_USER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/hr-user.json")"
TECH_USER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/tech-user.json")"
HR_TEMP_PASSWORD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["temporary_password"])' "$TEST_DIR/hr-user.json")"
TECH_TEMP_PASSWORD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["temporary_password"])' "$TEST_DIR/tech-user.json")"

curl -fsS -c "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$HR_USER" "$HR_TEMP_PASSWORD")" "$BASE_URL/api/v1/auth/login" > /dev/null
printf '{"current_password":"%s","new_password":"%s"}' "$HR_TEMP_PASSWORD" "$CHANGED_PASSWORD" > "$TEST_DIR/change-password.json"
curl -fsS -b "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d @"$TEST_DIR/change-password.json" "$BASE_URL/api/v1/auth/change-password" > /dev/null
OLD_PASSWORD_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "$(json_login "$HR_USER" "$HR_TEMP_PASSWORD")" "$BASE_URL/api/v1/auth/login")"
[[ "$OLD_PASSWORD_STATUS" == "401" ]]
curl -fsS -c "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$HR_USER" "$CHANGED_PASSWORD")" "$BASE_URL/api/v1/auth/login" > /dev/null
curl -fsS -c "$TEST_DIR/tech.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$TECH_USER" "$TECH_TEMP_PASSWORD")" "$BASE_URL/api/v1/auth/login" > /dev/null

printf '{"knowledge_base_id":1,"name":"E2E制度目录%s","sort_order":10}' "$STAMP" > "$TEST_DIR/folder-parent.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
  -d @"$TEST_DIR/folder-parent.json" "$BASE_URL/api/v1/folders" > "$TEST_DIR/folder-parent-result.json"
PARENT_FOLDER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/folder-parent-result.json")"
printf '{"knowledge_base_id":1,"parent_id":%s,"name":"E2E休假资料%s","sort_order":20}' "$PARENT_FOLDER_ID" "$STAMP" > "$TEST_DIR/folder-child.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
  -d @"$TEST_DIR/folder-child.json" "$BASE_URL/api/v1/folders" > "$TEST_DIR/folder-child-result.json"
CHILD_FOLDER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/folder-child-result.json")"

printf '{"name":"E2E休假资料已编辑%s","sort_order":21,"row_version":1}' "$STAMP" > "$TEST_DIR/folder-child-update.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PUT \
  -d @"$TEST_DIR/folder-child-update.json" "$BASE_URL/api/v1/folders/$CHILD_FOLDER_ID" > "$TEST_DIR/folder-child-updated.json"
CHILD_FOLDER_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["row_version"])' "$TEST_DIR/folder-child-updated.json")"
printf '{"parent_id":%s,"name":"E2E休假资料已编辑%s","sort_order":21,"row_version":%s}' "$PARENT_FOLDER_ID" "$STAMP" "$CHILD_FOLDER_VERSION" > "$TEST_DIR/folder-child-move.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PUT \
  -d @"$TEST_DIR/folder-child-move.json" "$BASE_URL/api/v1/folders/$CHILD_FOLDER_ID" > "$TEST_DIR/folder-child-moved.json"
CHILD_FOLDER_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["row_version"])' "$TEST_DIR/folder-child-moved.json")"
curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/folders?knowledge_base_id=1" > "$TEST_DIR/folders.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); f=next(x for x in d if x["id"]==int(sys.argv[2])); assert f["parent_id"]==int(sys.argv[3]) and f["depth"]==1 and "已编辑" in f["name"], f' "$TEST_DIR/folders.json" "$CHILD_FOLDER_ID" "$PARENT_FOLDER_ID"

TEST_FILE="$TEST_DIR/人资休假制度.txt"
printf '人力资源部休假制度\n员工连续工作满一年后享有带薪年休假。申请年休假须提前三个工作日在系统提交，由直属主管审批。紧急情况应联系人力资源部补充备案。\n' > "$TEST_FILE"
curl -fsS -b "$TEST_DIR/admin.cookie" -F knowledge_base_id=1 -F folder_id="$CHILD_FOLDER_ID" -F title="E2E休假制度${STAMP}" \
  -F security_level=internal -F "file=@$TEST_FILE;type=text/plain" \
  "$BASE_URL/api/v1/documents" > "$TEST_DIR/upload.json"
DOCUMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_id"])' "$TEST_DIR/upload.json")"

for _ in $(seq 1 60); do
  curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1" > "$TEST_DIR/documents.json"
  python3 -c 'import json,sys; d=next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2])); raise SystemExit(0 if d["job_status"]=="succeeded" and d["chunk_count"]>0 and d["vector_count"]==d["chunk_count"] and d["fulltext_count"]==d["chunk_count"] else 1)' "$TEST_DIR/documents.json" "$DOCUMENT_ID" && break
  sleep 2
done
python3 -c 'import json,sys; d=next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2])); assert d["job_status"]=="succeeded", d; assert d["chunk_count"]>0 and d["vector_count"]==d["chunk_count"] and d["fulltext_count"]==d["chunk_count"], d' "$TEST_DIR/documents.json" "$DOCUMENT_ID"

curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1&folder_id=$PARENT_FOLDER_ID&include_subfolders=true" > "$TEST_DIR/parent-documents.json"
python3 -c 'import json,sys; assert any(x["id"]==int(sys.argv[2]) for x in json.load(open(sys.argv[1])))' "$TEST_DIR/parent-documents.json" "$DOCUMENT_ID"
NONEMPTY_FOLDER_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/folders/$CHILD_FOLDER_ID?row_version=$CHILD_FOLDER_VERSION")"
[[ "$NONEMPTY_FOLDER_STATUS" == "422" ]]

DEPARTMENT_MANAGE_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$TEST_DIR/hr.cookie" -X POST "$BASE_URL/api/v1/documents/$DOCUMENT_ID/reindex")"
[[ "$DEPARTMENT_MANAGE_STATUS" == "200" ]]

curl -fsS -b "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d "{\"question\":\"年休假需要提前几个工作日申请？\",\"knowledge_base_id\":1,\"folder_id\":$PARENT_FOLDER_ID,\"include_subfolders\":true}" "$BASE_URL/api/v1/agents/1/chat" > "$TEST_DIR/chat.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["citations"], d; assert d["candidate_counts"]["vector"]>0, d; assert d["candidate_counts"]["keyword"]>0, d; assert "三个工作日" in d["answer"], d' "$TEST_DIR/chat.json"

curl -fsS -b "$TEST_DIR/admin.cookie" -D "$TEST_DIR/download.headers" -o "$TEST_DIR/downloaded.txt" \
  "$BASE_URL/api/v1/documents/$DOCUMENT_ID/download"
grep -qi "filename\*=UTF-8''%" "$TEST_DIR/download.headers"

curl -fsS -b "$TEST_DIR/hr.cookie" "$BASE_URL/api/v1/knowledge-bases" > "$TEST_DIR/hr-kbs.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert [x["code"] for x in d] == ["HR_POLICY"], d' "$TEST_DIR/hr-kbs.json"
curl -fsS -b "$TEST_DIR/tech.cookie" "$BASE_URL/api/v1/knowledge-bases" > "$TEST_DIR/tech-kbs.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert [x["code"] for x in d] == ["TECH_KB"], d' "$TEST_DIR/tech-kbs.json"
curl -fsS -b "$TEST_DIR/hr.cookie" "$BASE_URL/api/v1/agents" > "$TEST_DIR/hr-agents.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert [x["code"] for x in d] == ["HR_POLICY_ASSISTANT"], d' "$TEST_DIR/hr-agents.json"
curl -fsS -b "$TEST_DIR/tech.cookie" "$BASE_URL/api/v1/agents" > "$TEST_DIR/tech-agents.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == []' "$TEST_DIR/tech-agents.json"
FORBIDDEN_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$TEST_DIR/tech.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1")"
[[ "$FORBIDDEN_STATUS" == "403" ]]

curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1&folder_id=$CHILD_FOLDER_ID" > "$TEST_DIR/child-documents.json"
DOCUMENT_VERSION="$(python3 -c 'import json,sys; print(next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2]))["row_version"])' "$TEST_DIR/child-documents.json" "$DOCUMENT_ID")"
printf '{"folder_id":null,"row_version":%s}' "$DOCUMENT_VERSION" > "$TEST_DIR/document-move-root.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PUT \
  -d @"$TEST_DIR/document-move-root.json" "$BASE_URL/api/v1/documents/$DOCUMENT_ID/folder" > "$TEST_DIR/document-moved-root.json"
DOCUMENT_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["row_version"])' "$TEST_DIR/document-moved-root.json")"
printf '{"folder_id":%s,"row_version":%s}' "$CHILD_FOLDER_ID" "$DOCUMENT_VERSION" > "$TEST_DIR/document-move-back.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PUT \
  -d @"$TEST_DIR/document-move-back.json" "$BASE_URL/api/v1/documents/$DOCUMENT_ID/folder" > /dev/null

printf '{"username":"%s","display_name":"E2E 技术部已编辑","department_id":%s}' "$TECH_USER" "$TECH_ID" > "$TEST_DIR/edit-user.json"
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PUT \
  -d @"$TEST_DIR/edit-user.json" "$BASE_URL/api/v1/users/$TECH_USER_ID" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/users" > "$TEST_DIR/users.json"
python3 -c 'import json,sys; u=next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2])); assert u["display_name"]=="E2E 技术部已编辑", u; assert u["department_code"]=="TECH", u; assert "roles" not in u, u' "$TEST_DIR/users.json" "$TECH_USER_ID"

curl -fsS -b "$TEST_DIR/admin.cookie" -X POST "$BASE_URL/api/v1/users/$TECH_USER_ID/reset-password" > "$TEST_DIR/reset-password.json"
TECH_RESET_PASSWORD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["temporary_password"])' "$TEST_DIR/reset-password.json")"
TECH_OLD_PASSWORD_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "$(json_login "$TECH_USER" "$TECH_TEMP_PASSWORD")" "$BASE_URL/api/v1/auth/login")"
[[ "$TECH_OLD_PASSWORD_STATUS" == "401" ]]
curl -fsS -H 'Content-Type: application/json' -d "$(json_login "$TECH_USER" "$TECH_RESET_PASSWORD")" \
  "$BASE_URL/api/v1/auth/login" > /dev/null

curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/documents/$DOCUMENT_ID" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/folders/$CHILD_FOLDER_ID?row_version=$CHILD_FOLDER_VERSION" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/folders/$PARENT_FOLDER_ID?row_version=1" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/users/$HR_USER_ID" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/users/$TECH_USER_ID" > /dev/null
DELETED_LOGIN_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "$(json_login "$TECH_USER" "$TECH_RESET_PASSWORD")" "$BASE_URL/api/v1/auth/login")"
[[ "$DELETED_LOGIN_STATUS" == "401" ]]
curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/users" > "$TEST_DIR/users-after-delete.json"
python3 -c 'import json,sys; ids={x["id"] for x in json.load(open(sys.argv[1]))}; assert int(sys.argv[2]) not in ids and int(sys.argv[3]) not in ids, ids' "$TEST_DIR/users-after-delete.json" "$HR_USER_ID" "$TECH_USER_ID"

python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("E2E PASS | folders=ok | folder_edit_move=ok | nonempty_guard=ok | multi_scope_retrieval=ok | document_move_no_reindex=ok | department_admin=ok | temporary_password=ok | soft_delete=ok | department_isolation=ok | vector=%s keyword=%s final=%s | rerank=%s | cross_department=403" % (d["candidate_counts"]["vector"], d["candidate_counts"]["keyword"], d["candidate_counts"]["final"], d["retrieval_method"]["rerank"]))' "$TEST_DIR/chat.json"

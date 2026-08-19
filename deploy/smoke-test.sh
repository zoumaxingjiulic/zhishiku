#!/usr/bin/env bash
set -euo pipefail

BASE_URL="http://127.0.0.1:18000"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEST_DIR"' EXIT

cd "$PROJECT_DIR"
for _ in $(seq 1 30); do
  curl -fsS "$BASE_URL/readyz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$BASE_URL/readyz" >/dev/null
if [[ ! -f .initial-admin-password ]]; then
  echo "缺少 .initial-admin-password，无法自动执行管理员登录验收" >&2
  exit 1
fi
ADMIN_PASSWORD="$(<.initial-admin-password)"
STAMP="$(date +%s)"
HR_USER="e2e_hr_${STAMP}"
OTHER_USER="e2e_other_${STAMP}"
TEST_PASSWORD="Test!Aa${STAMP}"

json_login() { printf '{"username":"%s","password":"%s"}' "$1" "$2"; }
curl -fsS -c "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login admin "$ADMIN_PASSWORD")" "$BASE_URL/api/v1/auth/login" > "$TEST_DIR/admin.json"

curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/departments" > "$TEST_DIR/departments.json"
HR_ID="$(python3 -c 'import json,sys; print(next(x["id"] for x in json.load(open(sys.argv[1])) if x["code"]=="HR"))' "$TEST_DIR/departments.json")"

create_user() {
  local username="$1" department_id="$2" output="$3"
  printf '{"username":"%s","display_name":"E2E %s","password":"%s","department_ids":[%s],"roles":["employee"]}' \
    "$username" "$username" "$TEST_PASSWORD" "$department_id" > "$TEST_DIR/user-payload.json"
  curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' \
    -d @"$TEST_DIR/user-payload.json" "$BASE_URL/api/v1/users" > "$output"
}
create_user "$HR_USER" "$HR_ID" "$TEST_DIR/hr-user.json"
create_user "$OTHER_USER" 1 "$TEST_DIR/other-user.json"
HR_USER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/hr-user.json")"
OTHER_USER_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TEST_DIR/other-user.json")"

curl -fsS -c "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$HR_USER" "$TEST_PASSWORD")" "$BASE_URL/api/v1/auth/login" > /dev/null
curl -fsS -c "$TEST_DIR/other.cookie" -H 'Content-Type: application/json' \
  -d "$(json_login "$OTHER_USER" "$TEST_PASSWORD")" "$BASE_URL/api/v1/auth/login" > /dev/null

printf '人力资源部休假制度\n员工连续工作满一年后享有带薪年休假。申请年休假须提前三个工作日在系统提交，由直属主管审批。紧急情况应联系人力资源部补充备案。\n' > "$TEST_DIR/hr-policy.txt"
curl -fsS -b "$TEST_DIR/admin.cookie" -F knowledge_base_id=1 -F title="E2E休假制度${STAMP}" \
  -F security_level=internal -F "file=@$TEST_DIR/hr-policy.txt;type=text/plain" \
  "$BASE_URL/api/v1/documents" > "$TEST_DIR/upload.json"
DOCUMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_id"])' "$TEST_DIR/upload.json")"

for _ in $(seq 1 60); do
  curl -fsS -b "$TEST_DIR/admin.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1" > "$TEST_DIR/documents.json"
  python3 -c 'import json,sys; d=next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2])); raise SystemExit(0 if d["job_status"]=="succeeded" and d["chunk_count"]>0 and d["vector_count"]==d["chunk_count"] and d["fulltext_count"]==d["chunk_count"] else 1)' "$TEST_DIR/documents.json" "$DOCUMENT_ID" && break
  sleep 2
done
python3 -c 'import json,sys; d=next(x for x in json.load(open(sys.argv[1])) if x["id"]==int(sys.argv[2])); assert d["job_status"]=="succeeded", d; assert d["chunk_count"]>0 and d["vector_count"]==d["chunk_count"] and d["fulltext_count"]==d["chunk_count"], d' "$TEST_DIR/documents.json" "$DOCUMENT_ID"

EMPLOYEE_MANAGE_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$TEST_DIR/hr.cookie" -X POST "$BASE_URL/api/v1/documents/$DOCUMENT_ID/reindex")"
[[ "$EMPLOYEE_MANAGE_STATUS" == "403" ]]

curl -fsS -b "$TEST_DIR/hr.cookie" -H 'Content-Type: application/json' \
  -d '{"question":"年休假需要提前几个工作日申请？"}' "$BASE_URL/api/v1/agents/1/chat" > "$TEST_DIR/chat.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["citations"], d; assert d["candidate_counts"]["vector"]>0, d; assert d["candidate_counts"]["keyword"]>0, d; assert "三个工作日" in d["answer"], d' "$TEST_DIR/chat.json"

curl -fsS -b "$TEST_DIR/other.cookie" "$BASE_URL/api/v1/knowledge-bases" > "$TEST_DIR/other-kbs.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == []' "$TEST_DIR/other-kbs.json"
FORBIDDEN_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$TEST_DIR/other.cookie" "$BASE_URL/api/v1/documents?knowledge_base_id=1")"
[[ "$FORBIDDEN_STATUS" == "403" ]]

curl -fsS -b "$TEST_DIR/admin.cookie" -X DELETE "$BASE_URL/api/v1/documents/$DOCUMENT_ID" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PATCH -d '{"status":0}' "$BASE_URL/api/v1/users/$HR_USER_ID/status" > /dev/null
curl -fsS -b "$TEST_DIR/admin.cookie" -H 'Content-Type: application/json' -X PATCH -d '{"status":0}' "$BASE_URL/api/v1/users/$OTHER_USER_ID/status" > /dev/null

python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("E2E PASS | vector=%s keyword=%s final=%s | rerank=%s | employee_manage=403 | cross_department=403" % (d["candidate_counts"]["vector"], d["candidate_counts"]["keyword"], d["candidate_counts"]["final"], d["retrieval_method"]["rerank"]))' "$TEST_DIR/chat.json"

#!/usr/bin/env bash

set -Eeuo pipefail

# 恢复固定 zhangsan / E10001 本地演示状态；不访问或修改用户、外部 fixture、migration 和 sequence。

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${RESET_COMPOSE_FILE:-${SCRIPT_DIR}/docker-compose.local.yml}"
COMPOSE_ENV_FILE="${RESET_COMPOSE_ENV_FILE:-}"

TARGET_USER_ID="U10001"
TARGET_USERNAME="zhangsan"
TARGET_EMPLOYEE_ID="E10001"
TARGET_ANNUAL_BALANCE="10.0"
CHECKPOINT_DB="${LANGGRAPH_CHECKPOINT_DB:-enterprise_ai_runtime}"

AUTO_CONFIRM=0
DRY_RUN=0

usage() {
    cat <<'EOF'
用法：
  bash deploy/reset-demo-state.sh [--yes]
  bash deploy/reset-demo-state.sh --dry-run

说明：
  默认会在执行前要求输入 RESET；--yes 仅用于已确认目标的非交互执行。
  --dry-run 只执行 SELECT 和严格只读 SQLite 检查，不修改任何数据。
  RESET_COMPOSE_FILE 可用于显式指定 Compose 文件；默认使用 docker-compose.local.yml。
  RESET_COMPOSE_ENV_FILE 可选用于显式指定 Compose env 文件，不设置时不传入 --env-file。
  LANGGRAPH_CHECKPOINT_DB 可用于指定本地 checkpoint 数据库名，默认 enterprise_ai_runtime。
  演示年假余额固定恢复为 10.0，不接受运行时覆盖。

生产 dry-run 示例：
  COMPOSE_PROJECT_NAME=enterprise-ai-copilot \
  RESET_COMPOSE_FILE=/opt/enterprise-ai-copilot/deploy/docker-compose.prod.yml \
  RESET_COMPOSE_ENV_FILE=/opt/enterprise-ai-copilot/deploy/.env \
  LANGGRAPH_CHECKPOINT_DB=enterprise_ai_copilot \
  bash /opt/enterprise-ai-copilot/deploy/reset-demo-state.sh --dry-run
EOF
}

fail() {
    echo "错误：$*" >&2
    exit 1
}

info() {
    echo "[reset-demo-state] $*"
}

while (($# > 0)); do
    case "$1" in
        --yes)
            AUTO_CONFIRM=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "未知参数：$1"
            ;;
    esac
    shift
done

[[ -f "$COMPOSE_FILE" ]] || fail "找不到 Compose 文件：$COMPOSE_FILE"
if [[ -n "$COMPOSE_ENV_FILE" ]]; then
    [[ -f "$COMPOSE_ENV_FILE" ]] || fail "找不到 Compose env 文件：$COMPOSE_ENV_FILE"
fi
[[ "$CHECKPOINT_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "checkpoint 数据库名无效：$CHECKPOINT_DB"
command -v docker >/dev/null 2>&1 || fail "需要 docker 命令"

assert_target_constants() {
    [[ -n "$TARGET_USER_ID" && -n "$TARGET_USERNAME" && -n "$TARGET_EMPLOYEE_ID" ]] || \
        fail "目标身份变量不能为空"
    [[ "$TARGET_USER_ID" == "U10001" ]] || fail "USER_ID 不是固定 Demo 身份 U10001"
    [[ "$TARGET_USERNAME" == "zhangsan" ]] || fail "USERNAME 不是固定 Demo 身份 zhangsan"
    [[ "$TARGET_EMPLOYEE_ID" == "E10001" ]] || fail "EMPLOYEE_ID 不是固定 Demo 身份 E10001"
    [[ "$TARGET_ANNUAL_BALANCE" == "10.0" ]] || fail "年假余额基线不是固定值 10.0"
}

assert_target_constants

compose() {
    if [[ -n "$COMPOSE_ENV_FILE" ]]; then
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

business_psql() {
    compose exec -T postgres sh -lc \
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF "|"'
}

checkpoint_psql() {
    compose exec -T postgres sh -lc \
        "psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d '$CHECKPOINT_DB' -AtF \"|\""
}

mock_oa_python() {
    compose exec -T mock-oa python "$@"
}

sha256_stream() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 | awk '{print $NF}'
    else
        fail "需要 sha256sum 或 openssl 来定位 LangGraph checkpoint thread"
    fi
}

runtime_thread_id() {
    local conversation_id="$1"
    local task_id="${2-}"
    local digest

    if [[ -n "$task_id" ]]; then
        digest="$(printf '%s\0%s\0%s\0%s' \
            'enterprise-ai-copilot:agent-runtime:v1' \
            "$TARGET_USER_ID" "$conversation_id" "$task_id" | sha256_stream)"
    else
        digest="$(printf '%s\0%s\0%s' \
            'enterprise-ai-copilot:agent-runtime:v1' \
            "$TARGET_USER_ID" "$conversation_id" | sha256_stream)"
    fi
    printf 'rt_%s' "$digest"
}

declare -a TARGET_BASE_THREADS=()
declare -a TARGET_CONVERSATIONS=()
declare -a TARGET_THREADS=()
declare -A SEEN_CONVERSATIONS=()
declare -A SEEN_BASE_THREADS=()
declare -A SEEN_THREADS=()

add_target_threads() {
    local kind="$1"
    local conversation_id="$2"
    local task_id="${3-}"
    local base_id planner_id deterministic_id

    case "$kind" in
        BASE)
            ;;
        TASK)
            [[ -n "$task_id" ]] || fail "Task runtime context 缺少 task_id"
            ;;
        *)
            fail "未知 runtime context 类型：$kind"
            ;;
    esac
    [[ -n "$conversation_id" ]] || fail "runtime context 缺少 conversation_id"
    [[ "$conversation_id" =~ ^[A-Za-z0-9_.:-]+$ ]] || \
        fail "conversation_id 含有不支持的字符，已停止：$conversation_id"
    if [[ -z "${SEEN_CONVERSATIONS[$conversation_id]+x}" ]]; then
        SEEN_CONVERSATIONS["$conversation_id"]=1
        TARGET_CONVERSATIONS+=("$conversation_id")
    fi
    if [[ -n "$task_id" ]]; then
        [[ "$task_id" =~ ^[A-Za-z0-9_.:-]+$ ]] || \
            fail "task_id 含有不支持的字符，已停止：$task_id"
    fi

    if [[ "$kind" == "TASK" ]]; then
        base_id="$(runtime_thread_id "$conversation_id" "$task_id")"
    else
        base_id="$(runtime_thread_id "$conversation_id")"
    fi
    [[ "$base_id" =~ ^rt_[0-9a-f]{64}$ ]] || fail "推导出的 runtime thread 基础 ID 无效"

    if [[ -z "${SEEN_BASE_THREADS[$base_id]+x}" ]]; then
        SEEN_BASE_THREADS["$base_id"]=1
        TARGET_BASE_THREADS+=("$base_id")
    fi

    planner_id="${base_id}:planner-v1"
    deterministic_id="${base_id}:deterministic-v1"
    for thread_id in "$planner_id" "$deterministic_id"; do
        if [[ -z "${SEEN_THREADS[$thread_id]+x}" ]]; then
            SEEN_THREADS["$thread_id"]=1
            TARGET_THREADS+=("$thread_id")
        fi
    done
}

validate_id_list() {
    local label="$1"
    local values="$2"
    local value
    while IFS= read -r value; do
        [[ -z "$value" ]] && continue
        [[ "$value" =~ ^[A-Za-z0-9_.:-]+$ ]] || fail "$label 含有不安全 ID，已停止"
    done <<< "$values"
}

sql_literal_list() {
    local values="$1"
    local value
    local result=""
    while IFS= read -r value; do
        [[ -z "$value" ]] && continue
        if [[ -n "$result" ]]; then
            result+=", "
        fi
        result+="'$value'"
    done <<< "$values"
    if [[ -n "$result" ]]; then
        printf '%s' "$result"
    else
        printf 'NULL'
    fi
}

count_nonblank_lines() {
    local values="$1"
    if [[ -z "$values" ]]; then
        printf '0'
    else
        printf '%s\n' "$values" | sed '/^$/d' | wc -l | tr -d ' '
    fi
}

build_thread_values() {
    local result=""
    local thread_id
    for thread_id in "${TARGET_THREADS[@]}"; do
        [[ "$thread_id" =~ ^rt_[0-9a-f]{64}:(planner-v1|deterministic-v1)$ ]] || \
            fail "checkpoint thread ID 格式异常"
        if [[ -n "$result" ]]; then
            result+=","
        fi
        result+="('$thread_id')"
    done
    printf '%s' "$result"
}

checkpoint_counts() {
    if ((${#TARGET_THREADS[@]} == 0)); then
        printf 'checkpoints|0\ncheckpoint_blobs|0\ncheckpoint_writes|0\n'
        return 0
    fi
    local thread_values
    thread_values="$(build_thread_values)"
    checkpoint_psql <<SQL
WITH targets(thread_id) AS (VALUES $thread_values)
SELECT 'checkpoints|' || count(*)
FROM checkpoints stored
JOIN targets ON targets.thread_id = stored.thread_id;
WITH targets(thread_id) AS (VALUES $thread_values)
SELECT 'checkpoint_blobs|' || count(*)
FROM checkpoint_blobs stored
JOIN targets ON targets.thread_id = stored.thread_id;
WITH targets(thread_id) AS (VALUES $thread_values)
SELECT 'checkpoint_writes|' || count(*)
FROM checkpoint_writes stored
JOIN targets ON targets.thread_id = stored.thread_id;
SQL
}

mock_oa_readonly() {
    local request_ids="${1-}"
    printf '%s\n' "$request_ids" | mock_oa_python -c '
import os
import sqlite3
import sys
from pathlib import Path

path = os.environ.get("MOCK_OA_DB_PATH", "/data/mock-oa.sqlite3")
if not os.path.isfile(path):
    raise SystemExit(f"Mock OA SQLite DB 不存在：{path}")
uri = Path(path).resolve().as_uri() + "?mode=ro"
connection = sqlite3.connect(uri, uri=True)
try:
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = \"table\" ORDER BY name"
    ).fetchall()
    tables = ",".join(row[0] for row in table_rows)
    if "expense_approval" not in {row[0] for row in table_rows}:
        raise SystemExit("Mock OA SQLite 缺少 expense_approval 表")
    column_rows = connection.execute("PRAGMA table_info(expense_approval)").fetchall()
    columns = ",".join(row[1] for row in column_rows)
    required = {"request_id", "payload_json", "status"}
    missing_columns = sorted(required - {row[1] for row in column_rows})
    if missing_columns:
        raise SystemExit("Mock OA expense_approval 缺少字段：" + ",".join(missing_columns))
    request_ids = [line.strip() for line in sys.stdin if line.strip()]
    if request_ids:
        placeholders = ",".join("?" for _ in request_ids)
        rows = connection.execute(
            f"SELECT request_id FROM expense_approval WHERE request_id IN ({placeholders}) ORDER BY request_id",
            request_ids,
        ).fetchall()
        matching = [row[0] for row in rows]
    else:
        matching = []
    missing = sorted(set(request_ids) - set(matching))
    print(f"tables={tables}")
    print(f"columns={columns}")
    print(f"matching_request_count={len(matching)}")
    print("matching_request_ids=" + ",".join(matching))
    print("missing_request_ids=" + ",".join(missing))
finally:
    connection.close()
'
}

mock_oa_delete() {
    local request_ids="$1"
    if [[ -z "$request_ids" ]]; then
        info "[MOCK-OA] 没有 target external_request_id，跳过 SQLite mutation"
        return 0
    fi
    printf '%s\n' "$request_ids" | mock_oa_python -c '
import os
import sqlite3
import sys

path = os.environ.get("MOCK_OA_DB_PATH", "/data/mock-oa.sqlite3")
request_ids = [line.strip() for line in sys.stdin if line.strip()]
if not request_ids:
    print("MockOaApprovalsDeleted|0")
    raise SystemExit(0)
connection = sqlite3.connect(path, timeout=30)
try:
    connection.execute("BEGIN IMMEDIATE")
    placeholders = ",".join("?" for _ in request_ids)
    cursor = connection.execute(
        f"DELETE FROM expense_approval WHERE request_id IN ({placeholders})",
        request_ids,
    )
    deleted = cursor.rowcount
    connection.commit()
    print(f"MockOaApprovalsDeleted|{deleted}")
except Exception:
    connection.rollback()
    raise
finally:
    connection.close()
'
}

compose config --quiet || fail "Local Compose 配置检查失败"

info "[PRECHECK] 检查 PostgreSQL 与 Mock OA 容器"
business_psql <<'SQL' >/dev/null
SELECT 1;
SQL

missing_business_tables="$(business_psql <<'SQL'
WITH required(table_name) AS (
    VALUES
        ('app_user'), ('leave_account'), ('business_action'), ('leave_request'),
        ('ai_task_memory'), ('expense_claim'), ('expense_item'), ('purchase_request'),
        ('task_execution')
)
SELECT COALESCE(string_agg(required.table_name, ',' ORDER BY required.table_name), '')
FROM required
WHERE NOT EXISTS (
    SELECT 1
    FROM information_schema.tables actual
    WHERE actual.table_schema = 'public'
      AND actual.table_name = required.table_name
);
SQL
)"
[[ -z "$missing_business_tables" ]] || fail "Java 业务库缺少表：$missing_business_tables"

missing_business_columns="$(business_psql <<'SQL'
WITH required(table_name, column_name) AS (
    VALUES
        ('expense_claim', 'external_provider'),
        ('expense_claim', 'external_request_id')
)
SELECT COALESCE(string_agg(required.table_name || '.' || required.column_name, ','
                            ORDER BY required.table_name, required.column_name), '')
FROM required
WHERE NOT EXISTS (
    SELECT 1
    FROM information_schema.columns actual
    WHERE actual.table_schema = 'public'
      AND actual.table_name = required.table_name
      AND actual.column_name = required.column_name
);
SQL
)"
[[ -z "$missing_business_columns" ]] || fail "业务库缺少 external approval correlation 字段：$missing_business_columns"

missing_checkpoint_tables="$(checkpoint_psql <<'SQL'
WITH required(table_name) AS (
    VALUES ('checkpoints'), ('checkpoint_blobs'), ('checkpoint_writes'),
           ('checkpoint_migrations')
)
SELECT COALESCE(string_agg(required.table_name, ',' ORDER BY required.table_name), '')
FROM required
WHERE NOT EXISTS (
    SELECT 1
    FROM information_schema.tables actual
    WHERE actual.table_schema = 'public'
      AND actual.table_name = required.table_name
);
SQL
)"
[[ -z "$missing_checkpoint_tables" ]] || fail "Checkpoint 库缺少表：$missing_checkpoint_tables"

identity_count="$(business_psql <<SQL
SELECT count(*)
FROM app_user
WHERE user_id = '$TARGET_USER_ID'
  AND username = '$TARGET_USERNAME'
  AND employee_id = '$TARGET_EMPLOYEE_ID';
SQL
)"
[[ "$identity_count" == "1" ]] || fail "目标身份映射不是唯一的 U10001/zhangsan/E10001"

balance_count="$(business_psql <<SQL
SELECT count(*)
FROM leave_account
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SQL
)"
[[ "$balance_count" == "1" ]] || fail "目标 LeaveBalance 不是唯一的 E10001 记录"

USER_FIXTURES_BEFORE="$(business_psql <<'SQL'
SELECT user_id || '|' || username || '|' || COALESCE(employee_id, '') || '|' || role || '|' || enabled
FROM app_user
ORDER BY user_id;
SQL
)"

TARGET_ACTION_IDS="$(business_psql <<SQL
SELECT action_id
FROM business_action
WHERE owner_user_id = '$TARGET_USER_ID'
  AND employee_id = '$TARGET_EMPLOYEE_ID'
ORDER BY action_id;
SQL
)"
TARGET_PURCHASE_IDS="$(business_psql <<SQL
SELECT purchase.request_id
FROM purchase_request purchase
JOIN business_action action ON action.action_id = purchase.source_action_id
WHERE purchase.owner_user_id = '$TARGET_USER_ID'
  AND purchase.employee_id = '$TARGET_EMPLOYEE_ID'
  AND action.owner_user_id = '$TARGET_USER_ID'
  AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  AND action.action_type = 'PURCHASE_REQUEST'
ORDER BY purchase.request_id;
SQL
)"
TARGET_TASK_IDS="$(business_psql <<SQL
SELECT task_id
FROM task_execution
WHERE owner_user_id = '$TARGET_USER_ID'
ORDER BY task_id;
SQL
)"
TARGET_EXPENSE_IDS="$(business_psql <<SQL
SELECT expense_id
FROM expense_claim
WHERE employee_id = '$TARGET_EMPLOYEE_ID'
ORDER BY expense_id;
SQL
)"
validate_id_list "target action ids" "$TARGET_ACTION_IDS"
validate_id_list "target purchase request ids" "$TARGET_PURCHASE_IDS"
validate_id_list "target task ids" "$TARGET_TASK_IDS"
validate_id_list "target expense ids" "$TARGET_EXPENSE_IDS"

info "[PRECHECK] BUSINESS_SCOPE_FAIL_CLOSED = PASS（目标集合已按 owner AND employee 构造）"

CONSISTENCY_CHECKS="$(business_psql <<SQL
SELECT 'U10001_action_non_E10001|' || count(*)
FROM business_action
WHERE owner_user_id = '$TARGET_USER_ID'
  AND employee_id IS DISTINCT FROM '$TARGET_EMPLOYEE_ID';
SELECT 'E10001_action_non_U10001|' || count(*)
FROM business_action
WHERE employee_id = '$TARGET_EMPLOYEE_ID'
  AND owner_user_id IS DISTINCT FROM '$TARGET_USER_ID';
SELECT 'target_task_action_not_target|' || count(*)
FROM task_execution task
WHERE task.owner_user_id = '$TARGET_USER_ID'
  AND task.action_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = task.action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'other_owner_task_via_target_action|' || count(*)
FROM task_execution task
WHERE task.owner_user_id IS DISTINCT FROM '$TARGET_USER_ID'
  AND task.action_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = task.action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'target_leave_action_not_target|' || count(*)
FROM leave_request leave_row
WHERE leave_row.employee_id = '$TARGET_EMPLOYEE_ID'
  AND leave_row.source_action_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = leave_row.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'other_employee_leave_via_target_action|' || count(*)
FROM leave_request leave_row
WHERE leave_row.employee_id IS DISTINCT FROM '$TARGET_EMPLOYEE_ID'
  AND leave_row.source_action_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = leave_row.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'target_expense_action_not_target|' || count(*)
FROM expense_claim expense
WHERE expense.employee_id = '$TARGET_EMPLOYEE_ID'
  AND expense.source_action_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = expense.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'other_employee_expense_via_target_action|' || count(*)
FROM expense_claim expense
WHERE expense.employee_id IS DISTINCT FROM '$TARGET_EMPLOYEE_ID'
  AND expense.source_action_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = expense.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'target_purchase_owner_non_E10001|' || count(*)
FROM purchase_request purchase
WHERE purchase.owner_user_id = '$TARGET_USER_ID'
  AND purchase.employee_id IS DISTINCT FROM '$TARGET_EMPLOYEE_ID';
SELECT 'target_purchase_employee_non_U10001|' || count(*)
FROM purchase_request purchase
WHERE purchase.employee_id = '$TARGET_EMPLOYEE_ID'
  AND purchase.owner_user_id IS DISTINCT FROM '$TARGET_USER_ID';
SELECT 'target_purchase_action_not_target|' || count(*)
FROM purchase_request purchase
WHERE purchase.owner_user_id = '$TARGET_USER_ID'
  AND purchase.employee_id = '$TARGET_EMPLOYEE_ID'
  AND NOT EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = purchase.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
        AND action.action_type = 'PURCHASE_REQUEST'
  );
SELECT 'other_identity_purchase_via_target_action|' || count(*)
FROM purchase_request purchase
WHERE (purchase.owner_user_id IS DISTINCT FROM '$TARGET_USER_ID'
       OR purchase.employee_id IS DISTINCT FROM '$TARGET_EMPLOYEE_ID')
  AND EXISTS (
      SELECT 1
      FROM business_action action
      WHERE action.action_id = purchase.source_action_id
        AND action.owner_user_id = '$TARGET_USER_ID'
        AND action.employee_id = '$TARGET_EMPLOYEE_ID'
  );
SELECT 'target_mock_oa_missing_request_id|' || count(*)
FROM expense_claim expense
WHERE expense.employee_id = '$TARGET_EMPLOYEE_ID'
  AND expense.external_provider = 'MOCK_OA'
  AND NULLIF(btrim(expense.external_request_id), '') IS NULL;
SQL
)"
consistency_failed=0
while IFS='|' read -r check_name check_count; do
    [[ -z "${check_name:-}" ]] && continue
    info "[PRECHECK] $check_name=$check_count"
    if [[ "$check_count" != "0" ]]; then
        consistency_failed=1
    fi
done <<< "$CONSISTENCY_CHECKS"
((consistency_failed == 0)) || fail "一致性检查失败，未执行任何 mutation；请先人工修复异常数据"
info "[PRECHECK] CROSS_IDENTITY_PRECHECK = 0"

TARGET_MOCK_OA_REQUEST_IDS="$(business_psql <<SQL
SELECT external_request_id
FROM expense_claim
WHERE employee_id = '$TARGET_EMPLOYEE_ID'
  AND external_provider = 'MOCK_OA'
  AND NULLIF(btrim(external_request_id), '') IS NOT NULL
ORDER BY external_request_id;
SQL
)"
validate_id_list "target Mock OA request ids" "$TARGET_MOCK_OA_REQUEST_IDS"

RUNTIME_CONTEXTS="$(business_psql <<SQL
SELECT DISTINCT 'BASE|' || conversation_id || '|'
FROM ai_task_memory
WHERE user_id = '$TARGET_USER_ID'
  AND conversation_id IS NOT NULL
UNION
SELECT DISTINCT 'BASE|' || conversation_id || '|'
FROM business_action
WHERE owner_user_id = '$TARGET_USER_ID'
  AND employee_id = '$TARGET_EMPLOYEE_ID'
  AND conversation_id IS NOT NULL
UNION
SELECT DISTINCT 'TASK|' || conversation_id || '|' || task_id
FROM task_execution
WHERE owner_user_id = '$TARGET_USER_ID'
  AND conversation_id IS NOT NULL
ORDER BY 1;
SQL
)"
while IFS='|' read -r context_kind conversation_id task_id; do
    [[ -z "${context_kind:-}" ]] && continue
    info "[CHECKPOINT] runtime_context=$context_kind|$conversation_id|$task_id"
    add_target_threads "$context_kind" "$conversation_id" "$task_id"
done <<< "$RUNTIME_CONTEXTS"

TARGET_TASKS_WITHOUT_CONVERSATION="$(business_psql <<SQL
SELECT count(*)
FROM task_execution
WHERE owner_user_id = '$TARGET_USER_ID'
  AND conversation_id IS NULL;
SQL
)"
[[ "$TARGET_TASKS_WITHOUT_CONVERSATION" == "0" ]] || \
    fail "目标 TaskExecution 存在无法推导 runtime context 的记录，已停止"

GRAPH_RUNTIME_RECORD_COUNT="$(business_psql <<SQL
SELECT
    (SELECT count(*) FROM task_execution
     WHERE owner_user_id = '$TARGET_USER_ID')
  + (SELECT count(*) FROM business_action
     WHERE owner_user_id = '$TARGET_USER_ID'
       AND employee_id = '$TARGET_EMPLOYEE_ID'
       AND conversation_id IS NOT NULL)
  + (SELECT count(*) FROM ai_task_memory
     WHERE user_id = '$TARGET_USER_ID'
       AND conversation_id IS NOT NULL);
SQL
)"
if [[ "$GRAPH_RUNTIME_RECORD_COUNT" != "0" && ${#TARGET_THREADS[@]} == 0 ]]; then
    fail "存在目标 Graph runtime 数据但没有任何 checkpoint thread，已停止"
fi

info "[CHECKPOINT] CHECKPOINT_SCOPE_EXACT = YES（SHA-256 + exact thread_id）"
info "[CHECKPOINT] CHECKPOINT_EMPTY_SCOPE_FAIL_CLOSED = PASS"
info "[CHECKPOINT] target conversation ids：${#TARGET_CONVERSATIONS[@]}"
for conversation_id in "${TARGET_CONVERSATIONS[@]}"; do
    info "[CHECKPOINT] target_conversation_id=$conversation_id"
done
info "[CHECKPOINT] derived base thread ids：${#TARGET_BASE_THREADS[@]}"
for base_id in "${TARGET_BASE_THREADS[@]}"; do
    info "[CHECKPOINT] base_thread_id=$base_id"
done
info "[CHECKPOINT] planner-v1 / deterministic-v1 thread ids：${#TARGET_THREADS[@]}"
for thread_id in "${TARGET_THREADS[@]}"; do
    info "[CHECKPOINT] target_thread_id=$thread_id"
done

CHECKPOINT_HITS_BEFORE="$(checkpoint_counts)"
while IFS='|' read -r table_name table_count; do
    [[ -z "${table_name:-}" ]] && continue
    info "[CHECKPOINT] hit_$table_name=$table_count"
done <<< "$CHECKPOINT_HITS_BEFORE"

TARGET_PURCHASE_VALUES="$(sql_literal_list "$TARGET_PURCHASE_IDS")"
TARGET_EXPENSE_VALUES="$(sql_literal_list "$TARGET_EXPENSE_IDS")"
TARGET_COUNTS_BEFORE="$(business_psql <<SQL
SELECT 'TaskExecution|' || count(*)
FROM task_execution
WHERE owner_user_id = '$TARGET_USER_ID';
SELECT 'BusinessAction|' || count(*)
FROM business_action
WHERE owner_user_id = '$TARGET_USER_ID'
  AND employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'PurchaseRequest|' || count(*)
FROM purchase_request
WHERE request_id IN ($TARGET_PURCHASE_VALUES);
SELECT 'LeaveRequest|' || count(*)
FROM leave_request
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'ExpenseClaim|' || count(*)
FROM expense_claim
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'ExpenseItem|' || count(*)
FROM expense_item
WHERE expense_id IN ($TARGET_EXPENSE_VALUES);
SELECT 'ConversationMemory|' || count(*)
FROM ai_task_memory
WHERE user_id = '$TARGET_USER_ID';
SELECT 'LeaveBalance|' || COALESCE(min(annual_balance)::text, 'NULL')
FROM leave_account
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SQL
)"
while IFS='|' read -r name value; do
    [[ -z "${name:-}" ]] && continue
    info "[PRECHECK] 当前 $name=$value"
done <<< "$TARGET_COUNTS_BEFORE"

MOCK_OA_SNAPSHOT_BEFORE="$(mock_oa_readonly "$TARGET_MOCK_OA_REQUEST_IDS")"
MOCK_OA_TABLES_BEFORE="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_BEFORE" | awk -F= '$1 == "tables" {print $2}')"
MOCK_OA_COLUMNS_BEFORE="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_BEFORE" | awk -F= '$1 == "columns" {print $2}')"
MOCK_OA_MATCHING_BEFORE="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_BEFORE" | awk -F= '$1 == "matching_request_count" {print $2}')"
MOCK_OA_MISSING_BEFORE="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_BEFORE" | awk -F= '$1 == "missing_request_ids" {print $2}')"
[[ "$MOCK_OA_TABLES_BEFORE" == "expense_approval" ]] || \
    fail "Mock OA SQLite schema 不符合预期：$MOCK_OA_TABLES_BEFORE"
info "[MOCK-OA] MOCK_OA_SCOPE_EXACT = YES（ExpenseClaim.external_request_id → request_id）"
info "[MOCK-OA] target request ids：$(count_nonblank_lines "$TARGET_MOCK_OA_REQUEST_IDS")"
info "[MOCK-OA] exact matching approvals：$MOCK_OA_MATCHING_BEFORE"
if [[ -n "$MOCK_OA_MISSING_BEFORE" ]]; then
    info "[MOCK-OA] 已有 correlation 但当前 SQLite 无对应 row：$MOCK_OA_MISSING_BEFORE"
fi

if ((DRY_RUN)); then
    info "[VERIFY] DRY_RUN_STRICTLY_READ_ONLY = YES"
    info "[VERIFY] EXTERNAL_FIXTURE_VERIFICATION = NOT_APPLICABLE"
    info "[VERIFY] DRY-RUN：未执行 PostgreSQL/SQLite mutation。"
    exit 0
fi

if ((AUTO_CONFIRM == 0)); then
    [[ -t 0 ]] || fail "非交互执行必须显式传入 --yes"
    read -r -p "这是目标 Demo 数据清理，输入 RESET 继续：" confirmation
    [[ "$confirmation" == "RESET" ]] || fail "确认文本不匹配，已取消"
fi

info "[PRECHECK] PostgreSQL 与 SQLite 无分布式事务；按 checkpoint、Mock OA、PostgreSQL 顺序执行，任一步失败立即停止"

if ((${#TARGET_THREADS[@]} > 0)); then
    info "[CHECKPOINT] 删除目标 checkpoint（只使用 exact thread_id，保留 checkpoint_migrations）"
    {
        printf 'BEGIN;\n'
        printf 'CREATE TEMP TABLE reset_target_threads (thread_id TEXT PRIMARY KEY);\n'
        printf 'COPY reset_target_threads(thread_id) FROM STDIN;\n'
        for thread_id in "${TARGET_THREADS[@]}"; do
            printf '%s\n' "$thread_id"
        done
        printf '%s\n' '\.'
        cat <<'SQL'
WITH deleted AS (
    DELETE FROM checkpoint_writes AS stored
    USING reset_target_threads AS target
    WHERE stored.thread_id = target.thread_id
    RETURNING stored.thread_id
)
SELECT 'CheckpointWritesDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM checkpoint_blobs AS stored
    USING reset_target_threads AS target
    WHERE stored.thread_id = target.thread_id
    RETURNING stored.thread_id
)
SELECT 'CheckpointBlobsDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM checkpoints AS stored
    USING reset_target_threads AS target
    WHERE stored.thread_id = target.thread_id
    RETURNING stored.thread_id
)
SELECT 'CheckpointsDeleted|' || count(*) FROM deleted;

COMMIT;
SQL
    } | checkpoint_psql
else
    info "[CHECKPOINT] 没有目标 runtime context；已通过 Graph runtime 空范围检查，跳过 checkpoint mutation"
fi

CHECKPOINT_HITS_AFTER="$(checkpoint_counts)"
while IFS='|' read -r table_name table_count; do
    [[ -z "${table_name:-}" ]] && continue
    info "[VERIFY] checkpoint_$table_name=$table_count"
    [[ "$table_count" == "0" ]] || fail "checkpoint 目标范围仍有记录：$table_name=$table_count"
done <<< "$CHECKPOINT_HITS_AFTER"

info "[MOCK-OA] 按 exact request_id 删除目标 approval"
mock_oa_delete "$TARGET_MOCK_OA_REQUEST_IDS"
MOCK_OA_SNAPSHOT_AFTER="$(mock_oa_readonly "$TARGET_MOCK_OA_REQUEST_IDS")"
MOCK_OA_TABLES_AFTER="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_AFTER" | awk -F= '$1 == "tables" {print $2}')"
MOCK_OA_COLUMNS_AFTER="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_AFTER" | awk -F= '$1 == "columns" {print $2}')"
MOCK_OA_MATCHING_AFTER="$(printf '%s\n' "$MOCK_OA_SNAPSHOT_AFTER" | awk -F= '$1 == "matching_request_count" {print $2}')"
[[ "$MOCK_OA_TABLES_BEFORE" == "$MOCK_OA_TABLES_AFTER" ]] || fail "Mock OA SQLite 表结构发生变化"
[[ "$MOCK_OA_COLUMNS_BEFORE" == "$MOCK_OA_COLUMNS_AFTER" ]] || fail "Mock OA SQLite 字段结构发生变化"
if [[ -z "$MOCK_OA_MATCHING_AFTER" ]]; then
    MOCK_OA_MATCHING_AFTER=0
fi
[[ "$MOCK_OA_MATCHING_AFTER" == "0" ]] || fail "目标 Mock OA approval 未清零"

info "[POSTGRES] 清理业务表（单一事务，不重置 sequence）"
{
    printf 'BEGIN;\n'
    printf 'CREATE TEMP TABLE reset_target_actions (action_id VARCHAR(64) PRIMARY KEY);\n'
    printf 'COPY reset_target_actions(action_id) FROM STDIN;\n'
    if [[ -n "$TARGET_ACTION_IDS" ]]; then
        printf '%s\n' "$TARGET_ACTION_IDS"
    fi
    printf '%s\n' '\.'
    printf 'CREATE TEMP TABLE reset_target_purchases (request_id VARCHAR(64) PRIMARY KEY);\n'
    printf 'COPY reset_target_purchases(request_id) FROM STDIN;\n'
    if [[ -n "$TARGET_PURCHASE_IDS" ]]; then
        printf '%s\n' "$TARGET_PURCHASE_IDS"
    fi
    printf '%s\n' '\.'
    printf 'CREATE TEMP TABLE reset_target_expenses (expense_id VARCHAR(64) PRIMARY KEY);\n'
    printf 'COPY reset_target_expenses(expense_id) FROM STDIN;\n'
    if [[ -n "$TARGET_EXPENSE_IDS" ]]; then
        printf '%s\n' "$TARGET_EXPENSE_IDS"
    fi
    printf '%s\n' '\.'
    cat <<SQL
WITH deleted AS (
    DELETE FROM expense_item
    WHERE expense_id IN (SELECT expense_id FROM reset_target_expenses)
    RETURNING item_id
)
SELECT 'ExpenseItemDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM expense_claim
    WHERE expense_id IN (SELECT expense_id FROM reset_target_expenses)
    RETURNING expense_id
)
SELECT 'ExpenseClaimDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM leave_request
    WHERE employee_id = '$TARGET_EMPLOYEE_ID'
    RETURNING request_id
)
SELECT 'LeaveRequestDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM task_execution
    WHERE owner_user_id = '$TARGET_USER_ID'
    RETURNING task_id
)
SELECT 'TaskExecutionDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM purchase_request
    WHERE request_id IN (SELECT request_id FROM reset_target_purchases)
    RETURNING request_id
)
SELECT 'PurchaseRequestDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM business_action
    WHERE action_id IN (SELECT action_id FROM reset_target_actions)
    RETURNING action_id
)
SELECT 'BusinessActionDeleted|' || count(*) FROM deleted;

WITH deleted AS (
    DELETE FROM ai_task_memory
    WHERE user_id = '$TARGET_USER_ID'
    RETURNING user_id, conversation_id
)
SELECT 'ConversationMemoryDeleted|' || count(*) FROM deleted;

UPDATE leave_account
SET annual_balance = $TARGET_ANNUAL_BALANCE,
    updated_at = CURRENT_TIMESTAMP
WHERE employee_id = '$TARGET_EMPLOYEE_ID';

SELECT 'LeaveBalanceRestored|' || annual_balance
FROM leave_account
WHERE employee_id = '$TARGET_EMPLOYEE_ID';

COMMIT;
SQL
} | business_psql

info "[VERIFY] 校验业务目标集合"
TARGET_COUNTS_AFTER="$(business_psql <<SQL
SELECT 'TaskExecution|' || count(*)
FROM task_execution
WHERE owner_user_id = '$TARGET_USER_ID';
SELECT 'BusinessAction|' || count(*)
FROM business_action
WHERE owner_user_id = '$TARGET_USER_ID'
  AND employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'PurchaseRequest|' || count(*)
FROM purchase_request
WHERE request_id IN ($TARGET_PURCHASE_VALUES);
SELECT 'LeaveRequest|' || count(*)
FROM leave_request
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'ExpenseClaim|' || count(*)
FROM expense_claim
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SELECT 'ExpenseItem|' || count(*)
FROM expense_item
WHERE expense_id IN ($TARGET_EXPENSE_VALUES);
SELECT 'ConversationMemory|' || count(*)
FROM ai_task_memory
WHERE user_id = '$TARGET_USER_ID';
SQL
)"
while IFS='|' read -r name value; do
    [[ -z "${name:-}" ]] && continue
    info "[VERIFY] $name=$value"
    [[ "$value" == "0" ]] || fail "目标记录未清零：$name=$value"
done <<< "$TARGET_COUNTS_AFTER"

TARGET_BALANCE_AFTER="$(business_psql <<SQL
SELECT count(*) || '|' || COALESCE(min(annual_balance)::text, 'NULL')
FROM leave_account
WHERE employee_id = '$TARGET_EMPLOYEE_ID';
SQL
)"
info "[VERIFY] LeaveBalance=$TARGET_BALANCE_AFTER（期望 1|10.0）"
[[ "$TARGET_BALANCE_AFTER" == "1|10.0" ]] || fail "E10001 年假余额未恢复为固定基线 10.0"

USER_FIXTURES_AFTER="$(business_psql <<'SQL'
SELECT user_id || '|' || username || '|' || COALESCE(employee_id, '') || '|' || role || '|' || enabled
FROM app_user
ORDER BY user_id;
SQL
)"
[[ "$USER_FIXTURES_BEFORE" == "$USER_FIXTURES_AFTER" ]] || fail "用户 fixture 发生变化"
info "[VERIFY] app_user fixture unchanged"

info "[VERIFY] EXTERNAL_FIXTURE_VERIFICATION = NOT_APPLICABLE"
info "[VERIFY] 外部 Enterprise OA trip/invoice fixture 由后续 Smoke 验证，不在本脚本访问"
info "[VERIFY] 完成：目标 Demo 状态已恢复；用户、外部 fixture、migration 和 sequence 未被脚本访问或修改"

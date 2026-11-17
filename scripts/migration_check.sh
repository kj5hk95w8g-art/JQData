#!/usr/bin/env bash
#
# migration_check.sh 模板 —— 部署后迁移自检（migration 漏跑拦截）
# TEMPLATE_VERSION: 2
# @gate(shadow, migration-check-columns)
#
# 背景：迁移脚本是手动幂等执行、不随部署自动跑，曾出现同一迁移在生产与
# 测试环境双双漏跑（2026-09-10 管家 migration 100 复盘）；v3.5.18 再发列级
# 漏跑（2026-10-28 管家事故：加列迁移表全在、检查全绿放行，生产多入口 500）。
# v2 补列级核对与纯数据迁移声明。
#
# 用法（在项目仓库内、目标环境上执行）：
#   DB_PATH=/path/to/hni_client.db bash scripts/migration_check.sh
#   可选 MIGRATION_CHECK_MODE=shadow|enforce（缺省 shadow）
#   不传 DB_PATH 时尝试 python3 -c "import config; print(config.DB_PATH)"
#
# 约定：迁移脚本在文件头声明机读常量（可被 shell grep 到），三选一：
#   TARGET_TABLES = ['operation_audit_logs', 'external_api_logs']
#       表存在性核对（缺失即 exit 1 —— v1 既有硬拦行为，不设影子期）
#   TARGET_COLUMNS = [('fund_info', 'operation_fee'), ...]
#       列存在性核对（enforce 缺失 exit 1；shadow 缺失只 [shadow] 告警不拦）
#   TARGET_NONE = True
#       纯数据迁移（无 schema 目标，跳过核对，不计未声明 warn；执行状态靠
#       _data_migrations 登记表对账）
# 未声明任何常量的迁移脚本列为 [warn]（无法核对，请补声明）。
#
# 影子期（gates.shadow_period）：列级核对为新闸 shadow 起步，转正由各项目
# gate_modes 配置拍板（calibration 记录见 docs/gates/gate_registry.yml）。
#
# 自包含：仅依赖 bash + sqlite3 或 python3（二选一）。退出码 0=通过 1=缺表或缺列(enforce)。
set -u

DB_PATH="${DB_PATH:-}"
MIG_DIR="${MIG_DIR:-scripts/migrations}"
MODE="${MIGRATION_CHECK_MODE:-shadow}"

# ---------- 定位 DB ----------
if [ -z "$DB_PATH" ]; then
    DB_PATH=$(python3 -c "import config; print(config.DB_PATH)" 2>/dev/null || true)
fi
if [ -z "$DB_PATH" ] || [ ! -f "$DB_PATH" ]; then
    echo "❌ [migration_check] 未定位到数据库文件（DB_PATH 未设且 config.DB_PATH 不可读）"
    exit 1
fi
echo "🔍 [migration_check] 库：${DB_PATH}；迁移目录：${MIG_DIR}；模式：${MODE}"

if [ ! -d "$MIG_DIR" ]; then
    echo "✅ [migration_check] 无迁移目录（${MIG_DIR}），跳过"
    exit 0
fi

# ---------- 读 sqlite_master（优先 sqlite3 CLI，回退 python3）----------
list_tables() {
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table';"
    else
        python3 -c "
import sqlite3, sys
conn = sqlite3.connect('$DB_PATH')
for (n,) in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\"):
    print(n)
"
    fi
}
list_columns() {
    # 输出指定表的列名（每行一个）；表不存在时输出为空
    local table="$1"
    if command -v sqlite3 >/dev/null 2>&1; then
        # PRAGMA table_info 每行输出 cid|name|type|... ，只取 name（第 2 列）
        sqlite3 "$DB_PATH" "PRAGMA table_info($table);" | cut -d'|' -f2
    else
        python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
for row in conn.execute(\"PRAGMA table_info($table)\"):
    print(row[1])
"
    fi
}
TABLES=$(list_tables | sort -u)
if [ -z "$TABLES" ]; then
    echo "❌ [migration_check] sqlite_master 读取为空（库异常？）"
    exit 1
fi

# ---------- 逐迁移脚本核对（只核 migrate_*.py 正式迁移；_registry/seed_*/一次性脚本不入核）----------
FAILED=0
WARNED=0
for m in "$MIG_DIR"/migrate_*.py; do
    [ -f "$m" ] || continue

    # 纯数据迁移：跳过 schema 核对（执行状态由 _data_migrations 登记表对账）
    if grep -qE "TARGET_NONE[[:space:]]*=[[:space:]]*True" "$m" 2>/dev/null; then
        echo "ℹ️  $(basename "$m") 声明 TARGET_NONE（纯数据迁移，靠 _data_migrations 登记表核对）"
        continue
    fi

    # 提取 TARGET_TABLES = [...] / (...)（整文件解析，声明可跨行）
    DECL=$(MIG_FILE="$m" python3 -c "
import os, re
src = open(os.environ['MIG_FILE'], encoding='utf-8').read()
m = re.search(r'TARGET_TABLES\s*=\s*[(\[](.*?)[)\]]', src, re.S)
print(m.group(1) if m else '')
")
    # 提取 TARGET_COLUMNS = [('表','列'), ...] → 逐行输出 "表|列"（整文件解析，声明可跨行）
    DECL_COLS=$(MIG_FILE="$m" python3 -c "
import os, re, ast
src = open(os.environ['MIG_FILE'], encoding='utf-8').read()
m = re.search(r'TARGET_COLUMNS\s*=\s*(\[.*?\]|\(.*?\))', src, re.S)
if not m:
    print('')
    raise SystemExit
try:
    pairs = ast.literal_eval(m.group(1))
    for pair in pairs:
        t, c = pair
        print(f'{t}|{c}')
except Exception:
    print('__PARSE_ERROR__')
")

    if [ -z "$DECL" ] && [ -z "$DECL_COLS" ]; then
        echo "⚠️  [warn] $m 未声明 TARGET_TABLES/TARGET_COLUMNS/TARGET_NONE，无法核对（请补机读声明）"
        WARNED=1
        continue
    fi

    # 表级核对（v1 既有硬拦：缺表即 FAILED，不受 MODE 影响）
    if [ -n "$DECL" ]; then
        # shellcheck disable=SC2206
        TBL=($(echo "$DECL" | tr -d "'\"" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$'))
        for t in "${TBL[@]}"; do
            if echo "$TABLES" | grep -qx "$t"; then
                echo "✅ ${t}（$(basename "$m")）已存在"
            else
                FAILED=1
                echo "❌ [block] 表 $t 缺失 —— $(basename "$m") 声明但库中不存在"
                echo "           执行：python3 $m"
            fi
        done
    fi

    # 列级核对（v2 新闸：enforce 缺列 FAILED；shadow 只告警）
    if [ -n "$DECL_COLS" ]; then
        if [ "$DECL_COLS" = "__PARSE_ERROR__" ]; then
            echo "⚠️  [warn] $m 的 TARGET_COLUMNS 声明解析失败（应为 [('表','列'), ...] 字面量）"
            WARNED=1
        else
            while IFS='|' read -r t c; do
                [ -z "$t" ] && continue
                if list_columns "$t" | grep -qx "$c"; then
                    echo "✅ 列 ${t}.${c}（$(basename "$m")）已存在"
                elif [ "$MODE" = "enforce" ]; then
                    FAILED=1
                    echo "❌ [block] 列 $t.$c 缺失 —— $(basename "$m") 声明但库中不存在"
                    echo "           执行：python3 $m"
                else
                    echo "⚠️  [shadow] 列 $t.$c 缺失（$(basename "$m")）—— shadow 只报不拦，enforce 转正后阻断"
                fi
            done <<< "$DECL_COLS"
        fi
    fi
done

echo "----"
if [ "$FAILED" -eq 1 ]; then
    echo "❌ migration_check: 存在漏跑迁移（模式：${MODE}），请在目标环境执行上述命令后重查"
    exit 1
fi
echo "✅ migration_check: 通过（warn=${WARNED}，模式：${MODE}）"
exit 0

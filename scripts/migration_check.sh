#!/usr/bin/env bash
#
# migration_check.sh 模板 —— 部署后迁移自检（migration 漏跑拦截）
# TEMPLATE_VERSION: 1
#
# 背景：迁移脚本是手动幂等执行、不随部署自动跑，曾出现同一迁移在生产与
# 测试环境双双漏跑（2026-09-10 管家 migration 100 复盘）。本脚本在部署健康
# 检查后核对「迁移声明的表」是否真的存在于目标库，缺表即 FAIL 并给出执行命令。
#
# 用法（在项目仓库内、目标环境上执行）：
#   DB_PATH=/path/to/hni_client.db bash scripts/migration_check.sh
#   不传 DB_PATH 时尝试 python3 -c "import config; print(config.DB_PATH)"
#
# 约定：迁移脚本在文件头声明机读常量（可被 shell grep 到）：
#   TARGET_TABLES = ['operation_audit_logs', 'external_api_logs']
# 未声明 TARGET_TABLES 的迁移脚本会被列为 [warn]（无法核对，请补声明）。
#
# 自包含：仅依赖 bash + sqlite3 或 python3（二选一）。退出码 0=通过 1=有缺表。
set -u

DB_PATH="${DB_PATH:-}"
MIG_DIR="${MIG_DIR:-scripts/migrations}"

# ---------- 定位 DB ----------
if [ -z "$DB_PATH" ]; then
    DB_PATH=$(python3 -c "import config; print(config.DB_PATH)" 2>/dev/null || true)
fi
if [ -z "$DB_PATH" ] || [ ! -f "$DB_PATH" ]; then
    echo "❌ [migration_check] 未定位到数据库文件（DB_PATH 未设且 config.DB_PATH 不可读）"
    exit 1
fi
echo "🔍 [migration_check] 库：$DB_PATH；迁移目录：$MIG_DIR"

if [ ! -d "$MIG_DIR" ]; then
    echo "✅ [migration_check] 无迁移目录（$MIG_DIR），跳过"
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
TABLES=$(list_tables | sort -u)
if [ -z "$TABLES" ]; then
    echo "❌ [migration_check] sqlite_master 读取为空（库异常？）"
    exit 1
fi

# ---------- 逐迁移脚本核对 ----------
FAILED=0
WARNED=0
for m in "$MIG_DIR"/*.py; do
    [ -f "$m" ] || continue
    # 提取 TARGET_TABLES = [...] / (...)
    DECL=$(grep -m1 -E "^TARGET_TABLES\s*=" "$m" 2>/dev/null | sed -E "s/^TARGET_TABLES\s*=\s*[\(\[]//; s/[\)\]]\s*$//")
    if [ -z "$DECL" ]; then
        echo "⚠️  [warn] $m 未声明 TARGET_TABLES，无法核对（请补机读声明）"
        WARNED=1
        continue
    fi
    # shellcheck disable=SC2206
    TBL=($(echo "$DECL" | tr -d "'\"" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$'))
    for t in "${TBL[@]}"; do
        if echo "$TABLES" | grep -qx "$t"; then
            echo "✅ $t（$(basename "$m")）已存在"
        else
            FAILED=1
            echo "❌ [block] 表 $t 缺失 —— $(basename "$m") 声明但库中不存在"
            echo "           执行：python3 $m"
        fi
    done
done

echo "----"
if [ "$FAILED" -eq 1 ]; then
    echo "❌ migration_check: 存在漏跑迁移，请在目标环境执行上述命令后重查"
    exit 1
fi
echo "✅ migration_check: 通过（warn=$WARNED）"
exit 0

#!/bin/bash
# JQData 每日同步脚本（crontab 定时触发）
# 用法:
#   SYNC_PHASE=market  → 只同步当天核心行情（15:30 执行）
#   SYNC_PHASE=full    → 断点续传 + 扩展数据增量（23:00 执行）

# set -e  # 移除，因为后面大量使用 || true 忽略错误

PROJECT_DIR="/data/jqdata-platform"
LOG_DIR="$PROJECT_DIR/logs"
SYNC_PHASE="${SYNC_PHASE:-full}"
# market 阶段单独日志文件，避免与 full 阶段混在一起
if [ "$SYNC_PHASE" = "market" ]; then
    LOG_FILE="$LOG_DIR/jqdata-sync-market.log"
else
    LOG_FILE="$LOG_DIR/jqdata-sync.log"
fi
PHASES_FILE="/tmp/jqdata-sync-phases-${USER:-unknown}.json"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# root 用户禁止运行（统一使用 deploy 用户执行）
if [ "$(id -un)" = "root" ]; then
    echo "[$(date)] root 用户禁止运行此脚本，请使用 deploy 用户" >> "$LOG_FILE"
    exit 0
fi

# 互斥锁：防止同一脚本多实例并行运行
LOCK_FILE="/tmp/jqdata-sync-incremental.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date)] 另一个同步实例正在运行，退出" >> "$LOG_FILE"
    exit 0
fi


# 加载环境变量
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# 检查必要环境变量
if [ -z "$JQ_USER" ] || [ -z "$JQ_PASS" ]; then
    echo "[$(date)] 错误: JQ_USER 或 JQ_PASS 未设置" >> "$LOG_FILE"
    exit 1
fi

cd "$PROJECT_DIR"

# ── 统一同步命令（优先使用 venv 中的 Python，隔离 NumPy 版本）──
if [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    SYNC_CMD="$PROJECT_DIR/.venv/bin/python3"
else
    SYNC_CMD="python3"
fi
FAILED_TASKS=""

# 初始化阶段记录文件
echo '{"phases":[]}' > "$PHASES_FILE"

record_phase() {
    local label="$1"
    local status="$2"
    local exit_code="${3:-0}"
    # 用 stdin 传 JSON 数据，避免 shell 变量注入
    python3 -c "
import json, sys
data = json.load(sys.stdin)
with open('$PHASES_FILE') as f:
    phases = json.load(f)
phases['phases'].append(data)
with open('$PHASES_FILE', 'w') as f:
    json.dump(phases, f, ensure_ascii=False)
" <<EOF
{"label": "$label", "status": "$status", "exit_code": $exit_code}
EOF
}

run_sync() {
    local label="$1"; shift
    echo "[$(date)] 开始: $label" >> "$LOG_FILE"
    if "$@" >> "$LOG_FILE" 2>&1; then
        echo "[$(date)] ✅ 完成: $label" >> "$LOG_FILE"
        record_phase "$label" "success" 0
        return 0
    else
        local code=$?
        echo "[$(date)] ❌ 失败: $label (exit=$code)" >> "$LOG_FILE"
        FAILED_TASKS="$FAILED_TASKS\n  - $label (exit=$code)"
        record_phase "$label" "failed" "$code"
        return 1
    fi
}

echo "[$(date)] ====== 同步开始 (phase=${SYNC_PHASE}) ======" >> "$LOG_FILE"

# ═══════════════════════════════════════════════════════════════
# Phase: market — 核心行情增量（15:30 执行，当天收盘数据）
# ═══════════════════════════════════════════════════════════════
if [ "$SYNC_PHASE" = "market" ]; then
    echo "[$(date)] --- 核心行情增量同步 ---" >> "$LOG_FILE"
    run_sync "日线增量" $SYNC_CMD src/sync_daily.py --incremental --table all || true
    echo "[$(date)] --- ETF 行情增量同步 ---" >> "$LOG_FILE"
    run_sync "ETF增量" $SYNC_CMD src/sync_etf.py --incremental || true
    echo "[$(date)] ====== 核心行情同步结束 ======" >> "$LOG_FILE"

    # 汇总 & 通知
    if [ -n "$FAILED_TASKS" ]; then
        echo "[$(date)] ====== 以下任务失败 ======" >> "$LOG_FILE"
        echo -e "$FAILED_TASKS" >> "$LOG_FILE"
        $SYNC_CMD src/notify.py "market_同步失败" "$LOG_FILE" "$PHASES_FILE" >> "$LOG_FILE" 2>&1 || true
        exit 1
    fi
    $SYNC_CMD src/notify.py "market_完成" "$LOG_FILE" "$PHASES_FILE" >> "$LOG_FILE" 2>&1 || true
    exit 0
fi

# ═══════════════════════════════════════════════════════════════
# Phase: full — 断点续传 + 扩展数据（23:00 执行）
# ═══════════════════════════════════════════════════════════════

export DAILY_QUOTA_LIMIT=180000000

echo "[$(date)] --- 阶段1：日线增量 ---" >> "$LOG_FILE"
run_sync "日线增量" $SYNC_CMD src/sync_daily.py --incremental --table all || true

echo "[$(date)] --- 阶段2：日线断点续传 ---" >> "$LOG_FILE"
TRIAL_END_VAL=${TRIAL_END:-$(date +%Y-%m-%d)}

# 检查 stock_daily_pre 是否已完成
PRE_DONE=false
PRE_CP=$(redis-cli HGET jqdata_sync:checkpoint stock_daily_pre 2>/dev/null || echo "")
if [ -z "$PRE_CP" ]; then
    PRE_MAX=$(python3 -c "from clickhouse_driver import Client; c=Client(host='localhost',database='jqdata'); r=c.execute('SELECT max(trade_date) FROM stock_daily_pre'); print(r[0][0] if r and r[0][0] else '')" 2>/dev/null || echo "")
    if [ -n "$PRE_MAX" ] && [ "$PRE_MAX" = "$TRIAL_END_VAL" ]; then
        PRE_DONE=true
    fi
elif [ "$PRE_CP" = "$TRIAL_END_VAL" ]; then
    PRE_DONE=true
fi

# 检查 index_daily 是否已完成
INDEX_DONE=false
INDEX_CP=$(redis-cli HGET jqdata_sync:checkpoint index_daily 2>/dev/null || echo "")
if [ -n "$INDEX_CP" ] && [ "$INDEX_CP" = "$TRIAL_END_VAL" ]; then
    INDEX_DONE=true
fi

# 检查 stock_daily_post 是否已开始
POST_STARTED=false
POST_CP=$(redis-cli HGET jqdata_sync:checkpoint stock_daily_post 2>/dev/null || echo "")
if [ -n "$POST_CP" ]; then
    POST_STARTED=true
fi

# 按优先级执行：pre → index → post
if [ "$PRE_DONE" = false ]; then
    echo "[$(date)] 执行 pre 断点续传" >> "$LOG_FILE"
    run_sync "pre断点续传" $SYNC_CMD src/sync_daily.py --resume --fq pre --table stock || true
else
    echo "[$(date)] pre 已最新，跳过" >> "$LOG_FILE"
    record_phase "pre断点续传" "skipped" 0
fi

if [ "$INDEX_DONE" = false ]; then
    echo "[$(date)] 执行 index 断点续传" >> "$LOG_FILE"
    run_sync "index断点续传" $SYNC_CMD src/sync_daily.py --resume --fq pre --table index || true
else
    echo "[$(date)] index 已最新，跳过" >> "$LOG_FILE"
    record_phase "index断点续传" "skipped" 0
fi

if [ "$POST_STARTED" = true ]; then
    echo "[$(date)] 执行 post 断点续传" >> "$LOG_FILE"
    run_sync "post断点续传" $SYNC_CMD src/sync_daily.py --resume --fq post --table stock || true
else
    if [ "$PRE_DONE" = true ] && [ "$INDEX_DONE" = true ]; then
        echo "[$(date)] 首次启动 post 全量同步" >> "$LOG_FILE"
        run_sync "post首次全量" $SYNC_CMD src/sync_daily.py --resume --fq post --table stock || true
    else
        echo "[$(date)] post 等待 pre/index 完成后启动" >> "$LOG_FILE"
        record_phase "post断点续传" "skipped" 0
    fi
fi

# ── 阶段3：ETF ──
echo "[$(date)] --- 阶段3：ETF ---" >> "$LOG_FILE"
run_sync "ETF日线增量" $SYNC_CMD src/sync_etf.py --incremental || true

ETF_DONE=false
ETF_CP=$(redis-cli HGET jqdata_sync:checkpoint etf_daily 2>/dev/null || echo "")
if [ -n "$ETF_CP" ] && [ "$ETF_CP" = "$TRIAL_END_VAL" ]; then
    ETF_DONE=true
fi
if [ "$ETF_DONE" = false ]; then
    echo "[$(date)] 执行 ETF 断点续传" >> "$LOG_FILE"
    run_sync "ETF断点续传" $SYNC_CMD src/sync_etf.py --resume || true
else
    echo "[$(date)] ETF 已最新，跳过断点续传" >> "$LOG_FILE"
    record_phase "ETF断点续传" "skipped" 0
fi

# ── 阶段4：扩展数据增量 ──
echo "[$(date)] --- 阶段4：扩展数据增量 ---" >> "$LOG_FILE"

# 交易日历：每周同步一次（数据量小，变化少）
CAL_DAYS=$(python3 -c "from clickhouse_driver import Client; c=Client(host='localhost',database='jqdata'); r=c.execute('SELECT max(sync_date) FROM trade_calendar'); print((__import__('datetime').date.today()-r[0][0]).days if r and r[0][0] else 999)" 2>/dev/null || echo "999")
if [ "$CAL_DAYS" -gt 7 ] 2>/dev/null; then
    echo "[$(date)] trade_calendar 超过7天未更新，执行同步" >> "$LOG_FILE"
    run_sync "交易日历" $SYNC_CMD src/sync_trade_calendar.py || true
fi

echo "[$(date)] 执行 index_weights 增量同步" >> "$LOG_FILE"
run_sync "指数权重" env SYNC_MODE=incremental $SYNC_CMD src/sync_index_weights.py || true

echo "[$(date)] 执行 stk_xr_xd 增量同步" >> "$LOG_FILE"
run_sync "除权除息" env SYNC_MODE=incremental $SYNC_CMD src/sync_stk_xr_xd.py || true

echo "[$(date)] 执行 extended 增量同步" >> "$LOG_FILE"
run_sync "融资融券/龙虎榜" $SYNC_CMD src/sync_extended.py --incremental --days 7 || true

echo "[$(date)] 执行 fundamentals 增量同步" >> "$LOG_FILE"
run_sync "估值/财务" $SYNC_CMD src/sync_fundamentals.py --incremental --days 7 || true

# 每周日补充同步季度财报数据（避免增量模式跳过季度表）
if [ "$(date +%u)" = "7" ]; then
    echo "[$(date)] 周日补充季度财报同步" >> "$LOG_FILE"
    run_sync "季度财报" $SYNC_CMD src/sync_fundamentals.py --quarterly || true
fi

# ── 额度报告 ──
QUOTA_USED=$(redis-cli GET jqdata_sync:quota_used_today 2>/dev/null || echo "0")
echo "[$(date)] 今日已用额度: ${QUOTA_USED} / ${DAILY_QUOTA_LIMIT}" >> "$LOG_FILE"
echo "[$(date)] ====== 每日同步结束 ======" >> "$LOG_FILE"

# ── 通知 ──
if [ -n "$FAILED_TASKS" ]; then
    echo "[$(date)] ====== 以下任务失败 ======" >> "$LOG_FILE"
    echo -e "$FAILED_TASKS" >> "$LOG_FILE"
    $SYNC_CMD src/notify.py "full_同步失败" "$LOG_FILE" "$PHASES_FILE" >> "$LOG_FILE" 2>&1 || true
    exit 1
fi

$SYNC_CMD src/notify.py "full_完成" "$LOG_FILE" "$PHASES_FILE" >> "$LOG_FILE" 2>&1 || true

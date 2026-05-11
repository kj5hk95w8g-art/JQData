#!/bin/bash
# JQData 每日同步脚本（crontab 定时触发）
# 策略：先增量（当天数据，优先级高），剩余额度全量补全
# 触发时间：交易日 23:00
# 额度控制：保留 2000 万条，其余 1.8 亿条用于同步

set -e

PROJECT_DIR="/data/jqdata-platform"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/jqdata-sync.log"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

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

# ── 额度设置：保留 2000 万条，可用 1.8 亿条 ──
export DAILY_QUOTA_LIMIT=180000000

cd "$PROJECT_DIR"

echo "[$(date)] ====== 开始每日同步（可用额度: ${DAILY_QUOTA_LIMIT} 条）======" >> "$LOG_FILE"

# ── 阶段1：增量同步（同步当天收盘数据，优先级高）──
echo "[$(date)] --- 阶段1：增量同步 ---" >> "$LOG_FILE"
python3 src/sync_daily.py --incremental --table all >> "$LOG_FILE" 2>&1 || true

# ── 阶段2：全量补全（pre → index → post）──
echo "[$(date)] --- 阶段2：全量补全 ---" >> "$LOG_FILE"

TRIAL_END_VAL=${TRIAL_END:-$(date +%Y-%m-%d)}

# 检查 stock_daily_pre 是否已完成
PRE_DONE=false
PRE_CP=$(redis-cli HGET jqdata_sync:checkpoint stock_daily_pre 2>/dev/null || echo "")
if [ -z "$PRE_CP" ]; then
    PRE_MAX=$(python3 -c "from clickhouse_driver import Client; c=Client(host='localhost',database='jqdata'); r=c.execute('SELECT max(trade_date) FROM stock_daily_pre'); print(r[0][0] if r and r[0][0] else '')" 2>/dev/null || echo "")
    if [ -n "$PRE_MAX" ] && [ "$PRE_MAX" = "$TRIAL_END_VAL" ]; then
        PRE_DONE=true
        PRE_CP="$PRE_MAX"
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
    echo "[$(date)] 执行 pre 全量补全" >> "$LOG_FILE"
    python3 src/sync_daily.py --resume --fq pre --table stock >> "$LOG_FILE" 2>&1 || true
else
    echo "[$(date)] pre 已完成，跳过" >> "$LOG_FILE"
fi

if [ "$INDEX_DONE" = false ]; then
    echo "[$(date)] 执行 index 全量补全" >> "$LOG_FILE"
    python3 src/sync_daily.py --resume --fq pre --table index >> "$LOG_FILE" 2>&1 || true
else
    echo "[$(date)] index 已完成，跳过" >> "$LOG_FILE"
fi

if [ "$POST_STARTED" = true ]; then
    echo "[$(date)] 执行 post 断点续传" >> "$LOG_FILE"
    python3 src/sync_daily.py --resume --fq post --table stock >> "$LOG_FILE" 2>&1 || true
else
    # post 从未开始，在 pre 和 index 都完成后启动
    if [ "$PRE_DONE" = true ] && [ "$INDEX_DONE" = true ]; then
        echo "[$(date)] 首次启动 post 全量同步" >> "$LOG_FILE"
        python3 src/sync_daily.py --resume --fq post --table stock >> "$LOG_FILE" 2>&1 || true
    else
        echo "[$(date)] post 等待 pre/index 完成后启动" >> "$LOG_FILE"
    fi
fi

# ── 额度报告 ──
QUOTA_USED=$(redis-cli GET jqdata_sync:quota_used_today 2>/dev/null || echo "0")
echo "[$(date)] 今日已用额度: ${QUOTA_USED} / ${DAILY_QUOTA_LIMIT}" >> "$LOG_FILE"
echo "[$(date)] ====== 每日同步结束 ======" >> "$LOG_FILE"

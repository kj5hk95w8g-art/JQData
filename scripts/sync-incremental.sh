#!/bin/bash
# JQData 每日同步脚本（crontab 定时触发）
# 策略：先增量（当天数据，优先级高），剩余额度全量补全
# 触发时间：交易日 23:00（把当天额度用完）

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

cd "$PROJECT_DIR"

echo "[$(date)] ====== 开始每日同步（先增量，后全量）======" >> "$LOG_FILE"

# ── 阶段1：增量同步（优先级高，同步当天收盘数据）──
# 增量受 DAILY_QUOTA_LIMIT 限制（默认 550 万），留额度给白天
echo "[$(date)] --- 阶段1：增量同步 ---" >> "$LOG_FILE"
python3 src/sync_daily.py --incremental --table all >> "$LOG_FILE" 2>&1

# ── 阶段2：全量补全（晚上 23:00 放开自限，把 JQData 真实剩余额度用完）──
echo "[$(date)] --- 阶段2：全量补全（放开额度限制）---" >> "$LOG_FILE"

# 检查 pre 是否已完成
PRE_CP=$(redis-cli HGET jqdata_sync:checkpoint stock_daily_pre 2>/dev/null || echo "")
if [ -z "$PRE_CP" ]; then
    PRE_MAX=$(python3 -c "from clickhouse_driver import Client; c=Client(host='localhost',database='jqdata'); r=c.execute('SELECT max(trade_date) FROM stock_daily_pre'); print(r[0][0] if r and r[0][0] else '')" 2>/dev/null || echo "")
    if [ -n "$PRE_MAX" ] && [ "$PRE_MAX" \> "2000-01-01" ]; then
        PRE_CP="$PRE_MAX"
    fi
fi

TRIAL_END_VAL=${TRIAL_END:-$(date +%Y-%m-%d)}
if [ -n "$PRE_CP" ] && [ "$PRE_CP" = "$TRIAL_END_VAL" ]; then
    echo "[$(date)] pre 已完成，继续 post 全量补全" >> "$LOG_FILE"
    python3 src/sync_daily.py --resume --fq post --table stock --no-quota-limit >> "$LOG_FILE" 2>&1 || true
else
    echo "[$(date)] 继续 pre 全量补全" >> "$LOG_FILE"
    python3 src/sync_daily.py --resume --fq pre --table stock --no-quota-limit >> "$LOG_FILE" 2>&1 || true
fi

echo "[$(date)] ====== 每日同步结束 ======" >> "$LOG_FILE"

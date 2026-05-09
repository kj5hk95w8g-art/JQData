#!/bin/bash
# =============================================================================
# 轻量健康检查 + 告警脚本
# 
# 用法：加到 crontab 每分钟执行
#   * * * * * /data/jqdata-platform/scripts/health-check-alert.sh >> /data/monitoring/alerts/alerts.log 2>&1
#
# 告警方式：
#   1. 写入 /data/monitoring/alerts/alerts.log（默认）
#   2. 如果配置了 WEBHOOK_URL，会发送 HTTP POST 通知
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALERT_LOG_DIR="/data/monitoring/alerts"
ALERT_LOG="${ALERT_LOG_DIR}/alerts.log"
LOCK_FILE="/tmp/health-check-alert.lock"

# ============ 可配置项 ============
# 企业微信/钉钉/飞书 webhook（可选）
WEBHOOK_URL="${WEBHOOK_URL:-}"
# 磁盘告警阈值（%）
DISK_THRESHOLD=90
# 内存告警阈值（%）
MEM_THRESHOLD=90
# 检查的关键容器
CONTAINERS=("jqdata-clickhouse" "jqdata-redis" "jqdata-api")
# ==================================

# 防重复执行
if [[ -f "$LOCK_FILE" ]]; then
    PID=$(cat "$LOCK_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 脚本已在运行(PID=$PID)，跳过"
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

# 初始化日志目录
mkdir -p "$ALERT_LOG_DIR"

ALERTS=()
HOSTNAME=$(hostname)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

send_alert() {
    local msg="$1"
    echo "[$TIMESTAMP] $msg" >> "$ALERT_LOG"
    ALERTS+=("$msg")
    
    # 如果配置了 webhook，发送通知
    if [[ -n "$WEBHOOK_URL" ]]; then
        curl -s -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[JQData告警] $HOSTNAME: $msg\"}}" \
            > /dev/null 2>&1 || true
    fi
}

echo ""
echo "========== [$TIMESTAMP] 健康检查开始 =========="

# 1. 检查容器状态
echo "--- 容器状态检查 ---"
for c in "${CONTAINERS[@]}"; do
    if ! docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
        send_alert "容器 ${c} 未运行！"
    else
        STATUS=$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo "none")
        if [[ "$STATUS" != "healthy" && "$STATUS" != "none" ]]; then
            send_alert "容器 ${c} 健康状态异常: ${STATUS}"
        else
            echo "  ✓ ${c}: ${STATUS}"
        fi
    fi
done

# 2. 检查磁盘
echo "--- 磁盘检查 ---"
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [[ "$DISK_USAGE" -gt "$DISK_THRESHOLD" ]]; then
    send_alert "根分区磁盘使用率 ${DISK_USAGE}% > ${DISK_THRESHOLD}%"
else
    echo "  ✓ 磁盘使用率: ${DISK_USAGE}%"
fi

# 3. 检查内存
echo "--- 内存检查 ---"
MEM_USAGE=$(free | grep Mem | awk '{printf("%.0f", $3/$2 * 100.0)}')
if [[ "$MEM_USAGE" -gt "$MEM_THRESHOLD" ]]; then
    send_alert "内存使用率 ${MEM_USAGE}% > ${MEM_THRESHOLD}%"
else
    echo "  ✓ 内存使用率: ${MEM_USAGE}%"
fi

# 4. 检查API接口（如果容器在运行）
echo "--- API 接口检查 ---"
if docker ps --format '{{.Names}}' | grep -q "^jqdata-api$"; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/health --max-time 5 || echo "000")
    if [[ "$HTTP_CODE" != "200" ]]; then
        send_alert "API 健康检查返回 HTTP ${HTTP_CODE}"
    else
        echo "  ✓ API /health: 200"
    fi
fi

# 汇总
if [[ ${#ALERTS[@]} -gt 0 ]]; then
    echo ""
    echo "⚠️  发现 ${#ALERTS[@]} 个告警:"
    for a in "${ALERTS[@]}"; do
        echo "   - $a"
    done
else
    echo ""
    echo "✅ 所有检查通过"
fi

echo "========== 健康检查结束 =========="

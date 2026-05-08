#!/bin/bash
# JQData 统一部署脚本（在目标服务器上执行）
# D服务器: ./scripts/deploy.sh d
# C服务器: ./scripts/deploy.sh c
set -euo pipefail

SERVER=${1:-d}
PROJECT_DIR="/data/jqdata-platform"

echo "===== JQData Deploy [$SERVER] ====="
cd "$PROJECT_DIR"

case "$SERVER" in
    d)
        # D服务器: ClickHouse + Redis + API
        docker compose -f docker-compose.d.yml up -d
        sleep 5
        HEALTH=$(curl -sf http://localhost:8000/health || echo "FAIL")
        if echo "$HEALTH" | grep -q '"status":"ok"'; then
            echo "[OK] API health check passed"
        else
            echo "[WARN] API health check: $HEALTH"
        fi
        ;;
    c)
        # C服务器: Grafana
        docker compose -f docker-compose.c.yml up -d
        sleep 3
        HEALTH=$(curl -sf http://localhost:3000/api/health || echo "FAIL")
        if echo "$HEALTH" | grep -q '"database":"ok"'; then
            echo "[OK] Grafana health check passed"
        else
            echo "[WARN] Grafana health check: $HEALTH"
        fi
        ;;
    *)
        echo "Usage: $0 [d|c]"
        exit 1
        ;;
esac

echo "[OK] Deploy completed"

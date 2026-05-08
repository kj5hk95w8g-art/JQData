#!/bin/bash
# JQData 统一部署脚本（在目标服务器上执行）
# 用法: ./scripts/deploy.sh [a|b|c|d|all]
#
# 服务器定义:
#   A = 应用服务器    106.14.141.212  (不动)
#   B = 测试服务器    139.196.34.92   (不动)
#   C = 副本/可视化   139.196.186.67  (Grafana)
#   D = 核心数据层    101.132.161.52  (ClickHouse+Redis+FastAPI)

set -euo pipefail

SERVER=${1:-d}
PROJECT_DIR="/data/jqdata-platform"

echo "===== JQData Deploy [$SERVER] ====="
cd "$PROJECT_DIR"

case "$SERVER" in
    a|A)
        echo "[SKIP] A服务器(106.14.141.212)为应用服务器，禁止部署新组件"
        exit 0
        ;;
    b|B)
        echo "[SKIP] B服务器(139.196.34.92)为测试服务器，保留现有环境"
        exit 0
        ;;
    d|D)
        echo "[INFO] D服务器(101.132.161.52) — 核心数据层部署"
        docker compose -f docker-compose.d.yml up -d
        sleep 5
        HEALTH=$(curl -sf http://localhost:8000/health || echo "FAIL")
        if echo "$HEALTH" | grep -q '"status":"ok"'; then
            echo "[OK] API health check passed"
        else
            echo "[WARN] API health check: $HEALTH"
        fi
        ;;
    c|C)
        echo "[INFO] C服务器(139.196.186.67) — 可视化层部署"
        docker compose -f docker-compose.c.yml up -d
        sleep 3
        HEALTH=$(curl -sf http://localhost:3000/api/health || echo "FAIL")
        if echo "$HEALTH" | grep -q '"database":"ok"'; then
            echo "[OK] Grafana health check passed"
        else
            echo "[WARN] Grafana health check: $HEALTH"
        fi
        ;;
    all)
        echo "[INFO] 部署所有JQData服务器 (C+D)"
        "$0" d
        "$0" c
        exit 0
        ;;
    *)
        echo "用法: $0 [a|b|c|d|all]"
        echo ""
        echo "服务器定义:"
        echo "  a/A = 应用服务器    106.14.141.212  (禁止操作)"
        echo "  b/B = 测试服务器    139.196.34.92   (保留现状)"
        echo "  c/C = 副本/可视化   139.196.186.67  (Grafana)"
        echo "  d/D = 核心数据层    101.132.161.52  (ClickHouse+Redis+API)"
        echo "  all = 部署C+D"
        exit 1
        ;;
esac

echo "[OK] Deploy [$SERVER] completed"

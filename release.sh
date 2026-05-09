#!/bin/bash
# JQData 开发机发版脚本
# 用法: ./release.sh [a|b|c|d|all]
#
# 服务器定义:
#   A = 应用服务器    106.14.141.212  (禁止操作)
#   B = 测试服务器    139.196.34.92   (保留现状)
#   C = 副本/可视化   139.196.186.67  (Grafana)
#   D = 核心数据层    101.132.161.52  (ClickHouse+Redis+FastAPI)

set -e

TARGET=${1:-all}
SSH_PASS="Yuntu@2026"

# 服务器映射
resolve_server() {
    case "$1" in
        a|A) echo "a:106.14.141.212:应用服务器" ;;
        b|B) echo "b:139.196.34.92:测试服务器" ;;
        c|C) echo "c:139.196.186.67:副本/可视化" ;;
        d|D) echo "d:101.132.161.52:核心数据层" ;;
        *) echo "unknown" ;;
    esac
}

deploy_server() {
    local CODE=$1
    local HOST=$2
    local ROLE=$3
    
    echo ""
    echo "===== Release to $CODE ($HOST) — $ROLE ====="
    
    # 同步代码
    echo "[INFO] Syncing src/..."
    sshpass -p "$SSH_PASS" rsync -az --delete \
        /Users/kongyan/JQData/src/ \
        "root@$HOST:/data/jqdata-platform/src/" 2>/dev/null || {
        echo "[WARN] rsync failed, trying scp..."
        sshpass -p "$SSH_PASS" scp -o StrictHostKeyChecking=no -o PreferredAuthentications=password \
            /Users/kongyan/JQData/src/*.py \
            "root@$HOST:/data/jqdata-platform/src/"
    }
    
    # 执行部署
    echo "[INFO] Deploying..."
    sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no -o PreferredAuthentications=password \
        "root@$HOST" "bash /data/jqdata-platform/scripts/deploy.sh $CODE"
    
    echo "[OK] $CODE released"
}

case "$TARGET" in
    a|A)
        echo "[SKIP] A服务器为应用服务器，禁止通过release.sh部署"
        ;;
    b|B)
        echo "[SKIP] B服务器为测试服务器，禁止修改"
        ;;
    d|D)
        deploy_server "d" "101.132.161.52" "核心数据层"
        ;;
    c|C)
        deploy_server "c" "139.196.186.67" "副本/可视化"
        ;;
    all)
        deploy_server "d" "101.132.161.52" "核心数据层"
        deploy_server "c" "139.196.186.67" "副本/可视化"
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

echo ""
echo "===== Release completed ====="

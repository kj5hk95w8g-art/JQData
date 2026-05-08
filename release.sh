#!/bin/bash
# JQData 开发机发版脚本
# 用法: ./release.sh [d|c|all]
set -e

TARGET=${1:-all}
SSH_PASS="Yuntu@2026"

deploy_server() {
    local SERVER=$1
    local HOST=$2
    local ROLE=$3
    
    echo ""
    echo "===== Release to $SERVER ($HOST) ====="
    
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
        "root@$HOST" "bash /data/jqdata-platform/scripts/deploy.sh $SERVER"
    
    echo "[OK] $SERVER released"
}

case "$TARGET" in
    d|D)
        deploy_server "d" "101.132.161.52" "core"
        ;;
    c|C)
        deploy_server "c" "139.196.186.67" "visual"
        ;;
    all)
        deploy_server "d" "101.132.161.52" "core"
        deploy_server "c" "139.196.186.67" "visual"
        ;;
    *)
        echo "Usage: $0 [d|c|all]"
        exit 1
        ;;
esac

echo ""
echo "===== Release completed ====="

#!/bin/bash
# JQData 健康检查脚本
# 用法: ./scripts/health-check.sh [a|b|c|d|all]

SERVER=${1:-d}

check_a() {
    echo "===== A服务器(应用) 106.14.141.212 ====="
    echo "[SKIP] A服务器禁止直接操作，如需检查请登录服务器执行"
}

check_b() {
    echo "===== B服务器(测试) 139.196.34.92 ====="
    echo "[SKIP] B服务器保留现有环境，如需检查请登录服务器执行"
}

check_d() {
    echo "===== D服务器(核心数据) 101.132.161.52 ====="
    echo "--- Docker容器 ---"
    docker ps --format "table {{.Names}}\t{{.Status}}"
    echo "--- ClickHouse ---"
    docker exec jqdata-clickhouse clickhouse-client -q "SELECT 'OK', count() FROM system.tables WHERE database='jqdata'" 2>/dev/null || echo "FAIL"
    echo "--- Redis ---"
    docker exec jqdata-redis redis-cli ping 2>/dev/null || echo "FAIL"
    echo "--- API ---"
    curl -sf http://localhost:8000/health 2>/dev/null || echo "FAIL"
    echo "--- 数据量 ---"
    docker exec jqdata-clickhouse clickhouse-client -d jqdata -q "
        SELECT 'security_info', count() FROM security_info
        UNION ALL SELECT 'stock_daily_pre', count() FROM stock_daily_pre
        UNION ALL SELECT 'stock_daily_post', count() FROM stock_daily_post
        UNION ALL SELECT 'index_daily', count() FROM index_daily
    " 2>/dev/null || echo "FAIL"
    echo "--- 磁盘 ---"
    df -h | grep -E 'Filesystem|/dev/vda'
}

check_c() {
    echo "===== C服务器(可视化) 139.196.186.67 ====="
    echo "--- Docker容器 ---"
    docker ps --format "table {{.Names}}\t{{.Status}}"
    echo "--- Grafana ---"
    curl -sf http://localhost:3000/api/health 2>/dev/null || echo "FAIL"
    echo "--- 磁盘 ---"
    df -h | grep -E 'Filesystem|/dev/vda'
}

case "$SERVER" in
    a|A) check_a ;;
    b|B) check_b ;;
    d|D) check_d ;;
    c|C) check_c ;;
    all)
        check_d
        echo ""
        check_c
        ;;
    *)
        echo "用法: $0 [a|b|c|d|all]"
        exit 1
        ;;
esac

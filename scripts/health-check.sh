#!/bin/bash
SERVER=${1:-d}

case "$SERVER" in
    d)
        echo "===== D服务器健康检查 ====="
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
        ;;
    c)
        echo "===== C服务器健康检查 ====="
        echo "--- Docker容器 ---"
        docker ps --format "table {{.Names}}\t{{.Status}}"
        echo "--- Grafana ---"
        curl -sf http://localhost:3000/api/health 2>/dev/null || echo "FAIL"
        echo "--- 磁盘 ---"
        df -h | grep -E 'Filesystem|/dev/vda'
        ;;
esac

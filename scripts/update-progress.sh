#!/bin/bash
# 每天早上运行，自动更新进度文件
# 用法: ./update-progress.sh

set -e

PROGRESS_FILE="/Users/kongyan/JQData/progress.md"
REMOTE_FILE="/data/jqdata-platform/progress.md"
D_SERVER="deploy@101.132.161.52"

echo "=== 更新进度文件 ==="

# 1. 获取 ClickHouse 数据量
echo "查询 ClickHouse 数据量..."
CH_DATA=$(ssh -o StrictHostKeyChecking=no $D_SERVER "docker exec jqdata-clickhouse clickhouse-client -q '
SELECT name, count() FROM system.tables WHERE database=\"jqdata\" AND name IN (\"stock_daily_pre\",\"stock_daily_post\",\"index_daily\",\"security_info\")
'" 2>/dev/null || echo "查询失败")

# 2. 获取 JQData 额度
echo "查询 JQData 额度..."
QUOTA=$(ssh -o StrictHostKeyChecking=no $D_SERVER "python3 -c '
import jqdatasdk as jq
jq.auth(\"18918601977\",\"Another123\")
q = jq.get_query_count()
print(f\"{q[\"spare\"]}/{q[\"total\"]}\")
'" 2>/dev/null || echo "查询失败")

# 3. 生成新日期
echo "更新日期标记..."
TODAY=$(date +%Y-%m-%d)

# 4. 替换进度文件中的日期
echo "写入本地进度文件..."
cat > "$PROGRESS_FILE" << EOF
# JQData 工作进度

> 每天早上打开看一下，了解当前状态、今天该做什么、明天计划。
> 数据更新时间：\`$TODAY\`

---

## 1. 数据同步状态

| 表 | 数据量 | 状态 | 说明 |
|------|--------|------|------|
| **security_info** | 7,838 条 | ✅ | 股票/ETF/指数基本信息 |
| **index_daily** | $([ -n "$CH_DATA" ] && echo "$CH_DATA" | grep index_daily | awk '{print $2}' || echo "查询中...") 行 | ✅ | 15 个指数，2020-2026 |
| **stock_daily_pre** | $([ -n "$CH_DATA" ] && echo "$CH_DATA" | grep stock_daily_pre | awk '{print $2}' || echo "查询中...") 行 | ⚠️ | 前复权股票日线 |
| **stock_daily_post** | $([ -n "$CH_DATA" ] && echo "$CH_DATA" | grep stock_daily_post | awk '{print $2}' || echo "查询中...") 行 | ❌ | 后复权股票日线 |

- 数据范围：2020-01-01 ~ $(date +%Y-%m-%d)（近 5 年多）
- JQData 额度：$QUOTA（每天早上重置）

---

## 2. 今日完成（$TODAY）

- [ ] 继续同步前复权剩余批次
- [ ] 继续同步后复权批次

---

## 3. 明天的计划

1. 检查 ClickHouse 数据完整性
2. 资产沃土接入方案实施（jqdata_benchmark_manager.py）

---

## 4. 快速命令

\`\`\`bash
# 查看同步日志
ssh deploy@101.132.161.52 "tail -n 30 /data/jqdata-platform/sync_*.log"

# 查看数据量
ssh deploy@101.132.161.52 "docker exec jqdata-clickhouse clickhouse-client -q 'SELECT name,count() FROM system.tables WHERE database=\"jqdata\"'"

# 查看额度
ssh deploy@101.132.161.52 "python3 -c 'import jqdatasdk as jq; jq.auth(\"18918601977\",\"Another123\"); print(jq.get_query_count())'"
\`\`\`

---

## 5. 注意事项

- ⚠️ D 服务器带宽 1Mbps，禁止在服务器上执行 docker build 或下载大文件
- ⚠️ 额度 1000 万/天，每天早上重置
- ⚠️ 同步脚本在 D 服务器 /data/jqdata-platform/src/sync_daily.py

---

*最后更新：$TODAY $(date +%H:%M)*
EOF

# 5. 同步到 D 服务器
echo "同步到 D 服务器..."
scp -o StrictHostKeyChecking=no "$PROGRESS_FILE" root@101.132.161.52:$REMOTE_FILE

echo "=== 进度文件已更新 ==="
echo "本地: $PROGRESS_FILE"
echo "远程: $REMOTE_FILE"

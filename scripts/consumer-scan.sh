#!/usr/bin/env bash
# consumer-scan.sh —— 共享口径字段消费点全量枚举（ai.plan.consumer_scan_by_command 的机检出口）
# 用法: consumer-scan.sh <字段名> [代码根目录，缺省当前目录]
# 输出: 按文件聚合的消费点清单 + 逐文件命中行数，供需求/方案文档"消费点清单"节直接引用。
# 注意: 结果不截断；文件清单必须逐文件人工判定"读什么/改不改"后才能写进文档。
set -euo pipefail
FIELD="${1:?用法: consumer-scan.sh <字段名> [代码根]}"
ROOT="${2:-.}"
echo "== consumer-scan: 字段[$FIELD] 根[$ROOT] =="
echo "-- 后端(.py, 排除 tests/migrations) --"
grep -rn "$FIELD" "$ROOT" --include='*.py' 2>/dev/null | grep -v "tests/\|migrations/" | awk -F: '{print $1}' | sort | uniq -c | sort -rn
echo "-- 前端(.ts/.tsx, 排除 test) --"
grep -rn "$FIELD" "$ROOT" --include='*.ts' --include='*.tsx' 2>/dev/null | grep -v "\.test\.\|__tests__" | awk -F: '{print $1}' | sort | uniq -c | sort -rn
echo "-- 跑批与脚本(cron/scripts) --"
grep -rn "$FIELD" "$ROOT/cron" "$ROOT/scripts" 2>/dev/null | awk -F: '{print $1}' | sort | uniq -c || true
echo "-- 命中总数(后端): $(grep -rn "$FIELD" "$ROOT" --include='*.py' 2>/dev/null | grep -cv "tests/\|migrations/" || true)"
echo "[提示] 文件级清单已列，逐文件判定读什么/改不改后方可入文档；禁止截断后下结论。"

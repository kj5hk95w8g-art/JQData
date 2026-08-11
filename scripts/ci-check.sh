#!/usr/bin/env bash
#
# JQData 契约冒烟测试（手动 / CI 调用）
# pre-commit 不跑本脚本（避免过慢），仅在需要时手动或 CI 执行。
#
set -e
cd "$(dirname "$0")/.."

echo "== JQData 契约冒烟测试 =="
python3 -m pytest tests/ -q

#!/usr/bin/env bash
#
# backup_truth_sources.sh 模板 —— gitignored 单点真源文件每日快照
# TEMPLATE_VERSION: 2
#
# 背景：env 配置 / 本地开发库等真源文件不入 git、单命令可覆盖且常无备份
#       （2026-09-11 沃土 .env.local/nav.db 误覆盖丢 4 个月数据，全局规则 013 来源）。
#
# 项目实例按需覆写（同步后本地修改文件头 OVERRIDE 区即可，sync 保留实例差异时
# 以人工合并为准）：TRUTH_FILES 清单、BACKUP_DIR、KEEP。
#
# 用法：./scripts/backup_truth_sources.sh（建议 crontab 每日一次）
# 保留：每类最近 KEEP 份（默认 7），超出自动清理
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backend/data/backups/truth_sources}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d_%H%M%S)"

# OVERRIDE 区（项目实例改这里）：相对 ROOT 的真源文件清单，支持 "db:" 前缀走 sqlite 在线快照
TRUTH_FILES="${TRUTH_FILES:-backend/.env.local backend/.env .env.local frontend/.env.local}"
TRUTH_DB="${TRUTH_DB:-backend/data/nav.db}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"   # 备份含 .env 密钥，限本用户访问

# sqlite 库：优先 .backup 在线一致快照（避免 wal 中间态）；锁冲突回退连 wal/shm 拷贝
if [ -n "$TRUTH_DB" ] && [ -f "$ROOT/$TRUTH_DB" ]; then
    db_name="$(basename "$TRUTH_DB")"
    if command -v sqlite3 >/dev/null 2>&1 \
        && sqlite3 "$ROOT/$TRUTH_DB" ".timeout 5000" ".backup '$BACKUP_DIR/$db_name.$STAMP'" 2>/dev/null; then
        :
    else
        for ext in "" "-wal" "-shm"; do
            cp -p "$ROOT/$TRUTH_DB$ext" "$BACKUP_DIR/$db_name.$STAMP$ext" 2>/dev/null || true
        done
    fi
fi

# 普通文件直拷（含权限）；跳过软链（软链指向的真身已单独登记）
for f in $TRUTH_FILES; do
    src="$ROOT/$f"
    [ -f "$src" ] && [ ! -L "$src" ] && cp -p "$src" "$BACKUP_DIR/$(echo "$f" | tr '/' '_').$STAMP"
done

# 轮转：每类只保留最近 KEEP 份
for prefix in "$(basename "$TRUTH_DB")" $(for f in $TRUTH_FILES; do echo "$f" | tr '/' '_'; done); do
    ls -t "$BACKUP_DIR/$prefix".* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f 2>/dev/null || true
done

echo "[backup_truth_sources] 快照完成: $BACKUP_DIR (保留最近 ${KEEP} 份)"

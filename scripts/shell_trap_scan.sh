#!/usr/bin/env bash
#
# shell_trap_scan.sh — shell 脚本陷阱扫描（全局唯一实现，2026-11-17 复盘产物）
# TEMPLATE_VERSION: 1
# @gate(shadow, id=shell-trap-scan)  影子期起步（gates.shadow_period）；转正须 gate_registry 校准记录
# 背景（2026-11-17 机制统一复盘，单日 4 次同类事故）：
#   M1 bash 变量名后紧跟全角字符（$VAR，/$f））——bash 将全角字符并入变量名解析，
#      unbound variable 或静默空值；单日踩 4 处（td_gate/ci-check×2/commit_msg_guard），
#      全量扫描两仓+全局层另有 17 处存量消息级潜伏
#   M2 git init --bare 未显式 -b <branch>——依赖 init.defaultBranch 全局配置，
#      本地过 CI 挂（裸仓 HEAD 指向不存在分支 → clone 出孤儿子分支）
#   M3 钩子文件尾句 "[ ... ] && exit N"——husky v9 以 sh -e 执行，尾句条件不成立
#      时整个钩子退出码非零（静默失败模式）
# 用法:
#   bash "$HOME/.ai-rules/enforcement/shell_trap_scan.sh"            # pre-commit：扫 staged .sh + 钩子尾句
#   bash "$HOME/.ai-rules/enforcement/shell_trap_scan.sh" check       # CI/手动：全仓扫描
# 约定（写死）: 扫描 .sh 文件；M1 跳过注释行；M1/M2 影子期 warn，发现即修
set -u
RC=0
GATE_STATE="shadow"
note() {
    if [ "$GATE_STATE" = "enforce" ]; then echo "❌ [shell-trap-scan] $*"; RC=2; else echo "⚠️  [warn][shell-trap-scan] $*"; RC=1; fi
}
MODE="${1:-pre-commit}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

scan_file() {  # $1=文件  M1/M2 行级扫描（跳过注释行）
    "$PYTHON_BIN" - "$1" <<'PY'
import re, sys
m1 = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]")
m2 = re.compile(r"init .*--bare")  # 正确用法须显式 -b <branch>，否则自命中（-b 豁免见下行判断）
path = sys.argv[1]
for i, line in enumerate(open(path, encoding="utf-8", errors="ignore"), 1):
    if line.lstrip().startswith("#"):
        continue
    if m1.search(line):
        print(f"M1 {path}:{i} {line.strip()[:90]}")
    if m2.search(line) and "-b " not in line:
        print(f"M2 {path}:{i} {line.strip()[:90]}")
PY
}

if [ "$MODE" = "check" ]; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    HITS=0
    while IFS= read -r f; do
        OUT=$(scan_file "$f") || true
        if [ -n "$OUT" ]; then echo "$OUT"; HITS=$((HITS+1)); fi
    done < <(find "$ROOT" -name "*.sh" -not -path "*/archive/*" -not -path "*/node_modules/*" -not -path "*/.git/*" 2>/dev/null)
    [ "$HITS" -gt 0 ] && note "全仓扫描 $HITS 个文件命中陷阱模式（详见上行）——修存量或登记 LOCAL_OVERRIDES"
    [ $RC -eq 0 ] && echo "✅ shell_trap_scan: 全仓扫描通过"
    exit $RC
fi

# pre-commit 模式：staged .sh（增量零容忍）
STAGED_SH=$(git diff --cached --name-only --diff-filter=ACMR | grep -E "\.sh$" || true)
if [ -n "$STAGED_SH" ]; then
    HITS=0
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        OUT=$(scan_file "$f") || true
        if [ -n "$OUT" ]; then echo "$OUT"; HITS=$((HITS+1)); fi
    done <<EOF2
$STAGED_SH
EOF2
    [ "$HITS" -gt 0 ] && note "staged 脚本 $HITS 个文件命中陷阱模式（详见上行）"
fi

# M3：钩子文件尾句 "[ ... ] && exit N"（husky v9 sh -e 下尾句条件不成立=钩子非零退出）
for h in .husky/commit-msg .husky/pre-commit .husky/post-merge scripts/hooks/pre-commit scripts/hooks/commit-msg scripts/hooks/pre-push; do
    [ -f "$h" ] || continue
    TAIL=$(grep -vE "^\s*$|^\s*#" "$h" 2>/dev/null | tail -1)
    case "$TAIL" in
        *"&& "exit*|*"&& exit"*)
            note "钩子尾句为「${TAIL}」——sh -e 下条件不成立即钩子非零退出（静默失败），末尾请用显式 exit 0"; RC=1 ;;
    esac
done

[ $RC -eq 0 ] && echo "✅ shell_trap_scan: 通过"
exit $RC

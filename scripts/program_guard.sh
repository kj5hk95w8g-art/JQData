#!/usr/bin/env bash
#
# program_guard.sh — 程序级规范守卫（模板唯一真源）
# TEMPLATE_VERSION: 1
# 规则: ~/.ai-rules 014-program-execution（2026-09-22 立）
# 行为: ①新增/修改 REQ 带 `program:` 字段但主办仓缺对应总纲 → block
#       ②新增/修改 docs/programs/*.md 总纲缺五节任一 → block
# 用法: 无参=扫 staged 文件(pre-commit)；显式传文件=直接检查(CI)
set -u
ERR=0; WARN=0
FILES=()
if [ $# -gt 0 ]; then FILES=("$@"); else
  while IFS= read -r f; do FILES+=("$f"); done < <(git diff --cached --name-only --diff-filter=AM | grep -E '(docs/requirements/.*\.md|docs/programs/.*\.md)$' || true)
fi
[ "${#FILES[@]}" -eq 0 ] && { echo "program_guard: 无涉程序文件，跳过"; exit 0; }
python3 - "${FILES[@]}" <<'PY'
import re, subprocess, sys
from pathlib import Path

req_files, charter_files = [], []
for f in sys.argv[1:]:
    p = Path(f)
    if not p.is_file() or p.name.startswith(("_", "TEMPLATE", "INDEX", "README")):
        continue
    if str(p).startswith("docs/programs/"):
        charter_files.append(p)
    elif str(p).startswith("docs/requirements/"):
        req_files.append(p)

def staged_or_exists(pattern: str) -> bool:
    """工作树或 staged 集合中存在匹配文件即算数（REQ 与总纲允许同一次提交）。"""
    r = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
    staged = set(r.stdout.split())
    for cand in Path("docs/programs").glob(pattern):
        if str(cand) in staged or cand.exists():
            return True
    return any(s.startswith("docs/programs/") and Path(s).match(pattern) for s in staged)

SECTIONS = ("目标与边界", "依赖图", "决策登记", "风险", "上游")

for p in charter_files:
    text = p.read_text(encoding="utf-8", errors="ignore")
    missing = [s for s in SECTIONS if not re.search(r"^#{1,4}[^\n]*" + s, text, re.M)]
    if missing:
        print(f"🚫 [block] {p}: 总纲缺必含节: {', '.join(missing)}（规则 program.charter 五节）")
        sys.exit(2)

for p in req_files:
    text = p.read_text(encoding="utf-8", errors="ignore")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        continue
    pm = re.search(r"^program:\s*([A-Za-z0-9-]+)\s*$", m.group(1), re.M)
    if not pm:
        continue
    prog = pm.group(1)
    if not staged_or_exists(f"{prog}-*.md"):
        print(f"🚫 [block] {p}: 声明 program: {prog} 但 docs/programs/{prog}-*.md 总纲不存在"
              f"（须同 commit 或先于 REQ 提交，规则 program.charter）")
        sys.exit(2)

sys.exit(0)
PY

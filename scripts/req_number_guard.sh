#!/usr/bin/env bash
#
# req_number_guard.sh — REQ 编号跨分支唯一性 提交守卫（模板唯一真源）
# TEMPLATE_VERSION: 3  # 2026-11-17: 文案指向全局发号器（机制统一收编）
# @gate(shadow, id=req-number-guard)  影子期起步（gates.shadow_period）；转正须 gate_registry 校准记录
# 规则: 项目 AGENTS.md「REQ 编号」条款 + REQ-168（2026-09-30 REQ-167 复盘：
#       只查 main 漏 develop 在途同号，hotfix 被迫改号返工）
#       + REQ-189 撞号复盘（2026-10-21：本地 refs 滞后致守卫失明——v1 设计
#       "pre-commit 不自动 fetch"在并行会话直踩集成区时漏检，取消该取舍）
# 行为: 新增 REQ 文件的编号，在以下任一处已有登记（不同文件名）→
#       shadow 期 warn（rc=1），enforce 期 block（rc=2）：
#       ① origin/main 与 origin/develop 的 docs/requirements/
#       ② 全部本地 worktree 的 docs/requirements/（含未提交在写文件）
#       ③ 认领台账 req-number-claims.json（claim_req_number.sh（全局：~/.ai-rules/enforcement/） 维护，
#          默认位于 git common dir 下——linked worktree 共享、git 不跟踪）
# 用法: 无参=扫 staged 新增文件(pre-commit)；显式传文件=直接检查(CI)。
# 依赖: v2 起守卫自带一次 `git fetch origin main develop`；失败降级为显式
#       warn 并按本地缓存 refs 比对——禁止静默用旧 refs（2026-10-21 复盘条款）。
set -u
# @gate(shadow, id=req-number-guard)
GATE_STATE="shadow"   # shadow | enforce —— enforce 需 docs/gates/gate_registry.yml 校准记录

ERR=0; WARN=0
FILES=()
if [ $# -gt 0 ]; then FILES=("$@"); else
    while IFS= read -r f; do FILES+=("$f"); done < <(git -c core.quotepath=false diff --cached --name-only --diff-filter=A | grep -E 'docs/requirements/.*\.md$' || true)
fi
[ "${#FILES[@]}" -eq 0 ] && { echo "req_number_guard: 无新增 REQ 文件，跳过"; exit 0; }

# v2: 新鲜度——尽力 fetch；失败显式告警（不静默）
if git fetch origin main develop --quiet 2>/dev/null; then
    echo "req_number_guard: refs 已刷新（fetch ok）"
else
    echo "⚠️  [warn] req_number_guard: git fetch 失败（离线？）——按本地缓存 refs 比对，可能失明（REQ-189 复盘条款）"
    WARN=$((WARN+1))
fi

# v2: 收集全部本地 worktree 的 docs/requirements（含未提交在写文件）
WT_DIRS=""
while IFS= read -r wt; do
    [ -n "$wt" ] && [ -d "$wt/docs/requirements" ] && WT_DIRS="$WT_DIRS $wt/docs/requirements"
done < <(git worktree list --porcelain | sed -n 's/^worktree //p')

GIT_COMMON="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
case "$GIT_COMMON" in /*) ;; *) GIT_COMMON="$(git rev-parse --show-toplevel)/$GIT_COMMON" ;; esac
LEDGER="$GIT_COMMON/req-number-claims.json"

# 参数: state ledger n_wt [wt_dirs...] [files...]
python3 - "$GATE_STATE" "$LEDGER" "${#WT_DIRS}" $WT_DIRS "${FILES[@]}" <<'PY'
import json, re, subprocess, sys
from pathlib import Path

state = sys.argv[1]
ledger_path = Path(sys.argv[2])
n_wt = int(sys.argv[3])
wt_dirs = [Path(x) for x in sys.argv[4:4 + n_wt]]
files = sys.argv[4 + n_wt:]

claimed = {}
if ledger_path.is_file():
    try:
        claimed = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  [warn] 认领台账不可读（{e}）——跳过台账核对")
else:
    print("req_number_guard: 无认领台账（claim_req_number.sh（全局：~/.ai-rules/enforcement/） 未启用？），跳过台账核对")

def remote_conflicts(num, f):
    for br in ("origin/main", "origin/develop"):
        try:
            out = subprocess.run(
                ["git", "-c", "core.quotepath=false", "ls-tree", "--name-only", br, "docs/requirements/"],
                capture_output=True, text=True, timeout=20).stdout
        except subprocess.SubprocessError:
            yield (br, "（本地引用缺失？）")
            continue
        for line in out.splitlines():
            other = line.strip()
            om = re.search(r"REQ-(\d+)", other)
            if om and om.group(1) == num and other.endswith(".md") and other != f:
                yield (br, other)

def worktree_conflicts(num, f):
    for d in wt_dirs:
        for p in sorted(d.glob(f"REQ-{num}-*.md")):
            if p.name != Path(f).name:
                wt_root = str(d)[:-len("/docs/requirements")] if str(d).endswith("/docs/requirements") else str(d)
                yield (f"worktree:{wt_root}", p.name)

def ledger_conflicts(num, f):
    ent = claimed.get(f"REQ-{num}")
    if isinstance(ent, dict):
        other_file = str(ent.get("file", ""))
        if other_file and Path(other_file).name != Path(f).name:
            yield ("认领台账", f"{ent.get('by', '?')} 于 {ent.get('claimed_at', '?')} 认领（{other_file}）")

rc = 0
for f in files:
    p = Path(f)
    if not p.is_file() or p.name.startswith(("_", "TEMPLATE", "INDEX", "README")):
        continue
    m = re.match(r"REQ-(\d+)", p.name)
    if not m:
        continue
    num = m.group(1)
    conflicts = list(remote_conflicts(num, f)) + list(worktree_conflicts(num, f)) + list(ledger_conflicts(num, f))
    for src, other in conflicts:
        msg = (f"{f}: 编号 REQ-{num} 已被占用——{src}（{other}）")
        if state == "enforce":
            print(f"🚫 [block] {msg}")
            print("   换下一个空闲编号：跑 ./scripts/claim_req_number.sh（全局：~/.ai-rules/enforcement/）（fetch + 全 worktree + 台账取 max+1）")
            sys.exit(2)
        print(f"⚠️  [shadow] {msg}（影子期只告警；建议立即改号，转正后此处为硬拦）")
        rc = 1
sys.exit(rc)
PY
rc=$?
[ $rc -eq 2 ] && echo "   （转正依据/校准记录见 docs/gates/gate_registry.yml）"
[ $rc -eq 1 ] && echo "   （影子期告警不阻断提交；重复触发请改号并反馈校准记录）"
exit $rc

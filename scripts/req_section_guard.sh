#!/usr/bin/env bash
#
# req_section_guard.sh — 跨项目影响声明节 提交守卫（模板唯一真源）
# TEMPLATE_VERSION: 4
# v4（2026-11-05，REQ-209 复盘）：窗口/保留类关键词 → 必填「信息生命周期表」节。
#   背景：REQ-209 拍板了"近30天已上市"窗口但无人问过退场条件，上线后用户发现
#   10 条已上市无关行稀释列表。教训：展示类需求每行信息既要有入场条件，也要有
#   退场条件与消费者；机检查结构与填写存在性，内容质量由评审抽查。
# 规则: ~/.ai-rules crossproject.impact_declaration（2026-09-22 立）
# 行为: 提交含跨项目关键词的新 REQ 文件但缺「跨项目影响」节 → block；
#       存量文件（规则入册前引入）→ warn 不拦；blocked 豁免同规则。
# 用法: 无参=扫 staged 新增文件(pre-commit)；显式传文件=直接检查(CI)。
set -u
RULE_BORN_LINE=""
ERR=0; WARN=0
FILES=()
if [ $# -gt 0 ]; then FILES=("$@"); else
  while IFS= read -r f; do FILES+=("$f"); done < <(git diff --cached --name-only --diff-filter=A | grep -E 'docs/requirements/.*\.md$' || true)
fi
[ "${#FILES[@]}" -eq 0 ] && { echo "req_section_guard: 无新增 REQ 文件，跳过"; exit 0; }
python3 - "$RULE_BORN_LINE" "${FILES[@]}" <<'PY'
import re, subprocess, sys
from datetime import datetime
from pathlib import Path
CROSS_KW = ("/api/external", "X-Internal-API-Key", "external-consumers", "upstream-deps",
            "216", "171", "沃土", "管家", "liu_workspace", "跨项目", "docs/handshake",
            "garden_sync", "sync_garden", "strategy_garden", "woodypan", "kong_workspace",
            "yuntuCenter", "strategy-live-216", "KREQ-035")
IMPACT_RE = re.compile(r"^#{1,4}[^\n]*跨项目影响", re.M)
# REQ-168 真源盘点闸状态（gates.shadow_period）：shadow=只告警；enforce 需 gate_registry 校准记录
GATE_STATE = "shadow"
# REQ-168 扩展（2026-09-30）：真源切换类关键词 → 必填「旧真源退役盘点」节。
# @gate(shadow, id=legacy-source-retirement)：影子期 warn，转正须 gate_registry 校准记录。
LEGACY_KW = ("旧真源", "真源切换", "数据真源", "旧表退役", "双账本", "落库表切换",
             "消费方清点", "subscription_records", "transaction_records")
LEGACY_RE = re.compile(r"^#{1,4}[^\n]*旧真源退役盘点", re.M)
# v4 扩展（2026-11-05，REQ-209 复盘）：窗口/保留类关键词 → 必填「信息生命周期表」节。
# 关键词刻意收窄为"时间窗/保留/退场"类强信号（日历/列表/展示太泛，全量 REQ 皆中会稀释门禁）。
# @gate(shadow, id=info-lifecycle-table)：影子期 warn，转正须 gate_registry 校准记录。
LIFECYCLE_KW = ("时间窗", "窗口期", "近30天", "近 30 天", "近7天", "近 7 天",
                "保留天数", "退场", "滚动出", "回看窗口", "生命周期")
LIFECYCLE_RE = re.compile(r"^#{1,4}[^\n]*信息生命周期表", re.M)
LIFECYCLE_BORN = "2026-11-05"  # 入册日：之后新增文件 block（enforce 期），存量 warn
rc_warn = False
try:
    from datetime import datetime as _dt
    born = _dt(2026, 9, 22).timestamp()  # 规则入册日期常量：禁止取 yml mtime（无关编辑会静默漂移存量豁免），与 check.py C8 同源
except OSError:
    born = datetime(2026, 9, 22).timestamp()
for f in sys.argv[2:]:
    p = Path(f)
    if not p.is_file() or p.name.startswith(("_", "TEMPLATE", "INDEX", "README")):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")

    def _intro_ts(path):
        try:
            out = subprocess.run(["git", "log", "--follow", "--diff-filter=A", "-1", "--format=%ct", "--", path],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
            return float(out) if out else path.stat().st_mtime
        except (ValueError, subprocess.SubprocessError, OSError):
            return path.stat().st_mtime

    # REQ-168 扩展（独立检查，2026-09-30）：真源切换关键词 → 必填「旧真源退役盘点」节
    # @gate(shadow)：入册日 2026-09-30 之后新增的文件将 block；影子期先只告警
    lhit = [k for k in LEGACY_KW if k in text]
    if lhit and not LEGACY_RE.search(text):
        # 影子期一律 warn；enforce 期入册日(2026-09-30)后新增文件 block、存量 warn
        if GATE_STATE == "shadow":
            where = "warn"
        else:
            where = "block" if _intro_ts(p) >= _dt(2026, 9, 30).timestamp() else "warn"
        msg = f"{f}: 命中真源切换关键词 {lhit[:3]} 但缺「旧真源退役盘点」节（规则 REQ-168）"
        if where == "block":
            print(f"🚫 [block] {msg}"); print("   补节: 旧源全部消费点清单(grep 复核)+逐点改/不改+理由"); sys.exit(2)
        print(f"⚠️  [shadow] {msg}（影子期只告警）"); rc_warn = True

    # v4 扩展（2026-11-05）：窗口/保留类关键词 → 必填「信息生命周期表」节
    # @gate(shadow)：入册日 2026-11-05 之后新增的文件将 block；影子期先只告警
    chit = [k for k in LIFECYCLE_KW if k in text]
    if chit and not LIFECYCLE_RE.search(text):
        if GATE_STATE == "shadow":
            cwhere = "warn"
        else:
            cwhere = "block" if _intro_ts(p) >= _dt(2026, 11, 5).timestamp() else "warn"
        msg = f"{f}: 命中窗口/保留类关键词 {chit[:3]} 但缺「信息生命周期表」节（REQ-209 复盘立规）"
        if cwhere == "block":
            print(f"🚫 [block] {msg}")
            print("   补节: 每个展示对象一行——入场条件/退场条件/消费者（谁用它做什么决策）三列；退场条件或消费者答不出的对象不展示")
            sys.exit(2)
        print(f"⚠️  [shadow] {msg}（影子期只告警）"); rc_warn = True

    # 跨项目影响声明（原有检查，逻辑不变）
    def _kw_in(kw, t):
        if kw.isdigit():
            import re as _re
            return _re.search(r"(?<![\w-])" + kw + r"(?![\w-])", t) is not None
        return kw in t
    hit = [k for k in CROSS_KW if _kw_in(k, text)]
    if not hit or IMPACT_RE.search(text):
        continue
    where = "block" if _intro_ts(p) >= born else "warn"
    msg = f"{f}: 命中跨项目关键词 {hit[:3]} 但缺「跨项目影响」节（规则 crossproject.impact_declaration）"
    if where == "block":
        print(f"🚫 [block] {msg}"); print("   补节: 消费关系/契约变更/对端配合点+死线/登记册/已核对握手表"); sys.exit(2)
    print(f"⚠️  [warn] {msg}（存量文件，建议补节）"); rc_warn = True

sys.exit(1 if rc_warn else 0)
PY

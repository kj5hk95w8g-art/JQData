#!/usr/bin/env bash
#
# design_review_gate.sh — 设计评审闸（模板唯一真源）
# TEMPLATE_VERSION: 1
# 规则: ~/.ai-rules 013-engineering-gates.gates.design_review（2026-10-17 立）
# 背景: 单模型自审有确认偏误（2026-10-17 管家信号链路方案四视角评审抓到
#       三处自审盲区后用户拍板：高风险改动强制多视角设计评审，机器保"没跑掉"）。
# 触发（两条）:
#   A 强拦级：新增 REQ 文档命中风险关键词 → 文档必须含「设计评审」节
#      （三视角结论+日期+消化标注），缺节 block（enforce 期）/ warn（影子期）。
#   B 提醒级：staged 代码触碰风险路径（migration/cron/对外接口）→ 一律 warn，
#      提示确认对应 REQ 已评审。B 不做强拦：commit→REQ 映射不可靠，强拦误伤大；
#      硬闸靠 A（需求登记是唯一入口）+ 定稿三表机检兜底。
# 评审节格式（机器可检三要素）:
#   ## 设计评审
#   - 日期：YYYY-MM-DD
#   - 视角：可靠性 / 数据口径 / 使用者（三词齐备，各 ≥1 条结论）
#   - 消化：每条问题标 接受|反驳|砍掉
# 质量边界: 机器只保证"该评的没跑掉"，不保证评得好——硬字段可抽查戳穿，
#           与 C11 定稿三表同哲学。
# 用法: 无参=扫 staged(pre-commit)；显式传文件=直接检查(CI)。
# 退出码: 0=通过/跳过  1=warn（影子期命中或 B 级提醒）  2=block（仅 enforce 期 A 级）
set -u

# @gate(shadow, id=design-review-gate)  影子期起步；转正须 gate_registry.yml 校准记录
GATE_STATE="shadow"
# 规则入册日：enforce 期后新增文件 block、存量（入册前引入）warn
RULE_BORN="2026-10-17"

python3 - "$GATE_STATE" "$RULE_BORN" "$@" <<'PY'
import re, subprocess, sys
from datetime import datetime
from pathlib import Path

GATE_STATE = sys.argv[1]
BORN = datetime.strptime(sys.argv[2], "%Y-%m-%d").timestamp()

# REQ 风险关键词（与 007/008/010/013/014 规则同源；影子期校准误报后增删）
RISK_KW = ("cron", "定时任务", "定时", "migration", "数据迁移", "新表", "CREATE TABLE",
           "/api/external", "216", "171", "跨项目", "口径", "真源", "新增路由",
           "对外接口", "自动化链路", "新引擎", "状态表")
# 风险代码路径（B 级提醒）
RISK_PATH_RE = re.compile(r"(^|/)database/migrations/|setup-[^/]*cron|routes/.*external|/api/external")

# 评审节三要素
SEC_RE = re.compile(r"^#{1,4}[^\n]*设计评审", re.M)
PERSPECTIVES = ("可靠性", "数据口径", "使用者")
DIGEST_RE = re.compile(r"接受|反驳|砍掉")
DATE_RE = re.compile(r"日期[：:]\s*\d{4}-\d{2}-\d{2}")

staged = [a for a in sys.argv[3:]] if len(sys.argv) > 3 else subprocess.run(
    ["git", "-c", "core.quotepath=off", "diff", "--cached", "--name-only"],
    capture_output=True, text=True).stdout.split()
if not staged:
    print("design_review_gate: 无 staged 文件，跳过")
    sys.exit(0)

new_reqs = []
if len(sys.argv) > 3:
    new_reqs = [f for f in staged if re.match(r"docs/requirements/.*\.md$", f)]
else:
    out = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--cached",
                         "--name-only", "--diff-filter=A"],
                         capture_output=True, text=True).stdout
    new_reqs = [f for f in out.split() if re.match(r"docs/requirements/.*\.md$", f)]

def born_ts(path):
    try:
        o = subprocess.run(["git", "log", "--follow", "--diff-filter=A", "-1",
                            "--format=%ct", "--", path],
                           capture_output=True, text=True, timeout=15).stdout.strip()
        return float(o) if o else Path(path).stat().st_mtime
    except (ValueError, subprocess.SubprocessError, OSError):
        return Path(path).stat().st_mtime

rc = 0; hits = []

# ── A 级：新增 REQ 命中风险关键词 → 必须含合规「设计评审」节 ──
for f in new_reqs:
    p = Path(f)
    if not p.is_file() or p.name.startswith(("_", "TEMPLATE", "INDEX", "README")):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    kw = [k for k in RISK_KW if k in text]
    if not kw:
        continue
    missing = []
    if not SEC_RE.search(text):
        missing.append("缺「设计评审」节")
    else:
        sec = text[SEC_RE.search(text).start():]
        cut = re.split(r"\n#{1,4} ", sec, 1)[0]   # 节范围到下一个标题
        miss_p = [x for x in PERSPECTIVES if x not in cut]
        if miss_p:
            missing.append(f"视角缺 {('、'.join(miss_p))}（须三视角齐备）")
        if not DATE_RE.search(cut):
            missing.append("缺「日期：YYYY-MM-DD」行")
        if not DIGEST_RE.search(cut):
            missing.append("缺消化标注（每条问题标 接受/反驳/砍掉）")
    if missing:
        hits.append(f"  ❌ {f}  命中关键词[{','.join(kw[:5])}]  {'；'.join(missing)}")
        if GATE_STATE == "enforce" and born_ts(f) >= BORN:
            rc = 2
        else:
            rc = max(rc, 1)

# ── B 级：staged 风险路径 → 提醒（永久 warn，不强拦）──
risky = [f for f in staged if RISK_PATH_RE.search(f)]
if risky:
    print(f"  ⚠️ 设计评审范围代码路径被触碰（提醒级，不拦）: {', '.join(risky[:5])}")
    print("     请确认对应 REQ 已含「设计评审」节（三视角+日期+消化）；尚未评审的先补评再定稿")
    rc = max(rc, 1)

if hits:
    print("design_review_gate: 设计评审闸命中（shadow=提醒，enforce=阻断）:")
    for h in hits:
        print(h)
    print("  评审做法：定稿前开 ≥3 视角（可靠性/数据口径/使用者）对抗评审，")
    print("  每视角 ≥1 条带 file:line 证据的结论，逐条标 接受/反驳/砍掉，写入本节。")
if rc == 0 and not risky and not hits:
    print("design_review_gate: 通过（无风险触发）")
sys.exit(rc)
PY

#!/usr/bin/env bash
# TEMPLATE_VERSION: 1
#
# ci_convergence_check.sh —— CI workflow 收敛巡检（2026-09-12 Actions 免费额度治理）
#
# 背景：管家 ci.yml 曾因单测试挂死烧满 360 分钟默认上限（单次 ~720 job-min），
# 且 push 风暴 + 无 concurrency 去重，把账户 2000 免费分钟/月烧穿。
# 本脚本机器执行收敛红线，防复发（check.py 巡检调用或本地手动跑）。
#
# 检查项（.github/workflows/ 下所有启用 workflow，*.disabled 跳过）：
#   1) 每个 job 必须声明 timeout-minutes 且 ≤ 60（防挂满默认 6h 上限）
#   2) 含 push/pull_request 触发的 workflow 必须声明顶层 concurrency
#      （连续 push 自动取消过期 run；CI 主门禁建议 group 按 ref + cancel-in-progress）
#   3) push 触发必须限定 branches（禁止裸 push: 全分支触发）
#
# 用法：bash scripts/ci_convergence_check.sh [仓库根目录，默认 .]
# 退出码：0 合规；1 存在不合规项；2 环境错误

set -u
ROOT="${1:-.}"
WF_DIR="$ROOT/.github/workflows"
[ -d "$WF_DIR" ] || { echo "ℹ️  无 .github/workflows，跳过"; exit 0; }

python3 - "$WF_DIR" <<'PYEOF'
import sys, glob, os, re

wf_dir = sys.argv[1]
try:
    import yaml
except ImportError:
    print("❌ 需要 PyYAML：pip install pyyaml"); sys.exit(2)

issues = []
files = [f for f in glob.glob(os.path.join(wf_dir, "*.yml"))
         + glob.glob(os.path.join(wf_dir, "*.yaml"))
         if not f.endswith(".disabled")]

for f in sorted(files):
    name = os.path.basename(f)
    try:
        doc = yaml.safe_load(open(f, encoding="utf-8")) or {}
    except Exception as e:
        issues.append(f"{name}: YAML 解析失败 {e}"); continue
    if not isinstance(doc, dict):
        continue

    # 'on' 键会被 yaml 解析为 True
    on = doc.get("on", doc.get(True, {})) or {}
    triggers = on if isinstance(on, dict) else {}
    has_push = bool(triggers.get("push"))
    has_pr = bool(triggers.get("pull_request"))
    has_ci_trigger = has_push or has_pr

    # 规则 2：有 push/PR 触发必须有 concurrency
    if has_ci_trigger and "concurrency" not in doc:
        issues.append(f"{name}: 有 push/pull_request 触发但缺顶层 concurrency")

    # 规则 3：push 必须限定 branches
    if has_push:
        push = triggers.get("push")
        if isinstance(push, dict) and not push.get("branches"):
            issues.append(f"{name}: push 触发未限定 branches（全分支触发）")

    # 规则 1：每个 job 必须有 timeout-minutes ≤ 60
    jobs = doc.get("jobs") or {}
    for jid, job in (jobs.items() if isinstance(jobs, dict) else []):
        if not isinstance(job, dict):
            continue
        tm = job.get("timeout-minutes")
        if tm is None:
            issues.append(f"{name}: job [{jid}] 缺 timeout-minutes")
        elif isinstance(tm, (int, float)) and tm > 60:
            issues.append(f"{name}: job [{jid}] timeout-minutes={tm} > 60")

if issues:
    print("❌ CI workflow 收敛检查未通过：")
    for i in issues:
        print(f"  - {i}")
    sys.exit(1)
print(f"✅ CI workflow 收敛检查通过（{len(files)} 个 workflow）")
PYEOF

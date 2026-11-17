#!/usr/bin/env python3
TEMPLATE_VERSION = 1  # 2026-10-19 补登（此前无标记，check.py T2 误报）
# -*- coding: utf-8 -*-
"""guards.sh 第 7/8 类检查的内嵌判定器（REQ-145）。
从 stdin 读 anchor_scan.py --format json 输出，stdout 打印 T1(block)/T2(warn) 结论：
退出码 2 = T1 命中（block），1 = 仅 T2 提醒，0 = 干净。
"""
import json
import re
import subprocess
import sys

CONSUMERS_DOC = "docs/reviews/anchor-consumers.md"


def staged_files():
    r = subprocess.run(["git", "diff", "--cached", "--name-only"],
                       capture_output=True, text=True)
    return [f for f in r.stdout.splitlines() if f.strip()]


def changed_lines(path):
    """staged diff 的新侧行号集合（-U0）。"""
    r = subprocess.run(["git", "diff", "--cached", "-U0", "--", path],
                       capture_output=True, text=True)
    lines = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", r.stdout, re.M):
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        lines.update(range(start, start + count))
    return lines


def main():
    data = json.load(sys.stdin)
    anchors = data["anchors"]
    func_ranges = {(f["file"], f["name"]): (f["start"], f["end"])
                   for f in data["functions"]}

    files = staged_files()
    doc_updated = CONSUMERS_DOC in files
    py_files = [f for f in files if f.endswith(".py")]

    t1_hits, t2_hits = [], []
    for path in py_files:
        lines = changed_lines(path)
        if not lines:
            continue
        # T1：变更行命中锚点定义区间
        for a in anchors:
            if a["file"] == path and any(a["start"] <= n <= a["end"] for n in lines):
                t1_hits.append(a)
        # T2：变更行落在某消费函数体内
        hit_funcs = set()
        for (f, name), (s, e) in func_ranges.items():
            if f == path and name not in hit_funcs and any(s <= n <= e for n in lines):
                hit_funcs.add(name)
        warned = {a["name"] for a in t2_hits}
        for a in anchors:
            if a["name"] in warned:
                continue
            if any(c["file"] == path and c["function"] in hit_funcs for c in a["consumers"]):
                t2_hits.append(a)

    for a in t2_hits:
        print(f"⚠️  [warn] 变更触及共享口径「{a['name']}」（{a['file']}）的消费函数体——"
              f"该口径全部消费点如下，改完请逐点核对同页/相关页是否仍一致：")
        for c in a["consumers"]:
            print(f"           - {c['file']}:{c['function']}")

    for a in t1_hits:
        print(f"❌ [block] 变更触及锚点函数「{a['name']}」（{a['file']}:{a['start']}-{a['end']}）定义区间")
        if doc_updated:
            print(f"ℹ️  已检测到 {CONSUMERS_DOC} 随本次提交更新，T1 放行（请确认逐点结论已填）")
        else:
            print(f"       须同提交更新 {CONSUMERS_DOC}（先跑 python3 scripts/anchor_scan.py --write-docs 刷新消费清单，再填逐点结论）")

    if t1_hits and not doc_updated:
        return 2
    return 1 if t2_hits else 0


if __name__ == "__main__":
    sys.exit(main())

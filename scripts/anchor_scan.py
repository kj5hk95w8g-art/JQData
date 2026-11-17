#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anchor_scan.py —— 共享口径锚点扫描器（REQ-145 模板，ADR-0018）

锚点集合由 ast 自动扫描生成，禁止手工登记（手工表复刻"靠人记得"的盲区）。
候选 = services/ 下顶层函数（非下划线开头、不在白名单）；
入册 = 被其他模块（routes/scripts 等全 python-backend，不限 services）引用的
文件数 >= 阈值。引用计数解析范围必须覆盖 services 之外——否则 T2 对 route
消费方失明（REQ-143 事故真实形态即 route 消费点）。

用法：
  anchor_scan.py --check              # 与快照比对，漂移退出码 1（ci-check 用）
  anchor_scan.py --write              # 重新生成快照（锚点集合变化时显式提交）
  anchor_scan.py --write-docs         # 刷新登记册自动区（人工结论区保留）
  anchor_scan.py --format json        # 输出 JSON（guards.sh T1/T2 判定用）

OVERRIDE（管家实例，sync 保留）：
  SCAN_ROOTS 默认 python-backend/；快照/白名单/登记册路径见下方常量。
"""
import ast
import json
import re
import os
import sys

TEMPLATE_VERSION = 1
THRESHOLD = 3  # 跨模块引用文件数 >= 3 入册；调值直接改这里，暂不做配置化（REQ-145 设计裁定）
SCAN_ROOTS = ("python-backend",)      # 引用计数解析范围；目录不存在时回退整个根
SNAPSHOT_REL = "scripts/anchor-symbols.generated.yaml"
IGNORE_REL = "scripts/anchor-ignore.txt"
DOCS_REL = "docs/reviews/anchor-consumers.md"
EXCLUDE_DIRS = {"__pycache__", "node_modules", "venv", ".git", "tests", "test"}

AUTO_BEGIN = "<!-- ANCHOR:AUTO:BEGIN 守卫自动刷新，勿手改 -->"
AUTO_END = "<!-- ANCHOR:AUTO:END -->"


def _scan_roots(root):
    roots = [os.path.join(root, r) for r in SCAN_ROOTS]
    roots = [r for r in roots if os.path.isdir(r)]
    return roots or [root]


def _py_files(root):
    out = []
    for base in _scan_roots(root):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    out.append(os.path.join(dirpath, fn))
    return out


def _function_ranges(tree):
    """全量函数行号区间（T2 判定依据）：顶层函数 + 类方法（记 qualname
    Class.method）——只取顶层会把类方法消费体漏进 <module>，T2 对方法体失明。"""
    funcs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{node.name}.{sub.name}"] = (sub.lineno, sub.end_lineno)
    return funcs


def _enclosing(funcs, lineno):
    for name, (start, end) in funcs.items():
        if start <= lineno <= end:
            return name
    return "<module>"


def scan(root, ignore_file=None):
    """返回 {'anchors': [...], 'functions': [...]}（相对路径）。"""
    root = os.path.abspath(root)
    ignore = set()
    if ignore_file and os.path.isfile(ignore_file):
        with open(ignore_file, encoding="utf-8") as f:
            ignore = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}

    files = _py_files(root)
    parsed = {}          # rel -> (tree, top_funcs)
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        parsed[os.path.relpath(path, root)] = (tree, _function_ranges(tree))

    # 候选锚点：services/ 下顶层函数（路径含 services 段），非下划线、非白名单
    candidates = {}      # name -> rel（重名取先到，属罕见，快照里可见）
    for rel, (tree, funcs) in parsed.items():
        if "services" not in rel.replace(os.sep, "/").split("/"):
            continue
        for fname in funcs:
            if "." in fname or fname.startswith("_") or fname in ignore:
                continue
            candidates.setdefault(fname, rel)

    # 引用计数：其他文件中出现该名字（Name/Attribute），消费点记 enclosing 函数
    consumers = {name: [] for name in candidates}   # name -> [(file, func)]
    for rel, (tree, funcs) in parsed.items():
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.Name) and node.id in candidates:
                hit = node.id
            elif isinstance(node, ast.Attribute) and node.attr in candidates:
                hit = node.attr
            if not hit:
                continue
            if rel == candidates[hit]:
                continue  # 定义文件自身不算跨模块引用
            consumers[hit].append((rel, _enclosing(funcs, node.lineno)))

    anchors = []
    for name, cons in consumers.items():
        files = sorted({c[0] for c in cons})
        if len(files) < THRESHOLD:
            continue
        rel = candidates[name]
        start, end = parsed[rel][1][name]
        anchors.append({
            "name": name, "file": rel, "start": start, "end": end,
            "ref_files": files,
            "consumers": [
                {"file": f, "function": fn}
                for f, fn in sorted(set(cons))
            ],
        })
    anchors.sort(key=lambda a: (a["file"], a["name"]))

    functions = [
        {"file": rel, "name": fn, "start": s, "end": e}
        for rel, (_t, funcs) in parsed.items()
        for fn, (s, e) in funcs.items()
    ]
    return {"anchors": anchors, "functions": functions}


# ---------- 快照（手写 YAML 子集，stdlib 自包含） ----------

def _key(a):
    return (a["name"], a["file"], tuple(f"{c['file']}:{c['function']}" for c in a["consumers"]))


def write_snapshot(root, result):
    path = os.path.join(root, SNAPSHOT_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        "# generated by anchor_scan.py --write（ADR-0018：自动生成，禁止手改）",
        f"# threshold: {THRESHOLD}",
    ]
    for a in result["anchors"]:
        lines.append(f"- name: {a['name']}")
        lines.append(f"  file: {a['file']}")
        lines.append(f"  start: {a['start']}")
        lines.append(f"  end: {a['end']}")
        lines.append("  consumers:")
        for c in a["consumers"]:
            lines.append(f"    - {c['file']}:{c['function']}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def read_snapshot(root):
    path = os.path.join(root, SNAPSHOT_REL)
    if not os.path.isfile(path):
        return set()
    entries, cur = set(), None

    def _flush():
        if cur:
            entries.add((cur[0], cur[1], tuple(cur[2])))

    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if ln.startswith("- name: "):
                _flush()
                cur = [ln[8:], None, []]
            elif ln.startswith("  file: ") and cur:
                cur[1] = ln[8:]
            elif ln.startswith("    - ") and cur:
                cur[2].append(ln[6:])
    _flush()
    return entries


def check_drift(root, result):
    old, new = read_snapshot(root), {_key(a) for a in result["anchors"]}
    added = new - old
    removed = old - new
    if not (added or removed):
        return True, []
    msgs = []
    for name, f, _c in sorted(added):
        msgs.append(f"  + {name} ({f}) 新入册")
    for name, f, _c in sorted(removed):
        msgs.append(f"  - {name} ({f}) 已出册")
    return False, msgs


# ---------- 登记册（自动区刷新，人工区保留） ----------

def _read_filled_cells(path):
    """从既有登记册读人工已填的 (锚点, 消费点) → (结论, 最后核对)。"""
    filled = {}
    if not os.path.isfile(path):
        return filled
    cur_anchor = None
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = re.match(r"^### (\S+)（", ln)
            if m:
                cur_anchor = m.group(1)
                continue
            m = re.match(r"^\| ([^|]+?) \| ([^|]*?) \| ([^|]*?) \|$", ln)
            if m and cur_anchor and not set(m.group(1)) <= {"-", " "} and "消费点" not in m.group(1):
                concl, date = m.group(2).strip(), m.group(3).strip()
                if concl and concl != "待填":
                    filled[(cur_anchor, m.group(1).strip())] = (concl, date or "-")
    return filled


def write_docs(root, result):
    path = os.path.join(root, DOCS_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    filled = _read_filled_cells(path)
    auto = [AUTO_BEGIN, f"<!-- 扫描时间基准：threshold={THRESHOLD}，消费清单由 anchor_scan.py --write-docs 刷新；人工已填结论列自动保留 -->", ""]
    for a in result["anchors"]:
        auto.append(f"### {a['name']}（{a['file']}:{a['start']}-{a['end']}）")
        auto.append("")
        auto.append("| 消费点 | 逐点结论（人填：改/不改+理由） | 最后核对 |")
        auto.append("|---|---|---|")
        for c in a["consumers"]:
            key = (a["name"], f"{c['file']}:{c['function']}")
            concl, date = filled.get(key, ("待填", "-"))
            auto.append(f"| {c['file']}:{c['function']} | {concl} | {date} |")
        auto.append("")
    auto.append(AUTO_END)

    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if AUTO_BEGIN in content and AUTO_END in content:
            pre = content.split(AUTO_BEGIN)[0]
            post = content.split(AUTO_END, 1)[1]
        else:
            pre, post = content, ""
    else:
        pre = (
            "# 共享口径锚点消费登记册（REQ-145）\n\n"
            "> 自动区由 `anchor_scan.py --write-docs` 刷新（勿手改）；"
            "锚点定义区间被改动时，T1 守卫要求本文件与代码同提交，"
            "逐点结论列必填（改/不改+理由），同页聚合的不改项须用户拍板。\n\n"
        )
        post = "\n## 人工结论（拍板记录）\n\n> 未经拍板的「不改」禁止写成口径声明（REQ-143 教训）。\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(pre + "\n".join(auto) + post)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="共享口径锚点扫描器（REQ-145）")
    ap.add_argument("--root", default=".", help="仓库根目录")
    ap.add_argument("--check", action="store_true", help="与快照比对，漂移退出码 1")
    ap.add_argument("--write", action="store_true", help="重新生成快照")
    ap.add_argument("--write-docs", action="store_true", help="刷新登记册自动区")
    ap.add_argument("--format", choices=["json"], help="输出 JSON（guards.sh 用）")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    result = scan(root, ignore_file=os.path.join(root, IGNORE_REL))

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if args.write:
        write_snapshot(root, result)
        print(f"✅ 快照已生成：{os.path.join(root, SNAPSHOT_REL)}（锚点 {len(result['anchors'])} 个）")
    if args.write_docs:
        write_docs(root, result)
        print(f"✅ 登记册自动区已刷新：{os.path.join(root, DOCS_REL)}")
    if args.check:
        ok, msgs = check_drift(root, result)
        if ok:
            print("✅ anchor: 锚点集合与快照一致")
            return 0
        print("❌ [block] anchor: 锚点集合漂移，须运行 anchor_scan.py --write 并随代码同提交更新快照与登记册：")
        print("\n".join(msgs))
        return 1
    if not (args.write or args.write_docs or args.check):
        print(f"锚点 {len(result['anchors'])} 个（threshold={THRESHOLD}）：")
        for a in result["anchors"]:
            print(f"  {a['name']}  {a['file']}:{a['start']}-{a['end']}  消费 {len(a['consumers'])} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

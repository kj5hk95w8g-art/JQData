#!/usr/bin/env bash
#
# quality_patrol.sh 模板 —— 每周代码质量巡检（verdict 输出，不直发飞书）
# TEMPLATE_VERSION: 4  # 2026-11-16 v4: 双真源检测加 PATROL_DUAL_LEDGER 开关（管家账本合法路径即 docs/technical-debt/，默认 1 保持沃土行为）
#
# 机制（014 巡检统一方案 / inspection_registry.yml 登记）：
#   每周一跑，结论写 <项目>/docs/patrol/weekly-verdict-YYYY-MM-DD.md，
#   由 check_daily 每日简报汇总带出；本脚本绝不直接推飞书。
#
# 检查项（全部只报告，不改任何代码）：
#   1. 守卫接线完整性  —— 分发到 scripts/ 的守卫是否被 ci-check.sh / .githooks 引用（防掉线）
#   2. 死代码扫描      —— PATROL_CODE_DIRS 下定义但全库零引用的顶层函数（启发式，人工裁决）
#   3. 文档漂移        —— Feature 文档「最后同步代码版本」落后于最新 tag 的清单
#   4. God file 增长   —— >800 行文件清单 + 对比上次巡检快照的增长量
#   5. 分支卫生        —— 已合并未删分支数（有 branch-manager.sh 则用它）
#   6. 技术债账本一致性 —— technical-debt/ 与 reviews/tech-debt-review-* 双真源检测
#
# 环境变量（可选覆盖）：
#   PATROL_CODE_DIRS   死代码扫描目录（空格分隔，默认 backend/routes backend/services）
#   PATROL_GOD_LINES   God file 行数阈值（默认 800）
#   PATROL_KEEP_WEEKS  结论文件保留周数（默认 8）
#
# 退出码：0=PASS/WARN（巡检本身跑完），2=巡检自身故障（ERROR）。
#   死代码/漂移等发现一律 WARN，不置 ERROR——巡检是发现层，裁决在人工。

set -o pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 2
OUT_DIR="docs/patrol"
DATE="$(date +%Y-%m-%d)"
VERDICT="$OUT_DIR/weekly-verdict-$DATE.md"
CODE_DIRS="${PATROL_CODE_DIRS:-backend/routes backend/services}"
GOD_LINES="${PATROL_GOD_LINES:-800}"
KEEP_WEEKS="${PATROL_KEEP_WEEKS:-8}"
DUAL_LEDGER_CHECK="${PATROL_DUAL_LEDGER:-1}"   # 1=检测沃土式双真源；0=项目账本合法路径即 docs/technical-debt/（管家）
STATUS="PASS"
declare -a WARN_ITEMS=()

warn() { WARN_ITEMS+=("$1"); STATUS="WARN"; }

# ---------- 1. 守卫接线完整性 ----------
GUARDS="guards.sh gate_shadow_guard.sh terminology_guard.sh req_number_guard.sh req_section_guard.sh design_review_gate.sh program_guard.sh"
while read -r g; do
    [ -f "scripts/$g" ] || continue
    if ! grep -qr "$g" scripts/ci-check.sh .githooks/ 2>/dev/null; then
        warn "守卫掉线：scripts/$g 存在但未被 ci-check.sh / .githooks 引用"
    fi
done <<< "$GUARDS"

# ---------- 2. 死代码扫描（启发式） ----------
# 顶层 def，全库标识符一次性预收集后比对（O(n) 单遍 grep；逐函数全库 grep 是 O(n²) 不可接受）。
# 误报已知形态：动态调用、字符串引用——所以只列清单，人工裁决。
DEAD_LIST=""
ALL_IDS="$(mktemp)"
# 只从非 def 行收集标识符（排除定义本身，否则每个函数都能"找到自己"）
grep -rhvE '^[[:space:]]*(async )?def [A-Za-z_]' --include='*.py' --include='*.ts' --include='*.tsx' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=__pycache__ . 2>/dev/null \
    | grep -oE '[A-Za-z_][A-Za-z0-9_]*' | sort -u > "$ALL_IDS"
for d in $CODE_DIRS; do
    [ -d "$d" ] || continue
    # 装饰器注册型函数（def 行上方紧邻 @ 行 = 路由/依赖注入/命令注册）不被按名调用是正常的，
    # 零引用≠死代码，剔除（向上最多看 3 行连续装饰器）
    while IFS= read -r loc; do
        f="${loc%%:*}"
        [ -f "$f" ] || continue
        lineno="${loc#*:}"; lineno="${lineno%%:*}"
        skip=0; k=$lineno
        while [ "$k" -gt 1 ] && [ $((lineno-k)) -lt 3 ]; do
            k=$((k-1))
            dl="$(sed -n "${k}p" "$f")"
            case "${dl#"${dl%%[![:space:]]*}"}" in
                @*) skip=1; break ;;
                *) break ;;
            esac
        done
        [ $skip -eq 1 ] && continue
        fn="$(echo "$loc" | sed -E 's/^[^:]+:[0-9]+:[[:space:]]*(async[[:space:]]+)?def[[:space:]]+([A-Za-z_][A-Za-z0-9_]*).*/\2/')"
        # 提取失败（fn 含非标识符字符=整行残留）一律跳过，宁漏报勿误报
        case "$fn" in ''|*[!A-Za-z0-9_]*) continue ;; esac
        case "$fn" in _*|main|register_*|create_*|init_*) continue ;; esac  # 内部/框架钩子跳过
        # 预收集集含该名字 = 全库某处出现（保守跳过——宁漏报勿误报）
        grep -qxF "$fn" "$ALL_IDS" && continue
        DEAD_LIST+="- $f 的函数 \`$fn\`：全库零引用"$'\n'
    done < <(grep -rnE '^[[:space:]]*(async )?def [A-Za-z_]' "$d" --include='*.py' 2>/dev/null | head -2000)
done
rm -f "$ALL_IDS"
[ -n "$DEAD_LIST" ] && warn "死代码候选（启发式，人工裁决，勿直接删）"$'\n'"$DEAD_LIST"

# ---------- 3. 文档漂移（Feature 文档版本落后于最新 tag） ----------
LATEST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
DRIFT_LIST=""
if [ -n "$LATEST_TAG" ] && [ -d docs/features ]; then
    while IFS= read -r line; do
        doc="${line%%:*}"
        ver="$(echo "$line" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
        [ -n "$ver" ] || continue
        if [ "$ver" != "$LATEST_TAG" ] && [ "$(printf '%s\n%s\n' "$ver" "$LATEST_TAG" | sort -V | head -1)" = "$ver" ]; then
            DRIFT_LIST+="- $doc 标注 ${ver}（当前 ${LATEST_TAG}）"$'\n'
        fi
    done < <(grep -rn "最后同步代码版本" docs/features --include='*.md' 2>/dev/null | head -200)
fi
[ -n "$DRIFT_LIST" ] && warn "Feature 文档版本漂移（需按 doc.code_consistency 重验）"$'\n'"$DRIFT_LIST"

# ---------- 4. God file 增长 ----------
SNAPSHOT="$OUT_DIR/godfile-snapshot.txt"
FIND_SRC() { find backend frontend/src \( -name venv -o -name .venv -o -name env -o -name node_modules -o -name __pycache__ -o -name dist \) -prune -o -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \) -print 2>/dev/null; }
GOD_LIST=""
while IFS= read -r f; do
    lines=$(wc -l < "$f" | tr -d ' ')
    prev=$(grep -F "|$f|" "$SNAPSHOT" 2>/dev/null | cut -d'|' -f2)
    growth=""
    [ -n "$prev" ] && [ "$lines" -gt "$prev" ] && growth="（上次 $prev → 增 $((lines-prev)) 行）"
    GOD_LIST+="- ${f}：${lines} 行${growth}"$'\n'
done < <(FIND_SRC \
    | grep -vE '\.test\.|\.spec\.' \
    | xargs wc -l 2>/dev/null | awk -v n="$GOD_LINES" '$1>n && $2!="total" {print $2}' | sort)
if [ -n "$GOD_LIST" ]; then
    warn "God file（>${GOD_LINES} 行，持续增长者为拆分候选）"$'\n'"$GOD_LIST"
    mkdir -p "$OUT_DIR"
    FIND_SRC \
        | grep -vE '\.test\.|\.spec\.' \
        | xargs wc -l 2>/dev/null | awk -v n="$GOD_LINES" '$1>n && $2!="total" {print "|" $1 "|" $2 "|"}' \
        | sort -t'|' -k3 > "$SNAPSHOT"
fi

# ---------- 5. 分支卫生 ----------
if [ -f scripts/branch-manager.sh ]; then
    MERGED=$(bash scripts/branch-manager.sh --dry-run 2>/dev/null | grep -cE '将删除|可删除' || true)
else
    MERGED=$(git branch --merged origin/develop 2>/dev/null | grep -cvE 'develop|main|\*' || true)
fi
[ "${MERGED:-0}" -gt 0 ] && warn "已合并未删分支 $MERGED 条（跑 branch-manager.sh 清理）"

# ---------- 6. 技术债账本一致性 ----------
if [ "$DUAL_LEDGER_CHECK" = "1" ] && [ -d docs/technical-debt ] && ls docs/reviews/tech-debt-review-*.md >/dev/null 2>&1; then
    warn "技术债双真源：docs/technical-debt/ 与 docs/reviews/tech-debt-review-*.md 并存（gates.tech_debt_ledger 要求唯一账本，需合并）"
fi

# ---------- 写结论文件（verdict 纪律：不直发飞书，简报汇总带出） ----------
mkdir -p "$OUT_DIR"
{
    echo "# 每周质量巡检结论 $DATE"
    echo
        echo "- verdict: **${STATUS}**"
    echo "- runner: quality_patrol.sh (TEMPLATE_VERSION 1)"
    echo "- 范围: $CODE_DIRS / docs/features / 分支 / 技术债账本"
    echo
    if [ "${#WARN_ITEMS[@]}" -gt 0 ]; then
        echo "## 发现（${STATUS}）"
        echo
        for w in "${WARN_ITEMS[@]}"; do echo "$w"; echo; done
    else
        echo "## 发现"
        echo
        echo "无 WARN 项。"
    fi
} > "$VERDICT"

# 8 周轮转
find "$OUT_DIR" -name 'weekly-verdict-*.md' -mtime +"$((KEEP_WEEKS*7))" -delete 2>/dev/null

echo "巡检完成：$STATUS → $VERDICT"
[ "$STATUS" = "PASS" ] && exit 0 || exit 0

#!/usr/bin/env bash
#
# terminology_guard.sh 模板 —— UI 禁词扫描（CONTEXT.md _Avoid_ 登记表驱动）
# TEMPLATE_VERSION: 2
#
# 机制（gates.shadow_period）：默认影子模式只报不拦；校准无误报、拍板转正后
#   TERMINOLOGY_GUARD_MODE=enforce 才 block。转正需 docs/gates/gate_registry.yml 登记 + calibration 记录。
#
# 真源：项目根 CONTEXT.md 术语表中的 `_Avoid_:` 行（逗号/、分隔的弃用词）。
#   加禁词 = 改 CONTEXT.md 一处，不需要改本脚本、不需要改任何 rules。
#
# 用法：
#   bash terminology_guard.sh                # 默认扫描 git staged 文件（pre-commit 用）
#   bash terminology_guard.sh <file...>      # 显式文件列表（CI 用）
#
# 扫描范围：.ts/.tsx/.js/.jsx/.py 中的用户可见文案命中禁词。
#   注释豁免启发式（影子期校准对象）：行首修剪后以 //、#、*、/* 开头的行跳过。

set -o pipefail  # macOS bash3.2 下空数组+set -u 误报 unbound

MODE="${TERMINOLOGY_GUARD_MODE:-shadow}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CTX="$ROOT/CONTEXT.md"

if [ ! -f "$CTX" ]; then
    exit 0  # 无术语表的项目自动跳过（模板分发到全部项目，无 CONTEXT.md 者零成本）
fi

# ---------- 收集扫描文件 ----------
if [ $# -gt 0 ]; then
    FILES=("$@")
else
    # 测试文件按设计引用禁词做断言，排除
    # bash3.2 兼容（2026-10-01 修复）：mapfile 是 bash4+，macOS 自带 bash 3.2 下
    # "command not found" 导致 FILES 未定义，set -u 直接崩（管家 husky 直调场景实测）。
    # 改 while-read 收集，语义等价。
    FILES=()
    while IFS= read -r _f; do FILES+=("$_f"); done < <(
        git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '\.(ts|tsx|js|jsx|py)$' | grep -vE '(__tests__/|\.test\.|\.spec\.)' || true)
fi
[ ${#FILES[@]} -eq 0 ] && exit 0

# ---------- 解析机读禁词表（CONTEXT.md「UI 禁词」fenced block；_Avoid_ 散文仅供人读不解析） ----------
declare -a BANNED=()
while IFS= read -r line; do
    line="$(printf '%s' "$line" | sed -e 's/、/ /g' -e 's/，/ /g' -e 's/,/ /g')"
    for w in $line; do
        # 只收中文词（≥3 字节）或 ≥3 字符的英文标识，避免 size/sm 一类通用 token 误报
        [ "${#w}" -ge 3 ] && BANNED+=("$w")
    done
done < <(awk '/机读禁词表/{f=1} f && /^```/{c++; next} f && c==1' "$CTX")
[ ${#BANNED[@]} -eq 0 ] && exit 0

# ---------- 扫描 ----------
hits=0
for f in "${FILES[@]}"; do
    [ -f "$ROOT/$f" ] || continue
    lineno=0
    while IFS= read -r line; do
        lineno=$((lineno+1))
        line="${line%%//*}"   # 剥行尾 // 注释（shadow 期校准点：字符串含 https:// 时需白名单）
        [ -n "$line" ] || continue
        trimmed="${line#"${line%%[![:space:]]*}"}"
        case "$trimmed" in
            '//'*) continue ;;
            '#'*) continue ;;
            '*'*) continue ;;
            '/*'*) continue ;;
        esac
        # 只查含引号特征的行（近似用户可见文案；纯标识符行跳过）
        case "$line" in
            *'"'*|*"'"*|*'`'*) ;;
            *) continue ;;
        esac
        # bash3.2+set -u 兼容（2026-10-01 修复）：空数组展开 "${BANNED[@]}" 在
        # bash3.2 部分补丁级下报 unbound variable（沃土显式文件模式实测），
        # ${ARR[@]+...} 惯用法安全展开
        for w in ${BANNED[@]+"${BANNED[@]}"}; do
            if [[ "$line" == *"$w"* ]]; then
                hits=$((hits+1))
                echo "  ✗ [$f:$lineno] 禁词「${w}」：${trimmed:0:80}"
            fi
        done
    done < "$ROOT/$f"
done

if [ $hits -gt 0 ]; then
    if [ "$MODE" = "enforce" ]; then
        echo "❌ [terminology_guard/enforce] UI 禁词 $hits 处（登记表：CONTEXT.md _Avoid_）"
        exit 1
    fi
    echo "⚠️  [terminology_guard/shadow] UI 禁词 $hits 处（影子期只报不拦；登记表：CONTEXT.md _Avoid_）"
fi
exit 0

#!/usr/bin/env bash
#
# guards.sh 模板 —— 通用提交守卫（block / warn 两级）
# TEMPLATE_VERSION: 2
#
# 用法：
#   bash guards.sh                 # 默认扫描 git staged 文件（pre-commit 用）
#   bash guards.sh <file...>       # 显式传入文件列表（CI 用 diff 文件，如 git diff --name-only）
#
# 自包含、无外部依赖（bash 内建 + git）。输出中文，明确标注 block / warn。
#
# 三类 block 检查 + 一类 warn 检查 + 一类可选 block：
#   1. [block] 代码文件（.py/.ts/.tsx/.js/.sh）出现 /Users/ 硬编码绝对路径
#   2. [block] .py 中 execute(f"...") / f"SELECT|INSERT|UPDATE|DELETE" 拼 SQL
#              （值拼接；纯占位符串拼接如 ','.join('?'*n) 不算）
#   3. [block] 疑似硬编码密钥 (api_key|secret|token|password) = "xxxxxxxx"
#              （排除 .env*；排除测试桩值 test/fake/dummy/example）
#   4. [warn ] diff 触及 backend/routes、frontend/src、python-backend/routes、src/
#              但未触及 docs/ —— 提醒同步文档（不阻塞）
#   5. [block·可选] 路由文件行数门禁：ROUTE_GLOB 非空时启用，超 ROUTE_MAX_LINES
#              行即阻塞（防业务逻辑回流 route 层；管家 v3.3.24 Phase 3 下沉后固防）
#
# 注意：本文件是 ~/.ai-rules/enforcement/ 的模板源。项目实例由 sync-templates.sh
# 同步；有本地扩展的项目（如管家 SQL 豁免标记）同步时人工 diff 合并，勿整文件覆盖。
# check.py 巡检会比较 TEMPLATE_VERSION，实例落后会有提醒。

# ---------- 文件列表 ----------
if [ $# -gt 0 ]; then
    FILES=("$@")
else
    FILES=()
    while IFS= read -r _f; do
        [ -n "$_f" ] && FILES+=("$_f")
    done < <(git diff --cached --name-only 2>/dev/null || true)
fi

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "✅ guards: 无待检查文件"
    exit 0
fi

BLOCKED=0

# ---------- 检查 1：代码文件硬编码 /Users/ 绝对路径 ----------
echo "🔍 [block] 扫描代码文件硬编码绝对路径（/Users/）..."
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '\.(py|ts|tsx|js|sh)$' || continue
    basename "$f" | grep -qx 'guards.sh' && continue  # 豁免守卫脚本自身（含检查模式字面量）
    [ -f "$f" ] || continue
    HITS=$(grep -nE '/Users/' "$f" || true)
    if [ -n "$HITS" ]; then
        BLOCKED=1
        echo "❌ [block] $f 含硬编码 /Users/ 绝对路径（应改用环境变量或 ~）："
        echo "$HITS" | sed 's/^/          /'
    fi
done

# ---------- 检查 2：Python f-string 拼 SQL ----------
echo "🔍 [block] 扫描 Python f-string 拼 SQL..."
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '\.py$' || continue
    [ -f "$f" ] || continue
    HITS=$(grep -nE "execute\(f[\"']|f[\"'](SELECT|INSERT|UPDATE|DELETE)" "$f" || true)
    if [ -n "$HITS" ]; then
        BLOCKED=1
        echo "❌ [block] $f 使用 f-string 拼 SQL（可能引入 SQL 注入，应使用 ? 占位符）："
        echo "$HITS" | sed 's/^/          /'
    fi
done

# ---------- 检查 3：疑似硬编码密钥 ----------
echo "🔍 [block] 扫描疑似硬编码密钥..."
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '\.env' && continue      # 排除 .env*
    [ -f "$f" ] || continue
    HITS=$(grep -inE "(api_key|secret|token|password)[\"']?[[:space:]]*=[[:space:]]*[\"'][^\"']{8,}[\"']" "$f" \
        | grep -viE "=[[:space:]]*[\"'][^\"']*(test|fake|dummy|example)[^\"']*[\"']" || true)
    if [ -n "$HITS" ]; then
        BLOCKED=1
        echo "❌ [block] $f 疑似硬编码密钥（应改用环境变量）："
        echo "$HITS" | sed 's/^/          /'
    fi
done

# ---------- warn：改代码未动 docs/ ----------
TOUCHES_CODE=false
TOUCHES_DOCS=false
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '^(backend/routes/|frontend/src/|python-backend/routes/|src/)' && TOUCHES_CODE=true
    echo "$f" | grep -qE '^docs/' && TOUCHES_DOCS=true
done
if [ "$TOUCHES_CODE" = true ] && [ "$TOUCHES_DOCS" = false ]; then
    echo "⚠️  [warn] diff 触及 backend/routes、frontend/src、python-backend/routes 或 src/，但未触及 docs/"
    echo "         如涉及功能/接口变更，请同步更新 docs/ 下对应文档（005-documentation 红线）"
fi

# ---------- 可选：路由文件行数门禁 ----------
# 路径与阈值变量化：项目实例按需覆写（例：管家 ROUTE_GLOB='python-backend/routes/*.py' ROUTE_MAX_LINES=800）
# 模板默认关闭（ROUTE_GLOB 为空）。检查对象是 diff 触及的文件，不是全量扫描，保持 pre-commit 轻量。
ROUTE_GLOB="${ROUTE_GLOB:-}"
ROUTE_MAX_LINES="${ROUTE_MAX_LINES:-800}"
if [ -n "$ROUTE_GLOB" ]; then
    for f in "${FILES[@]}"; do
        # 用 bash glob 匹配（case 语法对 ** 不友好，这里做前缀+通配匹配）
        case "$f" in
            $ROUTE_GLOB)
                [ -f "$f" ] || continue
                LINES=$(wc -l < "$f" | tr -d ' ')
                if [ "$LINES" -gt "$ROUTE_MAX_LINES" ]; then
                    # 净增量判断：本次 diff 在缩文件则放行（鼓励渐进瘦身，不惩罚减行提交）
                    ADDED=$(git diff --cached --numstat -- "$f" 2>/dev/null | awk '{print $1}')
                    REMOVED=$(git diff --cached --numstat -- "$f" 2>/dev/null | awk '{print $2}')
                    NET=$(( ${ADDED:-0} - ${REMOVED:-0} ))
                    if [ "$NET" -gt 0 ]; then
                        BLOCKED=1
                        echo "❌ [block] $f 共 ${LINES} 行（超上限 ${ROUTE_MAX_LINES}）且本次净增 ${NET} 行"
                        echo "          （业务逻辑应下沉 services/，route 层只做 HTTP 装配；门禁可经 ROUTE_MAX_LINES 调整）"
                    else
                        echo "ℹ️  $f 共 ${LINES} 行仍超上限 ${ROUTE_MAX_LINES}，但本次为净减行，放行"
                    fi
                fi
                ;;
        esac
    done
fi

# ---------- 汇总 ----------
if [ "$BLOCKED" -eq 1 ]; then
    echo ""
    echo "❌ guards: 存在阻塞项，请修复后重新提交"
    exit 1
fi

echo "✅ guards: 检查通过"
exit 0

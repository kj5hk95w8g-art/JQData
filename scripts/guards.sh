#!/usr/bin/env bash
#
# guards.sh 模板 —— 通用提交守卫（block / warn 两级）
# TEMPLATE_VERSION: 6
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
#   6. [warn ] 体积债预警（God file 防复发）：diff 触及的源码文件超
#              SIZE_WARN_THRESHOLD 行（默认 800）且本次净增行 → 提醒拆分
#              （不阻塞；净减行放行，鼓励渐进瘦身）
#   7. [block·可选] 共享口径锚点 T1 硬拦截（REQ-145/ADR-0018）：diff 命中锚点
#              函数定义区间 → 须同提交更新 docs/reviews/anchor-consumers.md，
#              否则阻断（staged 模式专用；探测到 scripts/anchor_scan.py 与
#              快照存在才启用，同 ROUTE_GLOB 可选模式）
#   8. [warn ] 共享口径 T2 软提醒：diff 命中引用锚点的消费函数体 → 打印该口径
#              全部消费点与"同页是否一致"提示（REQ-143 实际形态是消费方变更，
#              只做 T1 拦不住）
#
#   9. [block·可选] 硬闸影子期制度（2026-09-23 拍板）：diff 新增 @gate(enforce, id)
#              标注必须在 docs/gates/gate_registry.yml 有条目且含 calibration 校准记录，
#              否则阻断；@gate(shadow, id) 未登记仅 warn（scripts/gate_shadow_guard.sh
#              与 docs/gates/gate_registry.yml 同时存在才启用）。
#
# 注意：本文件是 ~/.ai-rules/enforcement/ 的模板源。项目实例由 sync-templates.sh
# 同步；有本地扩展的项目（如管家 SQL 豁免标记）同步时人工 diff 合并，勿整文件覆盖。
# check.py 巡检会比较 TEMPLATE_VERSION，实例落后会有提醒。

# ---------- 文件列表 ----------
EXPLICIT_MODE=0
if [ $# -gt 0 ]; then
    EXPLICIT_MODE=1
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

# ---------- 体积债预警（God file 防复发，warn 不阻塞） ----------
# 阈值可经环境变量覆盖：SIZE_WARN_THRESHOLD=600 bash guards.sh
SIZE_WARN_THRESHOLD="${SIZE_WARN_THRESHOLD:-800}"
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '\.(py|ts|tsx)$' || continue
    echo "$f" | grep -qE '(^|/)(tests?|__pycache__|venv|node_modules)/' && continue
    basename "$f" | grep -qx 'guards.sh' && continue
    [ -f "$f" ] || continue
    LINES=$(wc -l < "$f" | tr -d ' ')
    if [ "$LINES" -gt "$SIZE_WARN_THRESHOLD" ]; then
        ADDED=$(git diff --cached --numstat -- "$f" 2>/dev/null | awk '{print $1}')
        REMOVED=$(git diff --cached --numstat -- "$f" 2>/dev/null | awk '{print $2}')
        NET=$(( ${ADDED:-0} - ${REMOVED:-0} ))
        if [ "$NET" -gt 0 ]; then
            echo "⚠️  [warn] $f 共 ${LINES} 行（超 ${SIZE_WARN_THRESHOLD}）且本次净增 ${NET} 行——建议顺手拆分，避免 God file 复发（项目技术债账本如有登记请回链）"
        fi
    fi
done

# ---------- 共享口径锚点守卫（REQ-145，T1 block / T2 warn，可选启用） ----------
# 探测到扫描器 + 快照才启用（staged 模式专用：判定依赖 git diff --cached）
# 锚点判定固定读 git diff --cached，仅在 staged 模式（pre-commit）运行，显式传参（CI）模式跳过
if [ "$EXPLICIT_MODE" -eq 0 ] && [ -f "scripts/anchor_scan.py" ] && [ -f "scripts/anchor-symbols.generated.yaml" ] && command -v python3 >/dev/null 2>&1; then
    echo "🔍 [block/warn] 共享口径锚点守卫（T1 锚点定义区间 / T2 消费函数体）..."
    python3 scripts/anchor_scan.py --root . --format json 2>/dev/null \
        | python3 scripts/anchor_guard_check.py
    RC=$?
    if [ "$RC" -eq 2 ]; then
        BLOCKED=1
    elif [ "$RC" -gt 2 ]; then
        # 判定器自身故障（非命中）：按 fail-open 记 warn，不阻塞
        echo "⚠️  [warn] anchor 守卫判定器异常退出（code=${RC}），本次跳过锚点检查"
    fi
fi

# ---------- 9. 硬闸影子期（可选，gate_shadow_guard.sh + 登记表存在才启用） ----------
if [ -f scripts/gate_shadow_guard.sh ] && [ -f docs/gates/gate_registry.yml ]; then
    if [ $EXPLICIT_MODE -eq 1 ]; then
        bash scripts/gate_shadow_guard.sh $FILES
    else
        bash scripts/gate_shadow_guard.sh
    fi
    RC=$?
    if [ "$RC" -eq 2 ]; then
        BLOCKED=1
    elif [ "$RC" -gt 2 ]; then
        echo "⚠️  [warn] gate_shadow_guard 异常退出（code=${RC}），本次跳过闸登记检查"
    fi
fi

# ---------- 10. 吞异常扫描（shadow 起步，@gate(shadow, silent-exception-scan) ----------
# 规则：observability.no_silent_exception / data_sync_error_visibility
# 扫 diff 触及的 .py/.ts 中"捕获后什么都不做"：Python `except ...: pass`、TS 空 catch {}
# 行内豁免：该行尾加 `# guard-ignore: silent-exception-scan`
# 影子期：默认只 warn 不拦；校准零误报后设 GUARD_SILENT_EXCEPTION=enforce 转硬拦
GATE_SILENT_EXCEPTION="${GUARD_SILENT_EXCEPTION:-shadow}"
echo "🔍 [$GATE_SILENT_EXCEPTION] 吞异常扫描（except:pass / 空 catch）..."
SE_HITS=0
for f in "${FILES[@]}"; do
    echo "$f" | grep -qE '\.(py|ts|tsx)$' || continue
    echo "$f" | grep -qE '(^|/)(tests?|__pycache__|venv|node_modules)/' && continue
    [ -f "$f" ] || continue
    if echo "$f" | grep -qE '\.py$'; then
        HITS=$(grep -nE 'except[^:]*:[[:space:]]*pass([[:space:]]+#.*)?$' "$f" | grep -v 'guard-ignore: silent-exception-scan' || true)
    else
        # TS 空 catch 块（catch (e) {} 单行形态；多行空块由 review 把关，正则不强求）
        HITS=$(grep -nE 'catch[[:space:]]*(\([^)]*\))?[[:space:]]*\{[[:space:]]*\}([[:space:]]+//.*)?$' "$f" | grep -v 'guard-ignore: silent-exception-scan' || true)
    fi
    if [ -n "$HITS" ]; then
        SE_HITS=$((SE_HITS + 1))
        echo "$HITS" | sed "s|^|          $f:|"
    fi
done
if [ "$SE_HITS" -gt 0 ]; then
    if [ "$GATE_SILENT_EXCEPTION" = "enforce" ]; then
        BLOCKED=1
        echo "❌ [block] 发现 ${SE_HITS} 个文件存在吞异常（异常至少记录日志；豁免加行内注释 # guard-ignore: silent-exception-scan）"
    else
        echo "⚠️  [shadow] 发现 ${SE_HITS} 个文件存在吞异常——影子期只提醒不拦截，校准记录见 docs/gates/gate_registry.yml"
    fi
fi

# ---------- 汇总 ----------
if [ "$BLOCKED" -eq 1 ]; then
    echo ""
    echo "❌ guards: 存在阻塞项，请修复后重新提交"
    exit 1
fi

echo "✅ guards: 检查通过"
exit 0

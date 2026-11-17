#!/usr/bin/env bash
#
# gate_shadow_guard.sh 模板 —— 硬闸影子期强制（gate registry 驱动）
# TEMPLATE_VERSION: 7  # 2026-10-19: grep 捕获类不含 "=" 致 id= 截断（v6 只改 sed 不够）  # 2026-10-19: @gate 标注 id= 前缀未剥离致影子闸误报 "@id 未登记"（QuantLab 接线首曝）  # 2026-10-19: 修复 $REGISTRY 后接中文逗号在 bash3.2+set -u 下误判变量名（QuantLab 无 registry 时首曝）
#
# 背景（2026-09-22/23 沃土/216 连环误报）：新质量闸上线第一周拿理想假设
# 对着真实数据硬拦，连发误报（KREQ-046 因子单调闸、发布 D 类闸等）。
# 拍板制度：新闸一律先 shadow（只喊不拦）跑校准期，评审拍板后才转 enforce。
# 本脚本把制度变成机器闸，违规提交物理上进不了库。
#
# 约定（闸的登记与标注）：
#   1. 每个硬拦截闸（拦截=exit 非零/rc!=0 拒绝放行）必须在代码内携带标注注释：
#        # @gate(enforce, <闸id>)   —— 硬拦
#        # @gate(shadow,  <闸id>)   —— 影子期（只告警不拦）
#   2. 每个 enforce 闸必须在 docs/gates/gate_registry.yml 有条目，且含
#      calibration: 字段（校准记录：影子期结论+拍板人+转正日期）。
#
# 检查项（staged/显式文件模式同 guards.sh）：
#   1. [block] 新增 @gate(enforce, id) 但登记表无该 id 条目
#   2. [block] 新增 @gate(enforce, id) 但条目缺 calibration 字段（未评审转正）
#   3. [warn ] 新增 @gate(shadow, id) 但登记表无条目（提醒补登记，不阻塞）
#
# 自包含（bash + git + grep/sed/awk，兼容 GNU 与 macOS BSD）。输出中文。
set -u

EXPLICIT_MODE=0
if [ $# -gt 0 ]; then EXPLICIT_MODE=1; FILES="$*"; else FILES=$(git diff --cached --name-only); fi

REGISTRY="docs/gates/gate_registry.yml"
[ -f "$REGISTRY" ] || { echo "ℹ️  gate_shadow_guard: 无 ${REGISTRY}，跳过（未启用闸登记）"; exit 0; }

block_n=0; warn_n=0

# 登记表解析：id 全量列表；含 calibration 的 id 列表（awk 按 entry 块跟踪）
ids_all=$(awk '/^[[:space:]-]*id:/{gsub(/.*id:[[:space:]]*/,""); gsub(/["'"'"']/,""); print}' "$REGISTRY" | sort -u)
ids_calibrated=$(awk '
  /^[[:space:]-]*id:/{id=$0; has=0}
  /calibration:/{if(id!=""){print id; id=""}}
  END{}' "$REGISTRY" | sed 's/.*id:[[:space:]]*//; s/["'"'"']//g' | sort -u)

for f in $FILES; do
  [ -f "$f" ] || continue
  # 守卫自身与挂载脚本豁免（其注释含标注示例,不自检）
  case "$f" in scripts/gate_shadow_guard.sh|scripts/guards.sh) continue;; esac
  case "$f" in *.py|*.sh|*.ts|*.tsx|*.js) ;; *) continue;; esac
  if [ $EXPLICIT_MODE -eq 1 ]; then DIFFSRC="git diff -- $f"; else DIFFSRC="git diff --cached -- $f"; fi
  added=$(eval "$DIFFSRC" | grep -E '^[+]' | grep -v '^[+][+][+]' || true)
  [ -z "$added" ] && continue

  for gid in $(echo "$added" | grep -oE '@gate\(enforce,[[:space:]]*(id=)?[A-Za-z0-9_.-]+' | sed 's/@gate(enforce,[[:space:]]*//; s/^id=//' | sort -u); do
    if ! echo "$ids_all" | grep -qx "$gid"; then
      echo "❌ [block] $f: 新硬闸 @$gid 未登记——新闸一律 shadow 起步，请在 $REGISTRY 登记 shadow 条目并改标注为 @gate(shadow, $gid)"
      block_n=$((block_n+1))
    elif ! echo "$ids_calibrated" | grep -qx "$gid"; then
      echo "❌ [block] $f: 硬闸 @$gid 登记表缺 calibration（校准记录）——影子期未满/未评审拍板，禁止转 enforce"
      block_n=$((block_n+1))
    fi
  done
  for gid in $(echo "$added" | grep -oE '@gate\(shadow,[[:space:]]*(id=)?[A-Za-z0-9_.-]+' | sed 's/@gate(shadow,[[:space:]]*//; s/^id=//' | sort -u); do
    if ! echo "$ids_all" | grep -qx "$gid"; then
      echo "⚠️  [warn ] $f: 影子闸 @$gid 未在 $REGISTRY 登记（补一条 shadow 条目，含 since 日期）"
      warn_n=$((warn_n+1))
    fi
  done
done

if [ $block_n -gt 0 ]; then echo "🚫 gate_shadow_guard: $block_n 项 block（新闸影子期制度，拍板 2026-09-23）"; exit 2; fi
[ $warn_n -gt 0 ] && echo "⚠️  gate_shadow_guard: $warn_n 项 warn"
exit 0

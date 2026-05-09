#!/bin/bash
# =============================================================================
# JQData 轻量发版脚本
# 用法: ./scripts/release.sh [patch|minor|major]
#
# 流程:
#   1. Git 检查（main分支、工作区干净）
#   2. 版本号 bump
#   3. 更新 version.txt
#   4. commit + tag + push
#   5. 输出部署提示
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

VERSION_FILE="version.txt"
TYPE="${1:-patch}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

error()  { echo -e "${RED}[ERROR]${NC} $1"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $1"; }
info()   { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }

# ---------- 1. Git 检查 ----------
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    error "必须在 main 分支执行发版，当前: $CURRENT_BRANCH"
    exit 1
fi

git fetch origin
if [ -n "$(git status --porcelain)" ]; then
    error "工作区不干净，请先提交或暂存修改"
    exit 1
fi

# ---------- 2. 读取并 bump 版本 ----------
CURRENT=$(cat "$VERSION_FILE" 2>/dev/null || echo "0.0.0")
MAJOR=$(echo "$CURRENT" | cut -d. -f1)
MINOR=$(echo "$CURRENT" | cut -d. -f2)
PATCH=$(echo "$CURRENT" | cut -d. -f3)

case "$TYPE" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
    *) error "版本类型必须是 patch/minor/major"; exit 1 ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"

info "当前版本: $CURRENT"
info "新版本:   v$NEW_VERSION"

# ---------- 3. 更新版本文件 ----------
echo "$NEW_VERSION" > "$VERSION_FILE"
git add "$VERSION_FILE"
git commit -m "chore(release): v${NEW_VERSION}"

# ---------- 4. 打 tag + 推送 ----------
git tag "v${NEW_VERSION}"
git push origin main
git push origin "v${NEW_VERSION}"

success "发版成功: v${NEW_VERSION}"
echo ""
echo "💡 部署到 D 服务器:"
echo "   ssh root@101.132.161.52 'cd /data/jqdata-platform && ./scripts/deploy.sh v${NEW_VERSION}'"
echo ""
echo "💡 部署到 C 服务器:"
echo "   ssh root@139.196.186.67 'cd /data/jqdata-platform && ./scripts/deploy.sh v${NEW_VERSION}'"
echo ""
echo "💡 或一键部署两台:"
echo "   ssh root@101.132.161.52 'cd /data/jqdata-platform && ./scripts/deploy.sh v${NEW_VERSION}'"
echo "   ssh root@139.196.186.67 'cd /data/jqdata-platform && ./scripts/deploy.sh v${NEW_VERSION}'"

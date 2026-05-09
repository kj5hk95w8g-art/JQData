#!/bin/bash
# =============================================================================
# API 镜像构建脚本
# 用法: ./build-image.sh [版本号] [仓库地址]
# 示例:
#   ./build-image.sh                    # 构建 jqdata-platform-api:latest
#   ./build-image.sh v1.2.3             # 构建 jqdata-platform-api:v1.2.3
#   ./build-image.sh v1.2.3 registry.cn-hangzhou.aliyuncs.com/yuntu  # 推送到阿里云ACR
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

VERSION="${1:-latest}"
REGISTRY="${2:-""}"
IMAGE_NAME="jqdata-platform-api"
FULL_TAG="${IMAGE_NAME}:${VERSION}"

echo "🐳 构建镜像: ${FULL_TAG}"
echo "   项目根目录: ${PROJECT_ROOT}"
echo ""

cd "$PROJECT_ROOT"

# 检查依赖文件存在
if [[ ! -f "Dockerfile" ]]; then
    echo "❌ 错误: Dockerfile 不存在" >&2
    exit 1
fi
if [[ ! -f "src/main.py" ]]; then
    echo "❌ 错误: src/main.py 不存在" >&2
    exit 1
fi

# 构建镜像
docker build \
    --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
    -t "${FULL_TAG}" \
    -f Dockerfile \
    .

echo ""
echo "✅ 构建完成: ${FULL_TAG}"

# 同时打 latest 标签（如果版本不是latest）
if [[ "$VERSION" != "latest" ]]; then
    docker tag "${FULL_TAG}" "${IMAGE_NAME}:latest"
    echo "✅ 已同步打标签: ${IMAGE_NAME}:latest"
fi

# 推送到镜像仓库（如果指定了）
if [[ -n "$REGISTRY" ]]; then
    REMOTE_TAG="${REGISTRY}/${FULL_TAG}"
    REMOTE_LATEST="${REGISTRY}/${IMAGE_NAME}:latest"
    
    echo ""
    echo "📤 推送到仓库: ${REGISTRY}"
    docker tag "${FULL_TAG}" "${REMOTE_TAG}"
    docker push "${REMOTE_TAG}"
    echo "✅ 已推送: ${REMOTE_TAG}"
    
    if [[ "$VERSION" != "latest" ]]; then
        docker tag "${FULL_TAG}" "${REMOTE_LATEST}"
        docker push "${REMOTE_LATEST}"
        echo "✅ 已推送: ${REMOTE_LATEST}"
    fi
fi

echo ""
echo "💡 使用方式:"
echo "   docker compose -f docker-compose.d.yml up -d api"

#!/bin/bash
# =============================================================================
# JQData 轻量部署脚本（在目标服务器上执行）
# 用法: ./scripts/deploy.sh [version_tag]
#
# 特性:
#   - 自动检测服务器角色（C/D）
#   - 支持指定 tag 部署（生产推荐）
#   - 默认部署最新 tag
#   - 部署后健康检查
#   - 失败自动回滚到上一个版本
# =============================================================================
set -euo pipefail

PROJECT_DIR="/data/jqdata-platform"
cd "$PROJECT_DIR"

VERSION="${1:-}"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

error()     { echo -e "${RED}[ERROR]${NC} $1"; }
warn()      { echo -e "${YELLOW}[WARN]${NC} $1"; }
info()      { echo -e "${BLUE}[INFO]${NC} $1"; }
success()   { echo -e "${GREEN}[SUCCESS]${NC} $1"; }

# 保存当前版本（用于回滚）
PREVIOUS=$(git describe --tags --abbrev=0 2>/dev/null || echo "")

# ---------- 1. 拉取代码 ----------
info "拉取远程代码..."
git fetch origin --tags

if [ -n "$VERSION" ]; then
    info "部署指定版本: $VERSION"
    if ! git show-ref --tags "$VERSION" > /dev/null 2>&1; then
        # 可能是远程tag，再fetch一次
        git fetch origin tag "$VERSION"
    fi
    git checkout "$VERSION"
else
    VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    if [ -z "$VERSION" ]; then
        error "未找到任何 tag，请先用 release.sh 发版"
        exit 1
    fi
    info "部署最新版本: $VERSION"
    git checkout "$VERSION"
fi

# ---------- 2. 判断服务器角色并部署 ----------
ROLLBACK=0

if [ -f "docker-compose.d.yml" ]; then
    # D 服务器
    info "检测到 D 服务器环境"
    info "部署主服务..."
    docker compose -f docker-compose.d.yml up -d
    
    if [ -f "monitoring/docker-compose.monitoring.yml" ]; then
        info "部署监控栈..."
        docker compose -f monitoring/docker-compose.monitoring.yml up -d
    fi
    
    # 健康检查
    info "健康检查（等待10秒）..."
    sleep 10
    
    if ! curl -sf http://127.0.0.1:18080/health > /dev/null 2>&1; then
        error "API (18080) 健康检查失败"
        ROLLBACK=1
    else
        success "API (18080) 正常"
    fi
    
    if ! docker exec jqdata-clickhouse clickhouse-client -q "SELECT 1" > /dev/null 2>&1; then
        error "ClickHouse 健康检查失败"
        ROLLBACK=1
    else
        success "ClickHouse 正常"
    fi
    
    if ! docker exec jqdata-redis redis-cli ping > /dev/null 2>&1; then
        error "Redis 健康检查失败"
        ROLLBACK=1
    else
        success "Redis 正常"
    fi
    
    if [ -n "${PREVIOUS}" ] && [ "${ROLLBACK}" = "0" ]; then
        info "容器状态:"
        docker ps --format "table {{.Names}}\t{{.Status}}" | grep jqdata
    fi

elif [ -f "docker-compose.c.yml" ]; then
    # C 服务器
    info "检测到 C 服务器环境"
    info "部署 Grafana..."
    docker compose -f docker-compose.c.yml up -d
    
    # 健康检查
    info "健康检查（等待5秒）..."
    sleep 5
    
    if ! curl -sf http://127.0.0.1:3000/api/health > /dev/null 2>&1; then
        error "Grafana (3000) 健康检查失败"
        ROLLBACK=1
    else
        success "Grafana 正常"
    fi
    
    if [ -n "${PREVIOUS}" ] && [ "${ROLLBACK}" = "0" ]; then
        info "容器状态:"
        docker ps --format "table {{.Names}}\t{{.Status}}" | grep grafana
    fi

else
    error "未找到 docker-compose 文件，无法判断服务器角色（C/D）"
    exit 1
fi

# ---------- 3. 回滚 ----------
if [ "${ROLLBACK}" = "1" ]; then
    if [ -n "$PREVIOUS" ]; then
        warn "健康检查失败，回滚到上一个版本: $PREVIOUS"
        git checkout "$PREVIOUS"
        if [ -f "docker-compose.d.yml" ]; then
            docker compose -f docker-compose.d.yml up -d
        elif [ -f "docker-compose.c.yml" ]; then
            docker compose -f docker-compose.c.yml up -d
        fi
        success "已回滚到 $PREVIOUS"
    else
        error "健康检查失败，且无上一个版本可回滚"
    fi
    exit 1
fi

success "部署完成: $VERSION"

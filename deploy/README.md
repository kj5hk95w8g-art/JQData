# JQData 部署说明

## 快速开始

```bash
# 1. 初始化服务器（首次）
./scripts/deploy.sh d --init
./scripts/deploy.sh c --init

# 2. 部署
./scripts/deploy.sh d    # 部署D服务器
./scripts/deploy.sh c    # 部署C服务器

# 3. 重启
./scripts/deploy.sh d --restart

# 4. 数据同步
./scripts/deploy.sh d --sync

# 5. 查看状态
./scripts/deploy.sh d --status
```

## 服务器配置

D服务器（核心数据）：101.132.161.52
- ClickHouse 8123/9000
- Redis 6379
- FastAPI 8000

C服务器（可视化）：139.196.186.67
- Grafana 3000

## 文件说明

| 文件 | 用途 |
|------|------|
| `deploy/docker-compose.d.yml` | D服务器编排 |
| `deploy/docker-compose.c.yml` | C服务器编排 |
| `deploy/Dockerfile` | API镜像 |
| `deploy/.env.example` | 配置模板 |
| `scripts/deploy.sh` | 部署入口 |
| `scripts/health-check.sh` | 健康检查 |
| `src/main.py` | API服务 |
| `src/sync_daily.py` | 数据同步 |

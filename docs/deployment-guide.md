# 容器运维手册

> 本文档描述 CD 服务器容器的管理方式、日常运维命令、监控告警配置。

---

## 一、架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│  D 服务器 (101.132.161.52)                                      │
│  ┌─────────────┐  ┌──────────┐  ┌─────────┐  ┌───────────────┐ │
│  │ ClickHouse  │  │  Redis   │  │ FastAPI │  │  Prometheus   │ │
│  │   :8123     │  │  :6379   │  │ :18080  │  │    :9090      │ │
│  └─────────────┘  └──────────┘  └─────────┘  └───────────────┘ │
│  ┌─────────────┐  ┌──────────────────────────────────────────┐ │
│  │  cadvisor   │  │          node-exporter                    │ │
│  │   :18081    │  │             :9100                         │ │
│  └─────────────┘  └──────────────────────────────────────────┘ │
│                                                                 │
│  数据持久化: /data/clickhouse, /data/redis, /data/prometheus   │
│  管理工具:  docker compose                                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ VPC 内网
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  C 服务器 (139.196.186.67)                                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Grafana 11                              │  │
│  │                    :3000                                   │  │
│  │  数据源: D:9090 (Prometheus)                               │  │
│  │  Dashboard: Node Exporter Full (ID: 1860)                  │  │
│  │            cAdvisor (ID: 193)                              │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、为什么用 Bind Mount？

Docker 的数据持久化有两种方式：

| 方式 | 原理 | 数据位置 | 适合场景 |
|------|------|---------|---------|
| **Bind Mount** | 宿主机目录直接挂载进容器 | `/data/xxx`（你看得见） | **数据重要、需要备份/人工干预** |
| Docker Volume | Docker 自己管理存储空间 | `/var/lib/docker/volumes/xxx/_data`（藏很深） | 临时数据、不care位置 |

**本项目选择 Bind Mount**，原因：

1. **备份简单**：直接 `tar czf backup.tar.gz /data/clickhouse`
2. **调试方便**：宿主机直接用 `clickhouse-client` 连本地数据
3. **迁移容易**：SCP 整个 `/data` 目录到新机器即可
4. **权限可控**：宿主机直接 `chown` 调整文件归属

**⚠️ 注意**：Bind Mount 的数据和宿主机绑定，如果宿主机系统盘重装，数据会丢失。因此必须**定期备份**。

---

## 三、日常运维命令

### 3.1 查看状态

```bash
cd /data/jqdata-platform

# 容器状态
docker compose -f docker-compose.d.yml ps
docker stats --no-stream

# 服务日志（最近50行）
docker logs --tail 50 -f jqdata-api
docker logs --tail 50 -f jqdata-clickhouse
docker logs --tail 50 -f jqdata-redis

# 磁盘/数据大小
du -sh /data/*
df -h
```

### 3.2 重启服务

```bash
# 仅重启API（改代码后）
docker compose -f docker-compose.d.yml restart api

# 全量重启
docker compose -f docker-compose.d.yml down
docker compose -f docker-compose.d.yml up -d

# 重建API镜像后重启
docker compose -f docker-compose.d.yml up -d --build api
```

### 3.3 进入容器排查

```bash
# ClickHouse 客户端
docker exec -it jqdata-clickhouse clickhouse-client

# Redis 客户端
docker exec -it jqdata-redis redis-cli

# API 容器内查看
docker exec -it jqdata-api bash
```

### 3.4 数据备份

```bash
# ClickHouse 数据备份（热备份，无需停机）
tar czf /data/backup/clickhouse-$(date +%Y%m%d).tar.gz /data/clickhouse

# Redis 数据备份（AOF持久化，直接复制）
cp /data/redis/appendonly.aof /data/backup/redis-$(date +%Y%m%d).aof

# 全量数据打包
tar czf /data/backup/jqdata-full-$(date +%Y%m%d).tar.gz /data/clickhouse /data/redis /data/grafana
```

---

## 四、日志管理

### 4.1 日志轮转（已配置）

每个容器通过 `logging` 配置限制日志大小：

| 服务 | 单文件上限 | 保留文件数 | 总上限 |
|------|-----------|-----------|--------|
| ClickHouse | 100MB | 3 | 300MB |
| Redis | 50MB | 3 | 150MB |
| API | 50MB | 3 | 150MB |
| Prometheus | 50MB | 3 | 150MB |
| cadvisor | 50MB | 3 | 150MB |
| node-exporter | 50MB | 3 | 150MB |

### 4.2 查看日志

```bash
# 实时跟踪
docker logs -f jqdata-api

# 查看历史（带时间戳）
docker logs --timestamps jqdata-api | tail -100

# 搜索关键词
docker logs jqdata-api 2>&1 | grep "ERROR"
```

---

## 五、监控栈

### 5.1 组件说明

| 组件 | 端口 | 作用 | 数据来源 |
|------|------|------|---------|
| **Prometheus** | 9090 | 时序数据库，存储所有指标 | 自身采集 |
| **cadvisor** | 18081 | Google容器监控工具 | Docker API |
| **node-exporter** | 9100 | 宿主机资源监控 | /proc, /sys |

### 5.2 部署监控栈

```bash
cd /data/jqdata-platform

# 启动监控
docker compose -f monitoring/docker-compose.monitoring.yml up -d

# 验证
curl -s http://localhost:9090/api/v1/status/targets | grep -o '"health":"[^"]*"'
```

### 5.3 Grafana 配置

在 C 服务器 Grafana（http://139.196.186.67:3000）添加数据源：

1. **Configuration → Data Sources → Add data source → Prometheus**
2. URL: `http://101.132.161.52:9090`
3. Save & Test

导入 Dashboard：

| Dashboard | ID | 用途 |
|-----------|-----|------|
| Node Exporter Full | 1860 | 宿主机CPU/内存/磁盘/网络 |
| cAdvisor | 193 | 容器资源使用/限制 |

### 5.4 告警规则

Prometheus 已配置基础告警规则（`monitoring/alert-rules.yml`）：

| 规则名 | 条件 | 级别 |
|--------|------|------|
| HighDiskUsage | 磁盘可用 < 10% | warning |
| HighMemoryUsage | 内存使用 > 90% | warning |
| NodeDown | node-exporter 不可达 | critical |
| ContainerHighCpu | 容器CPU > 80% 持续10分钟 | warning |
| ContainerHighMemory | 容器内存 > 90% 持续5分钟 | warning |

> 当前未接 Alertmanager，告警指标可在 Prometheus UI (http://D:9090/alerts) 查看。如需通知，配置 `WEBHOOK_URL` 后启用 `scripts/health-check-alert.sh`。

---

## 六、健康检查脚本

### 6.1 启用定时检查

```bash
# 编辑 crontab
crontab -e

# 添加（每分钟执行）
* * * * * /data/jqdata-platform/scripts/health-check-alert.sh >> /data/monitoring/alerts/alerts.log 2>&1

# 查看告警历史
tail -f /data/monitoring/alerts/alerts.log
```

### 6.2 配置企业微信/钉钉通知

```bash
# 在 ~/.bashrc 或 crontab 环境变量中添加
export WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
```

---

## 七、镜像构建

```bash
cd /data/jqdata-platform

# 构建最新镜像
./scripts/build-image.sh

# 构建指定版本
./scripts/build-image.sh v1.2.3

# 构建并推送到阿里云ACR
./scripts/build-image.sh v1.2.3 registry.cn-hangzhou.aliyuncs.com/yuntu
```

---

## 八、FAQ

**Q: 容器重启后数据还在吗？**
> 在。Bind Mount 的数据存在宿主机 `/data/xxx`，容器只是「借用」这些目录。`docker compose down` 不会删除宿主机数据。

**Q: 可以删除某个容器重新创建吗？**
> 可以。只要 `/data/xxx` 还在，删除容器后 `docker compose up -d` 会重建，数据自动挂载回来。

**Q: 日志文件太多怎么清理？**
> 日志已配置轮转（max-size + max-file），Docker 会自动清理。如需手动清理：`docker system prune --volumes`（⚠️ 会删除所有未使用的卷）。

**Q: Prometheus 数据占多大？**
> 30天 retention，当前规模约 1~2GB。Prometheus 数据在 `/data/prometheus`。

**Q: 监控栈挂了会影响主服务吗？**
> 不会。监控栈（`jqdata-monitoring`）和主服务（`jqdata-platform`）是两个独立的 docker compose 项目，网络隔离。

---

# 附录：完整部署配置（原05-deployment.md合并至此）

## 一、服务器基础信息

| 服务器 | 角色 | 公网IP | 内网IP | 配置 | 操作系统 | 带宽 |
|--------|------|--------|--------|------|---------|------|
| **C** | 可视化 | `139.196.186.67` | `172.24.52.235` | 4核32GB | Ubuntu 24.04 | 1Mbps |
| **D** | 核心数据 | `101.132.161.52` | `172.24.52.237` | 8核64GB | Ubuntu 24.04 | 1Mbps |

**网络**：阿里云 VPC `172.24.52.0/24`，内网互通。所有数据服务端口仅对内网开放。

---

## 二、D 服务器部署详情

### 2.1 项目目录

```
/data/jqdata-platform/
├── docker-compose.d.yml          # 主服务编排
├── Dockerfile                    # API镜像构建定义
├── src/
│   ├── main.py                   # FastAPI服务
│   └── sync_daily.py             # 数据同步脚本
├── monitoring/
│   ├── docker-compose.monitoring.yml   # 监控栈编排
│   ├── prometheus.yml            # Prometheus配置
│   └── alert-rules.yml           # 告警规则
├── scripts/
│   ├── deploy.sh                 # 标准化部署脚本
│   ├── health-check.sh           # 健康检查
│   ├── health-check-alert.sh     # 健康检查+告警（crontab）
│   ├── build-image.sh            # API镜像构建
│   ├── setup-log-rotation.sh     # 日志轮转配置
│   └── update-progress.sh        # 进度更新
└── deploy/
    └── docker-compose.c.yml      # C服务器compose（参考用）
```

### 2.2 主服务编排 (docker-compose.d.yml)

```yaml
name: jqdata-platform
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.8
    container_name: jqdata-clickhouse
    restart: unless-stopped
    ports: ["8123:8123", "9000:9000"]
    volumes: ["/data/clickhouse:/var/lib/clickhouse"]
    ulimits: {nofile: {soft: 262144, hard: 262144}}
    environment:
      - CLICKHOUSE_DB=default
      - CLICKHOUSE_USER=default
      - CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1
    healthcheck:
      test: ["CMD", "clickhouse-client", "-q", "SELECT 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    networks: [jqnet]
    logging:
      driver: "json-file"
      options: {max-size: "100m", max-file: "3"}

  redis:
    image: redis:7-alpine
    container_name: jqdata-redis
    restart: unless-stopped
    ports: ["6379:6379"]
    volumes: ["/data/redis:/data"]
    command: redis-server --appendonly yes --maxmemory 4gb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    networks: [jqnet]
    logging:
      driver: "json-file"
      options: {max-size: "50m", max-file: "3"}

  api:
    image: jqdata-platform-api:latest
    container_name: jqdata-api
    restart: unless-stopped
    ports: ["18080:8000"]
    volumes: ["/data/jqdata-platform/src/main.py:/app/main.py:ro"]
    environment:
      - CH_HOST=clickhouse
      - CH_DB=jqdata
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - SIGNATURE_SALT=yuntu-jqdata-2026-internal-only
    depends_on:
      clickhouse: {condition: service_healthy}
      redis: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
      interval: 15s
      timeout: 5s
      retries: 10
      start_period: 20s
    networks: [jqnet]
    logging:
      driver: "json-file"
      options: {max-size: "50m", max-file: "3"}

networks:
  jqnet: {driver: bridge}
```

### 2.3 监控栈编排 (monitoring/docker-compose.monitoring.yml)

```yaml
name: jqdata-monitoring
services:
  prometheus:
    image: prom/prometheus:v2.51.0
    container_name: jqdata-prometheus
    restart: unless-stopped
    ports: ["9090:9090"]
    volumes:
      - "./prometheus.yml:/etc/prometheus/prometheus.yml:ro"
      - "./alert-rules.yml:/etc/prometheus/alert-rules.yml:ro"
      - "/data/prometheus:/prometheus"
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=30d"
      - "--web.enable-lifecycle"
    logging:
      driver: "json-file"
      options: {max-size: "50m", max-file: "3"}
    networks: [jqmon]

  # cadvisor: 容器监控，因国内网络无法拉取镜像(gcr.io)，待恢复后启用
  # image: google/cadvisor:v0.49.1
  # ports: ["18081:8080"]

  node-exporter:
    image: prom/node-exporter:v1.7.0
    container_name: jqdata-node-exporter
    restart: unless-stopped
    ports: ["9100:9100"]
    volumes:
      - "/proc:/host/proc:ro"
      - "/sys:/host/sys:ro"
      - "/:/rootfs:ro"
    command:
      - "--path.procfs=/host/proc"
      - "--path.rootfs=/rootfs"
      - "--path.sysfs=/host/sys"
      - "--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)"
    logging:
      driver: "json-file"
      options: {max-size: "50m", max-file: "3"}
    networks: [jqmon]

networks:
  jqmon: {driver: bridge}
```

### 2.4 Prometheus 配置 (monitoring/prometheus.yml)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    instance: 'jqdata-d'
    env: 'production'

rule_files:
  - "alert-rules.yml"

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']
```

### 2.5 数据目录

| 目录 | 用途 | 当前大小 |
|------|------|---------|
| `/data/clickhouse` | ClickHouse 数据 | ~345MB |
| `/data/redis` | Redis AOF 数据 | ~24KB |
| `/data/prometheus` | Prometheus 时序数据 | ~动态增长 |
| `/data/jqdata-platform` | 项目代码/脚本 | ~704KB |
| `/data/monitoring/alerts` | 健康检查告警日志 | ~动态增长 |

### 2.6 系统配置

| 配置项 | 值 | 说明 |
|--------|----|------|
| 时区 | `Asia/Shanghai` (CST, +0800) | 与北京时间一致 |
| Swap | `0B` | 未配置swap，依赖物理内存 |
| Docker版本 | `latest (Ubuntu 24.04仓库)` | — |
| 内核 | `Linux 6.x` | — |

> 64GB物理内存充足，当前无swap不影响运行。后续若OOM可考虑添加swap或调整容器内存限制。

### 2.7 Docker Daemon 配置

文件路径：`/etc/docker/daemon.json`

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "registry-mirrors": ["https://docker.mirrors.sjtug.sjtu.edu.cn"]
}
```

> 注意：daemon.json 配置对新创建的容器生效。当前各服务已通过 compose 的 `logging` 配置限制日志，全局 daemon 配置供未来初始化参考。

### 2.8 环境变量模板

文件路径：`deploy/.env.example`

```bash
# ClickHouse
CH_CONTAINER_NAME=jqdata-clickhouse
CH_HOST_PORT=8123
CH_TCP_PORT=9000
CH_DATA_DIR=/data/clickhouse

# Redis
REDIS_CONTAINER_NAME=jqdata-redis
REDIS_HOST_PORT=6379
REDIS_DATA_DIR=/data/redis
REDIS_MAXMEM=4gb

# API
API_CONTAINER_NAME=jqdata-api
API_HOST_PORT=8000
API_IMAGE=jqdata-platform:latest
CODE_DIR=/data/jqdata-platform

# Grafana
GRAFANA_CONTAINER_NAME=jqdata-grafana
GRAFANA_HOST_PORT=3000
GRAFANA_DATA_DIR=/data/grafana
GRAFANA_ADMIN_PASS=admin123
```

> 当前 compose 文件已硬编码上述值，`.env.example` 供本地开发参考。生产环境如需走环境变量注入，需修改 compose 使用 `${VAR}` 语法。

### 2.6 定时任务

#### 健康检查（每分钟）

```bash
* * * * * /data/jqdata-platform/scripts/health-check-alert.sh >> /data/monitoring/alerts/alerts.log 2>&1
```

#### 数据每日同步（交易日 23:00）

```bash
# 交易日（周一到周五）23:00 执行每日同步
0 23 * * 1-5 /data/jqdata-platform/scripts/sync-incremental.sh
```

**策略：先增量，后全量**
1. **阶段1（增量）**：同步当天收盘数据，优先级高，额度消耗极小（约 1 万条/天）
2. **阶段2（全量补全）**：检查剩余额度，如有余量则继续全量补全（pre -> post）

**配置要求：**
1. crontab 所在用户需要设置环境变量 `JQ_USER` 和 `JQ_PASS`
2. 日志目录 `$PROJECT_DIR/logs` 自动创建
3. 日限额通过 `.env` 中 `DAILY_QUOTA_LIMIT` 控制，默认 550 万条

**手动全量同步（historical backfill）**：
```bash
ssh jqdata-d
cd /data/jqdata-platform
source .env
python3 src/sync_daily.py --resume --fq pre   # 继续 pre
python3 src/sync_daily.py --full --fq post    # 开始 post
```

配置位置：`deploy` 用户的 crontab

### 2.7 Dockerfile (API 镜像)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    fastapi==0.111.0 uvicorn==0.30.0 clickhouse-driver==0.2.9 \
    redis==5.0.0 pandas==2.2.0 jqdatasdk==1.9.6
COPY main.py /app/
COPY sync_daily.py /app/
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 三、C 服务器部署详情

### 3.1 项目目录

```
/data/jqdata-platform/
├── docker-compose.c.yml          # Grafana编排
└── .git/                         # 与D服务器同步的Git仓库
```

### 3.2 Grafana 编排 (docker-compose.c.yml)

```yaml
name: jqdata-platform
services:
  grafana:
    image: grafana/grafana:11.0.0
    container_name: jqdata-grafana
    restart: unless-stopped
    user: "472:472"
    ports: ["3000:3000"]
    volumes: ["/data/grafana:/var/lib/grafana"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin123
      - GF_SERVER_ENABLE_GZIP=true
      - GF_ANALYTICS_REPORTING_ENABLED=false
      - GF_ANALYTICS_CHECK_FOR_UPDATES=false
      - GF_SECURITY_ANGULAR_SUPPORT_ENABLED=false
      - GF_NEWS_NEWS_FEED_ENABLED=false
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:3000/api/health"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 15s
    networks: [jqnet]
    logging:
      driver: "json-file"
      options: {max-size: "50m", max-file: "3"}

networks:
  jqnet: {driver: bridge}
```

### 3.3 Grafana 配置详情

**管理员账号**
| 项 | 值 |
|----|----|
| 账号 | `admin` |
| 密码 | `admin123` |
| 访问地址 | `http://139.196.186.67:3000` |

**数据源配置**

| 配置项 | 值 |
|--------|----|
| 名称 | `Prometheus-D` |
| 类型 | `prometheus` |
| URL | `http://172.24.52.237:9090`（D服务器内网IP） |
| 访问方式 | `Server`（Grafana后端代理） |
| 是否默认 | 是 |
| UID | `cfliqnogb4i68d` |

> 数据源通过 Grafana HTTP API 配置，配置脚本：`scripts/setup-grafana-datasource.sh`（参考实现）

**Dashboard 配置**

| Dashboard | ID | URL 路径 | 数据源 |
|-----------|-----|---------|--------|
| Node Exporter Full | 1860 | `/d/afliqnsxl2hvka/node-exporter-full` | Prometheus-D |

> Dashboard 通过 Grafana HTTP API 导入，数据来源：grafana.com/dashboards/1860

### 3.4 数据持久化

| 目录 | 用途 | 权限 |
|------|------|------|
| `/data/grafana` | Grafana SQLite 数据库、插件、配置 | `472:472`（Grafana容器用户） |

> 容器内用户UID 472映射到宿主机，需确保宿主机 `/data/grafana` 目录权限正确。
> 首次启动前需执行：`chown -R 472:472 /data/grafana`

当前 `/data/grafana` 内容：

```
/data/grafana/
├── grafana.db          # SQLite 主数据库（配置、用户、Dashboard元数据）
├── csv/                # CSV导出目录
├── plugins/            # 已安装插件（如有）
└── png/                # 渲染图片缓存（如有）
```

### 3.5 环境变量说明

| 环境变量 | 值 | 说明 |
|---------|----|------|
| `GF_SECURITY_ADMIN_PASSWORD` | `admin123` | 管理员密码 |
| `GF_SERVER_ENABLE_GZIP` | `true` | 启用Gzip压缩，减少传输体积 |
| `GF_ANALYTICS_REPORTING_ENABLED` | `false` | 关闭使用统计上报 |
| `GF_ANALYTICS_CHECK_FOR_UPDATES` | `false` | 关闭更新检查 |
| `GF_SECURITY_ANGULAR_SUPPORT_ENABLED` | `false` | 禁用Angular支持，减少前端资源 |
| `GF_NEWS_NEWS_FEED_ENABLED` | `false` | 关闭新闻Feed |

### 3.6 已知问题

**问题：首次打开 Grafana 页面加载极慢**

- **原因**：Grafana 11 前端构建文件约 **143.6MB**，C 服务器带宽仅 **1Mbps**
- **表现**：首次访问 `/login` 页面时，logo 长时间旋转，内容无法渲染
- **缓解**：已启用 Gzip 压缩 + 禁用非必要前端模块，但仍需 **2~5 分钟**首次加载
- **后续**：浏览器缓存后再次访问速度正常
- **根治方案**：升级 C 服务器带宽至 5Mbps+，或换用 Netdata 等轻量监控工具

---

## 四、数据同步状态（REQ-003）

| 数据表 | 状态 | 已同步行数 | 备注 |
|--------|------|-----------|------|
| `index_daily` | ✅ 完成 | 23,025 | 近5年(2020-2026) |
| `stock_daily_pre` | ⚠️ 进行中 | 2,763,000 | 已完成9/27批，还差18批 |
| `stock_daily_post` | ❌ 未开始 | 0 | 待pre完成后开始 |

**同步工具**：`src/sync_daily.py`（D服务器容器内运行）  
**账号**：JQData 正式版，日额度 1000万条  
**策略**：分批次同步，每批约30万条，避免单日额度耗尽  
**日志**：`/data/jqdata-platform/sync_*.log`

---

## 五、安全组规则

### 4.1 D 服务器 (101.132.161.52)

**入方向规则**：

| 协议 | 端口 | 授权对象 | 说明 |
|------|------|---------|------|
| 自定义TCP | 8123 | `172.24.52.0/24` | ClickHouse（C的Airflow将来连CK） |
| 自定义TCP | 18080 | `172.24.52.0/24` | FastAPI（C服务器调API） |
| 自定义TCP | 9090 | `172.24.52.0/24` | Prometheus（Grafana读取监控） |
| 自定义TCP | 22 | `100.104.0.0/16` | SSH（阿里云内部服务/已有规则） |
| 所有TCP | 1-65535 | `223.166.186.162` | 全端口（管理员本地IP，已有规则） |

> 18080 公网访问规则由用户本地IP `223.166.186.162` 的全端口规则覆盖。

### 4.2 C 服务器 (139.196.186.67)

**入方向规则**（需确认）：

| 协议 | 端口 | 授权对象 | 说明 |
|------|------|---------|------|
| 自定义TCP | 3000 | `223.166.186.162` | Grafana（管理员本地访问） |

> 当前 Grafana 通过管理员本地IP访问。如需团队成员访问，需将该成员公网IP加入安全组。

---

## 六、网络拓扑

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户本地电脑                                 │
│                           (223.166.186.162)                             │
│                              ↓ 3000 / 18080                             │
├─────────────────────────────────────────────────────────────────────────┤
│  阿里云 VPC: 172.24.52.0/24                                              │
│                                                                          │
│  ┌──────────────────────────────┐      ┌──────────────────────────────┐ │
│  │  C 服务器                     │      │  D 服务器                     │ │
│  │  139.196.186.67              │      │  101.132.161.52              │ │
│  │  172.24.52.235               │◄────►│  172.24.52.237               │ │
│  │                              │ 9090 │                              │ │
│  │  ┌────────────────────────┐  │      │  ┌────────────────────────┐  │ │
│  │  │  Grafana 11            │  │      │  │  Prometheus v2.51.0    │  │ │
│  │  │  Port: 3000            │  │      │  │  Port: 9090            │  │ │
│  │  │  DataSource:           │◄─┘      │  │  Retention: 30d        │  │ │
│  │  │   Prometheus-D         │         │  │                        │  │ │
│  │  │   URL: 172.24.52.237   │         │  │  ┌────────────────┐    │  │ │
│  │  └────────────────────────┘         │  │  │ node-exporter  │    │  │ │
│  │                                      │  │  │ Port: 9100     │    │  │ │
│  │                                      │  │  └────────────────┘    │  │ │
│  │                                      │  │  ┌────────────────┐    │  │ │
│  │                                      │  │  │ clickhouse:24.8│    │  │ │
│  │                                      │  │  │ Port: 8123/9000│    │  │ │
│  │                                      │  │  └────────────────┘    │  │ │
│  │                                      │  │  ┌────────────────┐    │  │ │
│  │                                      │  │  │ redis:7-alpine │    │  │ │
│  │                                      │  │  │ Port: 6379     │    │  │ │
│  │                                      │  │  └────────────────┘    │  │ │
│  │                                      │  │  ┌────────────────┐    │  │ │
│  │                                      │  │  │ FastAPI        │    │  │ │
│  │                                      │  │  │ Port: 18080    │    │  │ │
│  │                                      │  │  └────────────────┘    │  │ │
│  └──────────────────────────────┘      └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 七、变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-05-08 | D服务器重装系统，部署 ClickHouse + Redis + FastAPI | Kimi |
| 2026-05-08 | C服务器重装系统，部署 Grafana 11 | Kimi |
| 2026-05-09 | API端口 8000 → 18080 | Kimi |
| 2026-05-09 | 部署 Prometheus + node-exporter 监控栈（D服务器） | Kimi |
| 2026-05-09 | Grafana 配置 Prometheus 数据源 + 导入 Dashboard（C服务器） | Kimi |
| 2026-05-09 | 安全组放行 8123/18080/9090 内网访问 | 用户 |
| 2026-05-09 | Grafana 启用 Gzip + 禁用非必要模块 | Kimi |

---

## 八、回滚参考

### 回滚 Grafana 配置

```bash
# C服务器
cd /data/jqdata-platform
docker compose -f docker-compose.c.yml down
docker compose -f docker-compose.c.yml up -d
```

### 回滚 D 服务器主服务

```bash
# D服务器
cd /data/jqdata-platform
docker compose -f docker-compose.d.yml down
docker compose -f docker-compose.d.yml up -d
```

### 回滚监控栈

```bash
# D服务器
cd /data/jqdata-platform
docker compose -f monitoring/docker-compose.monitoring.yml down
docker compose -f monitoring/docker-compose.monitoring.yml up -d
```

---

*最后更新：2026-05-09*  
*版本：v1.0.0*

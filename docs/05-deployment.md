# 部署配置手册

> 本文档记录 C/D 服务器的完整部署配置，作为运维变更的唯一事实来源。
> 任何配置变更必须同步更新本文档。

---

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

### 2.6 定时任务

```bash
# 每分钟执行健康检查
* * * * * /data/jqdata-platform/scripts/health-check-alert.sh >> /data/monitoring/alerts/alerts.log 2>&1
```

配置位置：`root` 用户的 crontab

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

## 四、安全组规则

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

## 五、网络拓扑

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

## 六、变更记录

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

## 七、回滚参考

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

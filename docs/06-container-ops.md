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

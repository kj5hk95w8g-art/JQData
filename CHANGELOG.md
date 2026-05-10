# 变更日志

所有版本变更记录。

---

## [0.1.4] - 2026-05-10

### 修复
- deploy.sh 增加 Prometheus 配置热重载后的同步校验：热重载后等待 3 秒，对比容器内与宿主机配置文件的 md5，不一致则自动重启 Prometheus 容器

---

## [0.1.3] - 2026-05-10

### 新增
- A 服务器（应用服务器）接入监控：部署 node-exporter，Prometheus 采集系统指标
- Grafana 支持切换查看 A/D 服务器性能（instance 标签友好名称：server A / server D）

---

## [0.1.2] - 2026-05-10

### 新增
- 定时同步脚本 `scripts/sync-incremental.sh`：交易日 23:00 先增量后全量，用完当天剩余额度
- `src/sync_daily.py` 支持 `--incremental`（增量）、`--resume`（断点续传）、`--no-quota-limit`（放开额度限制）模式
- 额度保护：默认日限额 550 万条，晚上定时任务放开至 JQData 真实额度

### 变更
- 定时同步时间从 17:30 改为 23:00（把当天剩余额度用完）
- 同步脚本账号密码改为环境变量读取，删除硬编码
- 文档体系参照沃土重构：AGENTS.md 操作手册、requirements/ 需求文档、docs/ 分类导航

---

## [0.1.1] - 2026-05-09

### 新增
- Nginx 反向代理 + 单 IP 500r/s 限流
- 公网暴露 18080 端口（弹性公网 IP）

### 变更
- 认证方案改为简化版签名（单盐值，泄露再换）
- 部署脚本自动检测 compose 文件路径（兼容根目录旧文件）

---

## [0.1.0] - 2026-05-08

### 新增
- ClickHouse + Redis + FastAPI 容器化部署（D 服务器）
- REST API：/v1/daily/{code}、/v1/daily/batch、/v1/index/{code}、/v1/securities、/v1/trade_days
- 请求签名认证（简化版）
- Python SDK（jqdata_sdk）内置自动签名
- Prometheus + node-exporter 监控（D 服务器）
- Grafana 可视化（C 服务器）
- release.sh 发版脚本 + deploy.sh 部署脚本

---

*格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)*

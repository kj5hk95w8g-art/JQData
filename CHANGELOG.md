# 变更日志

所有版本变更记录。

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

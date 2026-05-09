# JQData 本地行情数据平台

基于聚宽 JQData 正式版构建的本地化行情数据中台，为内部量化策略、投研分析提供统一数据服务。

> **🚀 第一次进入项目？先看 [QUICK_START.md](./QUICK_START.md)**

---

## 项目状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| REQ-001 部署基础设施 | ✅ 已完成 | ClickHouse + Redis + FastAPI 容器化 |
| REQ-002 REST API + SDK | ✅ 已完成 | 签名认证、批量查询、Python SDK |
| REQ-003 日线数据同步 | 🟡 进行中 | `index_daily` ✅，`stock_daily_pre` 9/27批，post 未开始 |
| REQ-004 分发平台核心 | ✅ 已完成 | 请求签名、SDK自动签名、Nginx限流、PyPI分发 |
| 监控告警 | ✅ 已完成 | Prometheus + node-exporter + Grafana |
| 自动化备份 | ❌ 未开始 | — |

---

## 基础设施

| 服务器 | 配置 | 角色 | SSH |
|--------|------|------|-----|
| D `101.132.161.52` | 8核 64GB | ClickHouse主 + Redis + FastAPI + Prometheus | `ssh jqdata-d` |
| C `139.196.186.67` | 4核 32GB | Grafana 可视化 | `ssh jqdata-c` |
| A `106.14.141.212` | 4核 16GB | 应用服务器（资产沃土/资产管家，不动） | — |
| B `139.196.34.92` | 4核 16GB | 测试服务器（现有环境，保留） | — |

---

## 快速链接

| 你想知道什么 | 文档 |
|-------------|------|
| **第一次来，不知道该看什么** | **[QUICK_START.md](./QUICK_START.md)** ⭐ |
| AI 助手操作约束（红线规则） | [AGENTS.md](./AGENTS.md) |
| 数据库表结构 | [docs/03-database-schema.md](./docs/03-database-schema.md) |
| API 接口规范 | [docs/04-api-spec.md](./docs/04-api-spec.md) |
| 服务器运维、部署、监控 | [docs/06-container-ops.md](./docs/06-container-ops.md) |
| 分发平台设计（签名/SDK） | [docs/REQ-004-spec.md](./docs/REQ-004-spec.md) |
| 开发流程、分支规范 | [docs/development-workflow.md](./docs/development-workflow.md) |
| 待办需求 | [docs/TODO.md](./docs/TODO.md) |
| 变更历史 | [CHANGELOG.md](./CHANGELOG.md) |

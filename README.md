# JQData 本地行情数据平台

基于聚宽 JQData 正式版构建的本地化行情数据中台，为内部量化策略、投研分析提供统一数据服务。

> **🤖 AI 操作约束** → [`AGENTS.md`](./AGENTS.md)  
> **📋 当前状态** → [`docs/TODO.md`](./docs/TODO.md)

---

## 项目状态速览

| 阶段 | 状态 | 说明 |
|------|------|------|
| REQ-001 部署基础设施 | ✅ 已完成 | ClickHouse + Redis + FastAPI 容器化 |
| REQ-002 REST API + SDK | ✅ 已完成 | 签名认证、批量查询、Python SDK |
| REQ-003 日线数据同步 | 🟡 进行中 | `index_daily` ✅，`stock_daily_pre` 9/27批，post 未开始 |
| REQ-004 分发平台核心 | ✅ 已完成 | 请求签名、SDK自动签名、Nginx限流 500r/s |
| 监控告警 | ✅ 已完成 | Prometheus + node-exporter + Grafana |
| 自动化备份 | ❌ 未开始 | — |

---

## 基础设施速查

| 服务器 | 配置 | 角色 | SSH |
|--------|------|------|-----|
| D `101.132.161.52` | 8核 64GB | ClickHouse主 + Redis + FastAPI + Nginx + Prometheus | `ssh jqdata-d` |
| C `139.196.186.67` | 4核 32GB | Grafana 可视化 | `ssh jqdata-c` |
| A `106.14.141.212` | 4核 16GB | 应用服务器（资产沃土/云图中心，❌不动） | — |
| B `139.196.34.92` | 4核 16GB | 测试服务器（现有环境，❌不动） | — |

---

## 📚 文档分类

### 项目规范（必读）

| 文档 | 说明 |
|------|------|
| [AGENTS.md](./AGENTS.md) | AI 助手操作约束、红线规则、环境信息 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本变更记录 |
| [docs/TODO.md](./docs/TODO.md) | 需求池、当前状态、阻塞项 |

### 架构设计

| 文档 | 说明 |
|------|------|
| [docs/database-schema.md](./docs/database-schema.md) | ClickHouse 表结构设计 |
| [docs/api-reference.md](./docs/api-reference.md) | REST API 接口规范 |
| [docs/requirements/REQ-004.md](./docs/requirements/REQ-004.md) | 分发平台（签名/SDK/限流/PyPI） |

### 部署运维

| 文档 | 说明 |
|------|------|
| [docs/deployment-guide.md](./docs/deployment-guide.md) | 容器化部署完整手册（架构、配置、命令、监控、回滚） |

### 开发流程

| 文档 | 说明 |
|------|------|
| [docs/development-workflow.md](./docs/development-workflow.md) | 研发流程指南（分支、提交、测试规范） |

---

## 🆘 快速导航

### 我是新加入的开发者
1. 先读 [AGENTS.md](./AGENTS.md) 了解红线规则和环境信息
2. 按 [docs/development-workflow.md](./docs/development-workflow.md) 了解研发流程
3. 查看 [docs/TODO.md](./docs/TODO.md) 了解当前迭代状态

### 我要部署到生产环境
1. 阅读 [docs/deployment-guide.md](./docs/deployment-guide.md)
2. 检查 [CHANGELOG.md](./CHANGELOG.md) 确认版本变更
3. 遵循 [AGENTS.md](./AGENTS.md) 的发版/部署流程

### 我要开发新功能
1. 确认需求已讨论并形成方案（参考 [docs/TODO.md](./docs/TODO.md)）
2. 按 [AGENTS.md](./AGENTS.md) 的开发流程规范执行
3. 参考相关架构设计文档

### 我要查询数据（业务人员）
1. SDK 安装：`pip install git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk`
2. API 文档：[docs/api-reference.md](./docs/api-reference.md)
3. 分发平台详情：[docs/requirements/REQ-004.md](./docs/requirements/REQ-004.md)

---

## 📝 文档维护

- **谁开发，谁更新**：开发人员负责更新自己修改的文档
- **命名规范**：kebab-case（如 `deploy-guide.md`），例外：`README.md`, `CHANGELOG.md`, `AGENTS.md`, `TODO.md`
- **文档复用**：同一主题只保留一份文档，迭代时更新而非新建 `xxx_v2.md`

---

*最后更新：2026-05-09*

# JQData 本地行情数据平台

基于聚宽 JQData 正式版构建的本地化行情数据中台，为内部量化策略、投研分析提供统一数据服务。

## 项目状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| 需求确认 | 🟡 进行中 | 待用户确认首批需求范围 |
| 技术方案 | 🟢 已完成 | 见 `docs/02-architecture.md` |
| 表结构设计 | 🟢 已完成 | 见 `docs/03-database-schema.md` |
| 本地开发 | ⚪ 未开始 | 等需求确认后启动 |
| 测试验证 | ⚪ 未开始 | — |
| 生产部署 | ⚪ 未开始 | — |

## 基础设施

| 服务器 | 配置 | 角色 |
|--------|------|------|
| D `101.132.161.52` | 8核 64GB | ClickHouse主 + Redis + FastAPI |
| C `139.196.186.67` | 4核 32GB | ClickHouse从 + Grafana + Airflow |
| A `106.14.141.212` | 4核 16GB | 应用服务器（现有业务，不动） |
| B `139.196.34.92` | 4核 16GB | 测试服务器（现有环境，保留） |

## 快速链接

- [AI 操作约束](./AGENTS.md)
- [需求池](./docs/TODO.md)
- [开发流程](./docs/development-workflow.md)
- [技术架构](./docs/02-architecture.md)
- [表结构设计](./docs/03-database-schema.md)
- [API 接口规范](./docs/04-api-spec.md)
- [部署手册](./docs/05-deployment.md)
# Deploy Key验证 2026-05-09 09:29:47

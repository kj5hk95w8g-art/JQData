# JQData 本地行情数据平台

> **定位**：基于聚宽 JQData 正式版构建的本地化行情数据中台，为内部量化策略、投研分析提供统一数据服务。

## 核心职责

1. **行情数据存储**：股票日线（前/后复权）、指数日线、财务数据、市值数据等
2. **数据服务**：REST API + Python SDK，支持批量查询、签名认证
3. **数据同步**：每日收盘后从聚宽同步增量数据到 ClickHouse

## 技术栈

- 存储：ClickHouse + Redis
- 服务：FastAPI + Nginx（限流 500r/s）
- 部署：Docker Compose（D 服务器）

## 快速开始

```bash
# 本地测试
pip install -e src/sdk
python -c "from jqdata_sdk import auth, get_price; auth('your-key'); print(get_price('000001.XSHE'))"

# 服务器部署
./scripts/deploy.sh d    # D 服务器
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/database-schema.md](docs/database-schema.md) | ClickHouse 表结构 |
| [docs/api-reference.md](docs/api-reference.md) | REST API 接口规范 |
| [docs/deployment-guide.md](docs/deployment-guide.md) | 部署运维手册 |
| [AGENTS.md](AGENTS.md) | 开发规范 |

## 相关项目

- [SSOT](https://github.com/kj5hk95w8g-art/SSOT) — 净值数据源（独立系统）

## 基础设施

| 服务器 | 配置 | 角色 | SSH |
|--------|------|------|-----|
| D `101.132.161.52` | 8核 64GB | ClickHouse + Redis + FastAPI + Nginx + Prometheus | `ssh jqdata-d` |
| C `139.196.186.67` | 4核 32GB | Grafana 可视化 | `ssh jqdata-c` |

> A/B 服务器由应用层项目自行维护。

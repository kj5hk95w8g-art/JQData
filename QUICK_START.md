# JQData 快速入门

> 第一次进入项目？按你的角色选路线，5分钟知道该看什么。

---

## 我是业务人员（用 Python 取数据）

**目标**：安装 SDK，查询股票/指数数据

| 步骤 | 文档 | 时间 |
|------|------|------|
| 1. 安装 SDK | [docs/REQ-004-spec.md](./docs/REQ-004-spec.md) → "SDK安装与使用" | 2分钟 |
| 2. 写代码取数 | [docs/04-api-spec.md](./docs/04-api-spec.md) | 3分钟 |
| 3. 遇到问题 | 问管理员或看 SDK 源码 `src/sdk/jqdata_sdk/api.py` | — |

**一行安装**：
```bash
pip install git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

**快速查询**：
```python
import jqdata_sdk as jq
df = jq.get_price("000001.XSHE", start_date="2024-01-01", end_date="2026-05-08")
```

---

## 我是开发人员（改代码/加功能）

**目标**：了解项目结构，知道改哪里

| 步骤 | 文档 | 时间 |
|------|------|------|
| 1. 项目背景和红线 | [AGENTS.md](./AGENTS.md) | 5分钟 |
| 2. 技术架构 | [docs/REQ-004-architecture.md](./docs/REQ-004-architecture.md) | 5分钟 |
| 3. 数据库表结构 | [docs/03-database-schema.md](./docs/03-database-schema.md) | 3分钟 |
| 4. API 接口规范 | [docs/04-api-spec.md](./docs/04-api-spec.md) | 3分钟 |
| 5. 开发流程 | [docs/development-workflow.md](./docs/development-workflow.md) | 2分钟 |

**核心代码位置**：
```
src/
  main.py              # FastAPI 服务入口
  sync_daily.py        # 数据同步脚本
  sdk/jqdata_sdk/      # Python SDK
    client.py          # HTTP 客户端 + 自动签名
    api.py             # 查询接口（get_price 等）
deploy/
  docker-compose.d.yml # D 服务器编排
  docker-compose.c.yml # C 服务器编排
  nginx.conf           # Nginx 限流配置
scripts/
  release.sh           # 本地发版（打 tag）
  deploy.sh            # 服务器部署
  health-check.sh      # 健康检查
```

---

## 我是运维人员（管服务器/发版）

**目标**：部署、监控、发版、故障排查

| 步骤 | 文档 | 时间 |
|------|------|------|
| 1. 服务器架构 | [docs/06-container-ops.md](./docs/06-container-ops.md) → "一、架构概览" | 3分钟 |
| 2. 日常运维命令 | [docs/06-container-ops.md](./docs/06-container-ops.md) → "三、日常运维命令" | 5分钟 |
| 3. 发版部署流程 | [docs/06-container-ops.md](./docs/06-container-ops.md) → "七、回滚参考" + 本节下方 | 3分钟 |
| 4. 监控告警 | [docs/06-container-ops.md](./docs/06-container-ops.md) → "五、监控栈" | 3分钟 |

**服务器速查**：

| 服务器 | IP | 角色 | 管理命令 |
|--------|----|------|---------|
| D | `101.132.161.52` | 核心数据（ClickHouse/Redis/API） | `ssh jqdata-d` |
| C | `139.196.186.67` | 可视化（Grafana） | `ssh jqdata-c` |

**发版命令**：
```bash
# 1. 本地打 tag
./scripts/release.sh patch

# 2. 部署到 D 服务器
ssh jqdata-d 'cd /data/jqdata-platform && ./scripts/deploy.sh v0.1.x'

# 3. 部署到 C 服务器
ssh jqdata-c 'cd /data/jqdata-platform && ./scripts/deploy.sh v0.1.x'
```

---

## 我是 AI 助手（Kimi Code CLI）

**必读**：
1. [AGENTS.md](./AGENTS.md) — 红线规则（违规即停止）
2. [CONSTRAINTS.md](./CONSTRAINTS.md) — 权限边界
3. [docs/06-container-ops.md](./docs/06-container-ops.md) — 运维手册（改配置前先看）

---

## 文档速查表

| 想知道什么 | 看哪份文档 |
|-----------|-----------|
| 项目背景、红线规则 | [AGENTS.md](./AGENTS.md) |
| 数据库表结构 | [docs/03-database-schema.md](./docs/03-database-schema.md) |
| API 接口列表 | [docs/04-api-spec.md](./docs/04-api-spec.md) |
| 服务器怎么管、怎么部署 | [docs/06-container-ops.md](./docs/06-container-ops.md) |
| 分发平台设计（签名/SDK） | [docs/REQ-004-spec.md](./docs/REQ-004-spec.md) |
| 技术架构 | [docs/REQ-004-architecture.md](./docs/REQ-004-architecture.md) |
| 开发流程、分支规范 | [docs/development-workflow.md](./docs/development-workflow.md) |
| 待办需求 | [docs/TODO.md](./docs/TODO.md) |
| 变更历史 | [CHANGELOG.md](./CHANGELOG.md) |

---

*最后更新：2026-05-09*

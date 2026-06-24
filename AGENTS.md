# JQData 本地行情数据平台 — AI 助手操作约束

> 本文档是 AI 助手的**强制性操作约束手册**。违反任何红线规则 → 立即停止操作。
> 通用规则继承 `~/.ai-rules/`（001~007），本文档只包含 **JQData 特有规则**。
>
> 完整文档导航 → [`README.md`](./README.md)

---

## 一、红线规则（项目特有补充）

通用红线见 `~/.ai-rules/002-security.yml`，以下为本项目特有：

| 红线 | 说明 |
|------|------|
| ❌ 修改生产数据库表结构 | 需用户明确"同意"，且必须先备份 |
| ❌ 擅自重启 ClickHouse / Redis 生产实例 | 需用户明确"同意"，先确认无活跃查询 |
| ❌ 直接操作 JQData 账号做测试 | 测试必须用本地数据集，禁止浪费正式账号流量 |
| ❌ 将行情数据导出到公网/外发 | 金融数据仅限内网使用 |
| ❌ 在全市场范围执行无限制查询 | 查询必须带时间范围和标的限制 |

---

## 二、环境信息

### 2.1 服务器定义（ABCD 标准命名）

| 代号 | 角色 | 公网IP | 配置 | 部署内容 | 约束 |
|------|------|--------|------|---------|------|
| **A** | 应用服务器 | `106.14.141.212` | 4核 16GB | 资产沃土/云图中心（核心业务❌不动）+ **node-exporter（监控，经同意部署）** | 核心业务禁止部署新组件 |
| **B** | 测试服务器 | `139.196.34.92` | 4核 16GB | **❌ 不动**（现有测试环境） | 禁止修改 |
| **C** | 可视化/调度 | `139.196.186.67` | 4核 32GB | Grafana + Airflow（待建） | docker-compose，deploy 用户 |
| **D** | 核心数据层 | `101.132.161.52` | 8核 64GB | ClickHouse + Redis + FastAPI + Nginx | docker-compose，deploy 用户 |

**网络：** 阿里云 VPC `172.24.52.0/24`，内网互通。C/D 数据服务端口仅开放内网访问。

**SSH 配置：**
```
Host jqdata-c
    HostName 139.196.186.67
    User deploy
Host jqdata-d
    HostName 101.132.161.52
    User deploy
```

**项目路径：** `/data/jqdata-platform`（C/D 服务器）

### 2.2 容器与服务

| 服务 | 容器名 | 端口 | 位置 |
|------|--------|------|------|
| ClickHouse | `clickhouse` | 8123(HTTP) / 9000(Native) | D 服务器 |
| Redis | `redis` | 6379 | D 服务器 |
| FastAPI | `api` | 8000（仅容器内） | D 服务器 |
| Nginx | `nginx` | 18080（公网暴露） | D 服务器 |
| Prometheus | `prometheus` | 9090 | D 服务器 |
| node-exporter | `node-exporter` | 9100 | D 服务器 |
| Grafana | `grafana` | 3000 | C 服务器 |

**配置分离：** `docker-compose.*.yml` / `.env.example` 在 Git（只读）；`.env.production` 在服务器本地（❌ 禁止直接修改）。

---

## 三、分支/发版/部署

通用规范见 [`~/.ai-rules/001-git.yml`](~/.ai-rules/001-git.yml) 和 [`~/.ai-rules/004-deployment.yml`](~/.ai-rules/004-deployment.yml)。

本项目特有：

| 分支 | 用途 | 合并目标 |
|------|------|---------|
| `main` | 唯一发版分支 | 禁止直接 push |
| `develop` | 开发集成 | feature/fix 合并目标 |
| `feature/xxx` | 新功能 | → `develop` |
| `fix/xxx` | Bug 修复 | → `develop` |
| `hotfix/xxx` | 紧急修复 | 从 `main` 切出，**必须同步到 `develop`** |

- **发版：** `./scripts/release.sh patch`（或 minor / major）
- **部署：** SSH 登录服务器执行 `./scripts/deploy.sh v0.1.x`

**数据变更：** 表结构变更必须写成迁移脚本（`migrations/VXXX__description.sql`），禁止手动 `ALTER TABLE`。

---

## 四、需求管理

通用规范见 [`~/.ai-rules/005-documentation.yml`](~/.ai-rules/005-documentation.yml)。

本项目特有：

- 状态流转：`待评审 → 已排期 → 开发中 → 待测试 → 已发布`
- 编号：`REQ-NNN`
- Commit 格式：`类型(范围): 描述` + `Closes REQ-XXX`
- 类型：`feat` \| `fix` \| `data` \| `perf` \| `refactor` \| `docs` \| `test` \| `deploy` \| `chore`

---

## 五、文档管理

通用规范见 [`~/.ai-rules/005-documentation.yml`](~/.ai-rules/005-documentation.yml)。

本项目特有：

- 命名：kebab-case（例外：`README.md`, `CHANGELOG.md`, `AGENTS.md`, `TODO.md`）
- 谁修改谁更新，废弃文件移到 `docs/archive/`
- 表结构变更必须同步 `docs/database-schema.md`
- API 变更必须同步 `docs/api-reference.md`
- 新增文档准入：全新领域 / 现有文档超 500 行 / 说明为何不更新现有文档

---

## 六、数据操作规范

### 6.1 查询约束

| 操作类型 | 约束 |
|---------|------|
| 全表扫描 | 禁止，必须带 `WHERE code = ?` 或 `WHERE trade_date >= ?` |
| 跨市场查询 | 必须限制标的数量，单次最多 1000 只 |
| 分钟线大范围查询 | 单次最多 30 天数据 |
| `SELECT *` | 禁止，必须指定字段 |
| `JOIN` 操作 | 优先在应用层做，避免大数据量 JOIN |

### 6.2 写入约束

| 操作类型 | 约束 |
|---------|------|
| 批量插入 | 使用 `INSERT INTO ... VALUES` 批量，单批 1000~10000 条 |
| 单条写入 | 禁止，性能极差 |
| 重复写入 | 必须幂等，使用 `ReplacingMergeTree` 或插入前查重 |
| 删除数据 | 禁止 `DELETE`，使用 `ALTER TABLE ... DROP PARTITION` 或 TTL |

### 6.3 JQData 流量管理

- 正式账号每日 2 亿条额度（详见 `docs/jqdata-data-catalog.md`）
- 全市场日线约 5200 条/天，分钟线约 120 万条/天
- 历史回填必须分批次，避免单日额度耗尽
- 同步脚本必须记录流量使用

---

## 七、Python 与测试规范

通用规范见 `~/.ai-rules/`：

- **`003-python.yml`**：配置走环境变量、新增环境变量同步 `.env.example`、公共函数类型注解建议、优先使用 logging、敏感信息禁止入代码库
- **`006-testing.yml`**：核心逻辑单元测试覆盖率建议 > 70%、外部依赖必须 mock、数据计算类必须测试边界条件、每个任务交付前须有验收脚本、测试禁止使用生产数据

---

## 八、AI Skill 速查

> 通用 Skill 从 `~/.kimi/skills/` 自动加载；项目特有 Skill 从 `.agents/skills/` 加载。

| 场景 | 直接说 | Skill | 位置 |
|------|--------|-------|------|
| 检查代码 | "检查代码" / "review代码" | `code-quality-guard` | `~/.kimi/skills/` |
| 检查表结构 | "检查表结构" / "schema对不对" | `schema-validator` | `.agents/skills/` |
| 数据同步检查 | "检查同步状态" / "今天数据齐了吗" | `sync-status-checker` | `.agents/skills/` |
| 数据质量检查 | "检查数据质量" / "有没有缺数据" | `data-quality-checker` | `.agents/skills/` |
| 性能分析 | "查询好慢" / "优化一下" | `query-performance-analyzer` | `.agents/skills/` |
| 部署检查 | "准备部署" / "检查部署条件" | `deploy-guard` | `.agents/skills/` |
| API 测试 | "测试API" / "接口通不通" | `api-smoke-test` | `~/.kimi/skills/` |
| 因子计算 | "计算MA" / "算一下RSI" | `factor-calculator` | `.agents/skills/` |

---

## 九、快速参考

| 内容 | 文档 |
|------|------|
| 完整文档导航 | `README.md` |
| 数据库表结构 | `docs/database-schema.md` |
| API 接口规范 | `docs/api-reference.md` |
| 部署运维手册 | `docs/deployment-guide.md` |
| 需求池与当前状态 | `docs/TODO.md` |
| 开发流程 | `docs/development-workflow.md` |
| 变更日志 | `CHANGELOG.md` |

---

## 附录：金融数据安全规范

1. **数据不出内网**：行情数据仅限 VPC 内网访问
2. **禁止截屏外发**：含股票行情的页面禁止截图发到外部
3. **访问审计**：关键查询接口记录访问日志
4. **备份加密**：归档的历史数据文件必须加密存储

---

*最后更新：2026-05-12*  
*版本：v2.3.0 — 瘦身：删除通用规则重复内容，引用 ~/.ai-rules/；增加公共 Skill 池引用*

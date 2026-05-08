# JQData 开发流程 — 闭环规范

> 开发必须闭环：**需求确认 → 方案评审 → 编码实现 → 自测验证 → 代码审查 → 合并发布 → 线上验收**

---

## 一、闭环流程图

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  需求确认 │───▶│  方案评审 │───▶│  编码实现 │───▶│  自测验证 │───▶│  代码审查 │
│ (TODO.md)│    │ (文档)   │    │ (feature)│    │ (tests)  │    │ (PR)    │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
                                                                    │
                                                                    ▼
┌─────────┐    ┌─────────┐    ┌─────────┐
│  线上验收 │◀───│  合并发布 │◀───│  测试环境 │
│ (验收标准)│    │ (main)  │    │ (验证)   │
└─────────┘    └─────────┘    └─────────┘
```

**每个环节必须有输出物，没有输出物 = 环节未完成。**

---

## 二、各环节输出物

| 环节 | 负责人 | 输入 | 输出 | 检查清单 |
|------|--------|------|------|---------|
| **需求确认** | PM/用户 | 业务诉求 | `docs/TODO.md` 中需求状态改为"已排期" | 需求编号、验收标准、优先级明确 |
| **方案评审** | 技术负责人 | 需求文档 | `docs/` 中技术方案/表结构/API文档更新 | 方案经过讨论确认，无重大风险 |
| **编码实现** | 开发者 | 技术方案 | feature分支代码 + 单元测试 | 代码符合规范，有测试覆盖 |
| **自测验证** | 开发者 | 代码 | 自测报告（截图/日志/输出） | 所有验收标准通过 |
| **代码审查** | 审查者 | PR | Review意见 +  approve | 无未解决的阻塞性问题 |
| **测试环境** | 开发者 | 合并到develop | 测试环境部署 + 验证报告 | 功能正常，数据正确 |
| **合并发布** | 负责人 | 测试通过 | main分支tag + 发布说明 | 版本号、变更日志完整 |
| **线上验收** | PM/用户 | 生产部署 | 验收签字/确认 | 所有验收标准在生产环境验证通过 |

---

## 三、Git 工作流

### 分支模型

```
main (生产) ◄────── tag v0.1.0 ◄────── tag v0.2.0
    ▲                              ▲
    │                              │
develop (集成) ─────────────────────────────────
    ▲                              ▲
    │                              │
feature/REQ-003-daily-sync      feature/REQ-005-api
```

### 操作规范

1. **需求确认后**，从 `develop` 切出 feature 分支：
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/REQ-003-daily-sync
   ```

2. **开发完成后**，提交前自检：
   ```bash
   # 代码格式检查
   make lint
   
   # 单元测试
   make test
   
   # 提交（遵循 commitlint 规范）
   git commit -m "feat(sync): 实现日线行情同步 - Closes REQ-003"
   ```

3. **推送到远程**，创建 PR：
   ```bash
   git push origin feature/REQ-003-daily-sync
   # 在 GitHub 创建 PR → target: develop
   ```

4. **PR 合并条件**：
   - [ ] 至少 1 个 approve
   - [ ] CI 检查通过（lint + test）
   - [ ] 无冲突
   - [ ] 关联需求编号在描述中

5. **发版流程**：
   ```bash
   git checkout main
   git merge develop
   git tag v0.1.0
   git push origin main --tags
   ```

---

## 四、开发规范

### 目录规范

```
src/
├── api/              # FastAPI 接口层
│   ├── main.py       # 应用入口
│   ├── routers/      # 路由模块
│   ├── schemas/      # Pydantic 模型
│   └── dependencies/ # 依赖注入
├── sync/             # 数据同步
│   ├── daily.py      # 日线同步
│   ├── minute.py     # 分钟线同步
│   ├── fundamental.py # 财务数据同步
│   └── client.py     # JQData 客户端封装
├── factor/           # 因子计算
│   ├── ma.py         # 移动平均线
│   ├── rsi.py        # RSI
│   └── base.py       # 基础计算工具
├── common/           # 公共模块
│   ├── config.py     # 配置管理
│   ├── db.py         # 数据库连接
│   ├── cache.py      # Redis 缓存
│   └── logger.py     # 日志工具
└── tests/            # 测试
    ├── unit/         # 单元测试
    └── integration/  # 集成测试
```

### 代码规范

- Python: PEP 8 + Black 格式化 + isort 排序
- 类型注解：所有函数参数和返回值必须标注类型
- 文档字符串：公共函数必须写 docstring（Google 风格）
- 日志：使用 `structlog`，禁止 `print()`

### 测试规范

- 单元测试覆盖率 > 70%
- 核心同步逻辑必须 mock JQData API 测试
- API 接口必须测试正常路径和异常路径
- 数据计算类必须测试边界条件

---

## 五、数据开发规范

### 表结构变更流程

1. 在 `migrations/` 创建迁移脚本：
   ```
   migrations/V001__create_stock_daily.sql
   migrations/V002__add_fq_factor.sql
   ```

2. 迁移脚本必须包含：
   - `UP` 脚本（升级）
   - `DOWN` 脚本（回滚）

3. 禁止直接在生产环境执行 `ALTER TABLE`

### 数据验证流程

每次同步任务执行后：
1. 检查 `sync_checkpoint` 表状态
2. 抽样比对 JQData 源数据和本地数据（10条）
3. 检查数据完整性（全市场标的数 vs 实际入库数）
4. 异常数据写入 `data_quality_issues` 表

---

## 六、闭环检查表（每个需求发布前必须勾选）

```markdown
### REQ-XXX: 需求名称

- [ ] 需求文档已更新（TODO.md状态改为"已发布"）
- [ ] 技术方案已评审通过
- [ ] 表结构文档已更新（如有变更）
- [ ] API文档已更新（如有变更）
- [ ] 代码已提交到 feature 分支
- [ ] 单元测试覆盖率达标
- [ ] 自测报告已产出（截图/日志）
- [ ] PR 已合并到 develop
- [ ] 测试环境已部署并验证通过
- [ ] 已合并到 main 并打 tag
- [ ] 生产环境已部署
- [ ] 线上验收通过（用户确认）
- [ ] CHANGELOG.md 已更新
```

---

## 七、当前迭代（Iteration 1）

**目标**: 完成核心数据层 + 日线同步 + 基础API

**范围**:
- REQ-001: 核心数据层部署
- REQ-002: 股票基础信息同步
- REQ-003: 日线行情同步
- REQ-005: REST API — 行情查询
- REQ-009: 定时同步调度

**排期**: 待用户确认后开始

**验收**: 本地开发完成 + 数据回填验证 + API测试通过

---

*最后更新：2026-05-08*

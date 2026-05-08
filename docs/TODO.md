# JQData 需求池

> 状态：`待评审 → 待排期 → 已排期 → 开发中 → 待测试 → 已发布`
>
> 编号规则：`REQ-NNN`
> 决策记录：复权方案选B（前复权+后复权各存一份），指数成分股含权重，正式版所有基础数据全部同步

---

## 需求列表

### REQ-001: 核心数据层部署
- **类型**: deploy
- **状态**: 🟡 待评审
- **优先级**: P0（阻塞后续所有开发）
- **描述**: 在服务器D部署ClickHouse主节点+Redis+FastAPI，服务器C部署ClickHouse从节点+Grafana+Airflow
- **验收标准**:
  - [ ] D和C上Docker正常运行
  - [ ] ClickHouse主从复制正常
  - [ ] Redis可读写
  - [ ] FastAPI服务可访问
  - [ ] Grafana能查看监控面板
  - [ ] Airflow调度器正常运行
- **关联文档**: `docs/05-deployment.md`

---

### REQ-002: 股票基础信息同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 从JQData同步全市场股票、指数、ETF、基金、期货的基础信息
- **API**: `get_all_securities()`, `get_security_info()`
- **验收标准**:
  - [ ] `security_info`表包含全市场标的
  - [ ] 包含字段：code, display_name, name, type, exchange, start_date, end_date
  - [ ] 每日自动更新（新股上市、退市标记、名称变更）
  - [ ] 与JQData源数据比对一致
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-003: 日线行情同步（前复权 + 后复权）
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 同步沪深A股日线数据，前复权和后复权各存一份
- **API**: `get_price(fq='pre')`, `get_price(fq='post')`
- **验收标准**:
  - [ ] `stock_daily_pre` 表（前复权）：全市场，2005年至今
  - [ ] `stock_daily_post` 表（后复权）：全市场，2005年至今
  - [ ] 字段：open, high, low, close, volume, amount, high_limit, low_limit, paused
  - [ ] 每日收盘后自动同步当日数据
  - [ ] 与JQData源数据抽样比对一致
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-004: 分钟线行情同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 同步核心标的1分钟线行情（前复权）
- **API**: `get_price(frequency='1m', fq='pre')`
- **标的范围**: 沪深300+中证500成分股（约800只）
- **验收标准**:
  - [ ] `stock_minute_pre`表包含分钟线
  - [ ] 历史范围：最近2年热存
  - [ ] 字段：open, high, low, close, volume, amount
  - [ ] 每日收盘后自动同步
  - [ ] TTL自动清理超18个月数据
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-005: REST API — 行情查询
- **类型**: feat
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 提供REST接口查询日线/分钟线行情
- **验收标准**:
  - [ ] GET `/v1/daily/{code}` — 查询单股票日线（支持pre/post/none参数）
  - [ ] POST `/v1/daily/batch` — 批量查询日线
  - [ ] GET `/v1/minute/{code}` — 查询单股票分钟线
  - [ ] 支持时间范围筛选
  - [ ] 支持字段筛选
  - [ ] P95响应时间 < 200ms
  - [ ] 接口文档自动生成（Swagger/OpenAPI）
- **关联文档**: `docs/04-api-spec.md`

---

### REQ-006: 股票列表与基础信息查询API
- **类型**: feat
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 提供接口查询股票基础信息
- **验收标准**:
  - [ ] GET `/v1/stocks` — 查询全市场股票列表
  - [ ] 支持按类型筛选（stock/etf/index/futures）
  - [ ] 支持按交易所筛选
  - [ ] 支持关键词搜索（名称/代码）
- **关联文档**: `docs/04-api-spec.md`

---

### REQ-007: 技术指标计算 — MA/RSI/MACD
- **类型**: feat
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 提供移动平均线(MA)、相对强弱指标(RSI)、MACD计算接口
- **验收标准**:
  - [ ] GET `/v1/factor/ma?code=xxx&window=20` — 计算MA
  - [ ] GET `/v1/factor/rsi?code=xxx&window=14` — 计算RSI
  - [ ] GET `/v1/factor/macd?code=xxx` — 计算MACD
  - [ ] 结果与聚宽平台计算结果一致（误差<0.01%）
  - [ ] P95 < 500ms
- **关联文档**: `docs/04-api-spec.md`

---

### REQ-008: 数据质量监控
- **类型**: feat
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 建立数据质量检查机制，监控同步完整性
- **验收标准**:
  - [ ] 每日检查全市场日线数据完整性
  - [ ] 检测缺失的标的和日期
  - [ ] 检测价格异常（涨停价错误、停牌未标记等）
  - [ ] Grafana面板展示数据质量指标
  - [ ] 异常自动报警（日志/邮件）
- **关联文档**: `docs/05-deployment.md`

---

### REQ-009: 定时同步调度
- **类型**: deploy
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: Airflow DAG调度每日数据同步任务，仅交易日触发
- **验收标准**:
  - [ ] 交易日17:30自动触发日线同步
  - [ ] 交易日18:00自动触发分钟线同步
  - [ ] 支持手动重跑某日数据
  - [ ] 失败自动重试（3次，间隔5分钟）
  - [ ] 同步状态记录到`sync_checkpoint`表
- **关联文档**: `docs/05-deployment.md`

---

### REQ-010: 应用服务器对接
- **类型**: feat
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: A服务器上的应用通过内网调用数据服务
- **验收标准**:
  - [ ] A服务器应用可访问D的FastAPI（172.24.52.237:8000）
  - [ ] 提供Python SDK或调用示例给应用团队
  - [ ] 记录访问日志
- **关联文档**: `docs/04-api-spec.md`

---

### REQ-011: 指数行情与成分股同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 同步主要宽基指数日线行情、成分股及权重
- **API**: `get_price()`, `get_index_stocks()`, `get_index_weights()`
- **指数列表**: 000001.XSHG, 000300.XSHG, 000905.XSHG, 399001.XSHE, 399006.XSHE, 000016.XSHG 等
- **验收标准**:
  - [ ] `index_daily`表：指数日线行情
  - [ ] `index_component`表：指数成分股及权重（月度更新）
  - [ ] 支持查询某股票属于哪些指数
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-012: 基金行情同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 同步场内基金（ETF/LOF）日线和净值数据
- **API**: `get_price()`, `get_extras()`, `get_fund_info()`
- **验收标准**:
  - [ ] `fund_daily`表：基金日线行情
  - [ ] `fund_nav`表：基金净值
  - [ ] `fund_info`表：基金基础信息
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-013: 期货行情同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 同步商品期货、金融期货日线行情
- **API**: `get_price()`, `get_futures_info()`
- **验收标准**:
  - [ ] `futures_daily`表：期货日线行情
  - [ ] `futures_info`表：期货合约信息
  - [ ] 主力合约标记
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-014: 财务数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 同步上市公司财务数据（利润表、资产负债表、现金流量表）
- **API**: `get_history_fundamentals()`, `run_query()`
- **注意**: run_query每次最多5000行，需分页处理
- **验收标准**:
  - [ ] `income_statement`表：利润表
  - [ ] `balance_sheet`表：资产负债表
  - [ ] `cash_flow`表：现金流量表
  - [ ] 支持按报告期查询（单季度/年度）
  - [ ] 与JQData源数据抽样比对一致
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-015: 市值数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 同步股票市值表数据（总市值、流通市值、PE、PB等）
- **API**: `get_valuation()`
- **验收标准**:
  - [ ] `stock_valuation`表：市值指标
  - [ ] 字段：market_cap, circulating_market_cap, pe_ratio, pb_ratio, ps_ratio, pcf_ratio
  - [ ] 日频更新
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-016: 行业与概念数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P1
- **描述**: 同步行业分类、概念板块及成分股
- **API**: `get_industries()`, `get_industry_stocks()`, `get_concepts()`, `get_concept_stocks()`
- **验收标准**:
  - [ ] `industry_info`表：行业列表
  - [ ] `industry_component`表：行业成分股
  - [ ] `concept_info`表：概念板块列表
  - [ ] `concept_component`表：概念成分股
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-017: 融资融券数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P2
- **描述**: 同步融资融券交易数据
- **API**: `get_mtss()`
- **验收标准**:
  - [ ] `margin_trading`表：融资融券数据
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-018: 资金流数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P2
- **描述**: 同步个股资金流数据
- **API**: `get_money_flow()`
- **验收标准**:
  - [ ] `money_flow`表：资金流数据
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-019: 龙虎榜数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P2
- **描述**: 同步龙虎榜交易数据
- **API**: `get_billboard_list()`
- **验收标准**:
  - [ ] `billboard`表：龙虎榜数据
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-020: 限售解禁数据同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P2
- **描述**: 同步限售股解禁数据
- **API**: `get_locked_shares()`
- **验收标准**:
  - [ ] `locked_shares`表：限售解禁数据
- **关联文档**: `docs/03-database-schema.md`

---

### REQ-021: 交易日历同步
- **类型**: data
- **状态**: 🟡 待评审
- **优先级**: P0
- **描述**: 同步中国A股交易日历，作为调度依据
- **API**: `get_trade_days()`, `get_all_trade_days()`
- **验收标准**:
  - [ ] `trade_calendar`表：所有交易日
  - [ ] Airflow DAG仅在交易日触发
  - [ ] 支持判断某天是否为交易日
- **关联文档**: `docs/03-database-schema.md`

---

## 需求统计

| 优先级 | 数量 | 需求编号 |
|--------|------|---------|
| P0 | 7 | REQ-001, REQ-002, REQ-003, REQ-005, REQ-008, REQ-009, REQ-011, REQ-014, REQ-021 |
| P1 | 8 | REQ-004, REQ-006, REQ-007, REQ-010, REQ-012, REQ-013, REQ-015, REQ-016 |
| P2 | 4 | REQ-017, REQ-018, REQ-019, REQ-020 |
| **合计** | **19** | |

---

## ⚠️ 存储预警

"能下的都要下"意味着数据量大幅增加：

| 数据类型 | 预估年增量 | 5年累计 |
|---------|-----------|--------|
| 股票日线（前复权+后复权） | ~6 GB | ~30 GB |
| 指数日线 | ~0.5 GB | ~2.5 GB |
| 基金日线+净值 | ~1 GB | ~5 GB |
| 期货日线 | ~0.5 GB | ~2.5 GB |
| 财务数据 | ~2 GB | ~10 GB |
| 市值数据 | ~3 GB | ~15 GB |
| 分钟线（800只） | ~8 GB | ~40 GB |
| 其他（行业/概念/融资融券等） | ~1 GB | ~5 GB |
| **合计** | | **~110 GB** |

**200GB 系统盘够用，但余量紧张。建议迭代2开始前给D加一块数据盘。**

---

## 迭代规划

### Iteration 1（MVP）
REQ-001, REQ-002, REQ-003, REQ-005, REQ-009, REQ-021, REQ-011, REQ-014, REQ-008

### Iteration 2（扩展）
REQ-004, REQ-006, REQ-007, REQ-010, REQ-012, REQ-013, REQ-015, REQ-016

### Iteration 3（特色数据）
REQ-017, REQ-018, REQ-019, REQ-020

---

## 变更记录

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-05-08 | v0.1.0 | 初始需求池，10个需求 |
| 2026-05-08 | v0.2.0 | **大幅扩展至19个需求**，增加：指数/基金/期货/财务/市值/行业概念/融资融券/资金流/龙虎榜/限售解禁/交易日历；明确复权方案B；增加存储预警 |

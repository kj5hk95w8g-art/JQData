# 数据库表结构设计

> 本文档基于 **JQData API 实际探测结果** 编写（账号：18918601977，探测时间：2026-05-08）。
>
> 策略：自定义规范 + 映射层。表字段采用清晰命名，同步程序负责 JQData → ClickHouse 字段映射。

---

## 一、字段映射总览

### 1.1 标的/基础信息

| JQData API | JQ返回字段 | 我们的表字段 | 类型 | 说明 |
|-----------|-----------|-------------|------|------|
| `get_all_securities()` | `display_name` | `display_name` | String | 显示名称 |
| | `name` | `name` | String | 拼音简称 |
| | `start_date` | `start_date` | Date | 上市日期 |
| | `end_date` | `end_date` | Date | 退市日期（2200-01-01表示未退市） |
| | `type` | `type` | LowCardinality(String) | stock/index/etf/futures |
| `get_index_stocks()` | list item | `code` | String | 指数成分股代码 |
| `get_index_weights()` | index → `code` | `code` | String | 股票代码 |
| | `date` | `trade_date` | Date | 权重日期 |
| | `weight` | `weight` | Float64 | 权重（%） |
| | `display_name` | `display_name` | String | 显示名称 |

### 1.2 行情数据

| JQData API | JQ返回字段 | 我们的表字段 | 类型 | 说明 |
|-----------|-----------|-------------|------|------|
| `get_price()` | `open` | `open` | Float64 | 开盘价 |
| | `close` | `close` | Float64 | 收盘价 |
| | `high` | `high` | Float64 | 最高价 |
| | `low` | `low` | Float64 | 最低价 |
| | `volume` | `volume` | Float64 | 成交量（股） |
| | `money` | `amount` | Float64 | 成交额（元） |
| | `factor` | `fq_factor` | Float64 | 复权因子 |
| | `high_limit` | `high_limit` | Float64 | 涨停价 |
| | `low_limit` | `low_limit` | Float64 | 跌停价 |
| | `avg` | `avg_price` | Float64 | 均价 |
| | `pre_close` | `pre_close` | Float64 | 昨收价 |
| | `paused` | `paused` | UInt8 | 是否停牌：0正常，1停牌 |

> **注意**：JQ 的 `volume` 和 `money` 返回类型是 `float64`，但 `volume` 实际是整数（股数）。入库时按 Float64 存，查询时可转整型。

### 1.3 市值数据

| JQData API | JQ返回字段 | 我们的表字段 | 类型 |
|-----------|-----------|-------------|------|
| `get_valuation()` | `code` | `code` | String |
| | `day` | `trade_date` | Date |
| | `pe_ratio` | `pe_ratio` | Float64 |
| | `turnover_ratio` | `turnover_ratio` | Float64 |
| | `pb_ratio` | `pb_ratio` | Float64 |
| | `ps_ratio` | `ps_ratio` | Float64 |
| | `pcf_ratio` | `pcf_ratio` | Float64 |
| | `capitalization` | `total_shares` | Float64 | 总股本（万股） |
| | `market_cap` | `market_cap` | Float64 | 总市值（亿元） |
| | `circulating_cap` | `circulating_shares` | Float64 | 流通股本（万股） |
| | `circulating_market_cap` | `circulating_market_cap` | Float64 | 流通市值（亿元） |
| | `pe_ratio_lyr` | `pe_ratio_lyr` | Float64 | 静态PE |
| | `pcf_ratio2` | `pcf_ratio2` | Float64 | PCF |
| | `dividend_ratio` | `dividend_ratio` | Float64 | 股息率 |
| | `free_cap` | `free_shares` | Float64 | 自由流通股本 |
| | `free_market_cap` | `free_market_cap` | Float64 | 自由流通市值 |
| | `a_cap` | `a_shares` | Float64 | A股股本 |
| | `a_market_cap` | `a_market_cap` | Float64 | A股市值 |

### 1.4 融资融券

| JQData API | JQ返回字段 | 我们的表字段 | 类型 |
|-----------|-----------|-------------|------|
| `get_mtss()` | `date` | `trade_date` | Date |
| | `sec_code` | `code` | String |
| | `fin_value` | `fin_value` | Float64 | 融资余额 |
| | `fin_buy_value` | `fin_buy_value` | Float64 | 融资买入额 |
| | `fin_refund_value` | `fin_refund_value` | Float64 | 融资偿还额 |
| | `sec_value` | `sec_value` | Float64 | 融券余量 |
| | `sec_sell_value` | `sec_sell_value` | Float64 | 融券卖出额 |
| | `sec_refund_value` | `sec_refund_value` | Float64 | 融券偿还额 |
| | `fin_sec_value` | `fin_sec_value` | Float64 | 融资融券余额 |

### 1.5 龙虎榜

| JQData API | JQ返回字段 | 我们的表字段 | 类型 |
|-----------|-----------|-------------|------|
| `get_billboard_list()` | `code` | `code` | String |
| | `day` | `trade_date` | Date |
| | `direction` | `direction` | String | BUY/SELL |
| | `rank` | `rank` | UInt8 | 排名 |
| | `abnormal_code` | `abnormal_code` | UInt32 | 异动代码 |
| | `abnormal_name` | `abnormal_name` | String | 异动名称 |
| | `sales_depart_name` | `sales_depart_name` | String | 营业部名称 |
| | `buy_value` | `buy_value` | Float64 | 买入额 |
| | `buy_rate` | `buy_rate` | Float64 | 买入占比 |
| | `sell_value` | `sell_value` | Float64 | 卖出额 |
| | `sell_rate` | `sell_rate` | Float64 | 卖出占比 |
| | `total_value` | `total_value` | Float64 | 总成交额 |
| | `net_value` | `net_value` | Float64 | 净额 |
| | `amount` | `amount` | Float64 | 成交额 |

### 1.6 基金净值

| JQData API | JQ返回字段 | 我们的表字段 | 类型 |
|-----------|-----------|-------------|------|
| `get_extras()` | index → `trade_date` | `trade_date` | Date |
| | column → `code` | `code` | String |
| | value | `unit_net_value` | Float64 | 单位净值 |

### 1.7 限售解禁

| JQData API | JQ返回字段 | 我们的表字段 | 类型 |
|-----------|-----------|-------------|------|
| `get_locked_shares()` | `day` | `trade_date` | Date |
| | `code` | `code` | String |
| | `num` | `num` | Float64 | 解禁股数 |
| | `rate1` | `rate1` | Float64 | 占总股本比例 |
| | `rate2` | `rate2` | Float64 | 占流通股本比例 |

---

## 二、ClickHouse 建表语句

### 2.1 标的基础信息表

```sql
CREATE TABLE IF NOT EXISTS security_info (
    code String COMMENT '标的代码，如000001.XSHE',
    display_name String COMMENT '显示名称',
    name String COMMENT '拼音简称',
    type LowCardinality(String) COMMENT '类型：stock/index/etf/futures',
    exchange LowCardinality(String) COMMENT '交易所',
    start_date Date COMMENT '上市日期',
    end_date Date COMMENT '退市日期，2200-01-01表示未退市',
    updated_at DateTime DEFAULT now() COMMENT '更新时间'
) ENGINE = ReplicatedReplacingMergeTree(updated_at)
ORDER BY code;
```

### 2.2 股票日线（前复权）

```sql
CREATE TABLE IF NOT EXISTS stock_daily_pre (
    code LowCardinality(String) COMMENT '股票代码',
    trade_date Date COMMENT '交易日',
    open Float64 CODEC(Delta, LZ4) COMMENT '开盘价',
    high Float64 CODEC(Delta, LZ4) COMMENT '最高价',
    low Float64 CODEC(Delta, LZ4) COMMENT '最低价',
    close Float64 CODEC(Delta, LZ4) COMMENT '收盘价',
    volume Float64 CODEC(Delta, LZ4) COMMENT '成交量（股）',
    amount Float64 CODEC(Delta, LZ4) COMMENT '成交额（元）',
    fq_factor Float64 COMMENT '复权因子',
    high_limit Float64 COMMENT '涨停价',
    low_limit Float64 COMMENT '跌停价',
    avg_price Float64 COMMENT '均价',
    pre_close Float64 COMMENT '昨收价',
    paused UInt8 COMMENT '是否停牌：0正常，1停牌',
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/stock_daily_pre', '{replica}')
PARTITION BY toYear(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

### 2.3 股票日线（后复权）

```sql
CREATE TABLE IF NOT EXISTS stock_daily_post (
    code LowCardinality(String) COMMENT '股票代码',
    trade_date Date COMMENT '交易日',
    open Float64 CODEC(Delta, LZ4),
    high Float64 CODEC(Delta, LZ4),
    low Float64 CODEC(Delta, LZ4),
    close Float64 CODEC(Delta, LZ4),
    volume Float64 CODEC(Delta, LZ4),
    amount Float64 CODEC(Delta, LZ4),
    fq_factor Float64 COMMENT '复权因子',
    high_limit Float64,
    low_limit Float64,
    avg_price Float64,
    pre_close Float64,
    paused UInt8,
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/stock_daily_post', '{replica}')
PARTITION BY toYear(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

### 2.4 指数日线

```sql
CREATE TABLE IF NOT EXISTS index_daily (
    code LowCardinality(String) COMMENT '指数代码',
    trade_date Date COMMENT '交易日',
    open Float64 CODEC(Delta, LZ4),
    high Float64 CODEC(Delta, LZ4),
    low Float64 CODEC(Delta, LZ4),
    close Float64 CODEC(Delta, LZ4),
    volume Float64 CODEC(Delta, LZ4),
    amount Float64 CODEC(Delta, LZ4),
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/index_daily', '{replica}')
PARTITION BY toYear(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

### 2.5 指数成分股权重

```sql
CREATE TABLE IF NOT EXISTS index_weights (
    index_code LowCardinality(String) COMMENT '指数代码，如000300.XSHG',
    code String COMMENT '成分股代码',
    date String COMMENT '权重日期',
    weight Float64 COMMENT '权重（%）',
    display_name String COMMENT '成分股名称',
    index_name String COMMENT '指数名称',
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (index_code, date, code)
SETTINGS index_granularity = 8192;
```

**同步脚本:** `src/sync_index_weights.py`（全量 + 增量 30 天）

覆盖基准指数：沪深300、中证500、中证1000、中证2000、国证2000、中证A500、上证指数、上证50、中证800、深证成指、深证100、创业板指、科创50、国债指数、300价值、300成长、中证红利，共 17 个。

### 2.6 股票分钟线（前复权）

```sql
CREATE TABLE IF NOT EXISTS stock_minute_pre (
    code LowCardinality(String) COMMENT '股票代码',
    trade_time DateTime COMMENT '交易时间',
    open Float64 CODEC(Delta, LZ4),
    high Float64 CODEC(Delta, LZ4),
    low Float64 CODEC(Delta, LZ4),
    close Float64 CODEC(Delta, LZ4),
    volume Float64 CODEC(Delta, LZ4),
    amount Float64 CODEC(Delta, LZ4),
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/stock_minute_pre', '{replica}')
PARTITION BY toYYYYMM(trade_time)
ORDER BY (code, trade_time)
TTL trade_time + INTERVAL 18 MONTH
SETTINGS index_granularity = 8192;
```

### 2.7 市值数据

```sql
CREATE TABLE IF NOT EXISTS valuation (
    code LowCardinality(String) COMMENT '股票代码',
    trade_date Date COMMENT '日期',
    pe_ratio Float64 COMMENT '市盈率TTM',
    turnover_ratio Float64 COMMENT '换手率',
    pb_ratio Float64 COMMENT '市净率',
    ps_ratio Float64 COMMENT '市销率',
    pcf_ratio Float64 COMMENT '市现率',
    total_shares Float64 COMMENT '总股本（万股）',
    market_cap Float64 COMMENT '总市值（亿元）',
    circulating_shares Float64 COMMENT '流通股本（万股）',
    circulating_market_cap Float64 COMMENT '流通市值（亿元）',
    pe_ratio_lyr Float64 COMMENT '静态市盈率',
    pcf_ratio2 Float64 COMMENT '市现率2',
    dividend_ratio Float64 COMMENT '股息率',
    free_shares Float64 COMMENT '自由流通股本',
    free_market_cap Float64 COMMENT '自由流通市值',
    a_shares Float64 COMMENT 'A股股本',
    a_market_cap Float64 COMMENT 'A股市值',
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/stock_valuation', '{replica}')
PARTITION BY toYYYYMM(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

### 2.8 融资融券

```sql
CREATE TABLE IF NOT EXISTS mtss (
    code LowCardinality(String) COMMENT '股票代码',
    trade_date Date COMMENT '日期',
    fin_value Float64 COMMENT '融资余额',
    fin_buy_value Float64 COMMENT '融资买入额',
    fin_refund_value Float64 COMMENT '融资偿还额',
    sec_value Float64 COMMENT '融券余量',
    sec_sell_value Float64 COMMENT '融券卖出额',
    sec_refund_value Float64 COMMENT '融券偿还额',
    fin_sec_value Float64 COMMENT '融资融券余额',
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/margin_trading', '{replica}')
PARTITION BY toYYYYMM(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

### 2.9 龙虎榜

```sql
CREATE TABLE IF NOT EXISTS billboard_list (
    code LowCardinality(String) COMMENT '股票代码',
    trade_date Date COMMENT '日期',
    direction LowCardinality(String) COMMENT 'BUY/SELL',
    rank UInt8 COMMENT '排名',
    abnormal_code UInt32 COMMENT '异动代码',
    abnormal_name String COMMENT '异动名称',
    sales_depart_name String COMMENT '营业部名称',
    buy_value Float64 COMMENT '买入额',
    buy_rate Float64 COMMENT '买入占比',
    sell_value Float64 COMMENT '卖出额',
    sell_rate Float64 COMMENT '卖出占比',
    total_value Float64 COMMENT '总成交额',
    net_value Float64 COMMENT '净额',
    amount Float64 COMMENT '成交额',
    created_at DateTime DEFAULT now()
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/billboard', '{replica}')
PARTITION BY toYYYYMM(trade_date)
ORDER BY (code, trade_date, direction, rank)
SETTINGS index_granularity = 8192;
```

### 2.10 交易日历

```sql
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date Date COMMENT '交易日',
    is_trading_day UInt8 COMMENT '1=交易日，0=非交易日',
    PRIMARY KEY trade_date
) ENGINE = MergeTree()
ORDER BY trade_date;
```

### 2.11 同步状态检查点

```sql
CREATE TABLE IF NOT EXISTS sync_checkpoint (
    task_id String COMMENT '任务标识',
    worker_id LowCardinality(String) COMMENT '执行节点',
    data_type LowCardinality(String) COMMENT '数据类型',
    target_date Date COMMENT '目标日期',
    status LowCardinality(String) COMMENT 'running/success/failed',
    record_count UInt32 COMMENT '同步记录数',
    started_at DateTime COMMENT '开始时间',
    completed_at Nullable(DateTime) COMMENT '完成时间',
    error_msg String COMMENT '错误信息'
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(target_date)
ORDER BY (data_type, target_date, worker_id);
```

### 2.12 除权除息（STK_XR_XD）

```sql
CREATE TABLE IF NOT EXISTS stk_xr_xd (
    id UInt64,
    company_id UInt64,
    company_name String,
    code LowCardinality(String),
    report_date Date,
    bonus_type LowCardinality(String),
    board_plan_pub_date Date,
    board_plan_bonusnote String,
    shareholders_plan_pub_date Date,
    shareholders_plan_bonusnote String,
    implementation_pub_date Date,
    implementation_bonusnote String,
    dividend_ratio Float64 COMMENT '分红比例',
    transfer_ratio Float64 COMMENT '送转比例',
    bonus_ratio_rmb Float64,
    bonus_ratio_usd Float64,
    bonus_ratio_hkd Float64,
    bonus_amount_rmb Float64 COMMENT '分红总额',
    a_registration_date Date COMMENT 'A股登记日',
    a_xr_date Date COMMENT 'A股除权除息日',
    a_bonus_date Date COMMENT 'A股派息日',
    a_increment_listing_date Date COMMENT 'A股送转股上市日',
    total_capital_before_transfer Float64,
    total_capital_after_transfer Float64,
    plan_progress LowCardinality(String) COMMENT '预案进度',
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (code, report_date, implementation_pub_date)
SETTINGS index_granularity = 8192;
```

**同步脚本:** `src/sync_stk_xr_xd.py`（全量+增量+月兜底三模式）

### 2.13 行业成分（industry_stocks）

```sql
CREATE TABLE IF NOT EXISTS industry_stocks (
    industry_code String COMMENT '申万行业代码',
    industry_name String COMMENT '申万行业名称',
    stock_code String COMMENT '成分股代码',
    level String COMMENT '行业级别: sw_l1/sw_l2/sw_l3',
    date String COMMENT '快照日期',
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (industry_code, stock_code)
SETTINGS index_granularity = 8192;
```

**同步脚本:** `src/sync_extended.py` 的 `sync_industries()`

### 2.14 宏观数据（macro_bond_yield_10y）

```sql
CREATE TABLE IF NOT EXISTS macro_bond_yield_10y (
    stat_date Date COMMENT '统计日期',
    yield Float64 COMMENT '10年期国债收益率（%）',
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY stat_date
SETTINGS index_granularity = 8192;
```

**同步脚本:** `src/sync_extended.py` 的 `sync_macro()`

其他宏观表：`macro_cn_gdp`, `macro_cn_cpi`, `macro_cn_m2`, `macro_cn_pmi`（结构由 `ensure_table` 动态创建）

### 2.15 ETF 日线

```sql
CREATE TABLE IF NOT EXISTS etf_daily (
    code LowCardinality(String),
    trade_date Date,
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume UInt64,
    amount Float64,
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192;
```

**同步脚本:** `src/sync_etf.py`

### 2.16 其他表（动态创建，无固定建表语句）

以下表由同步脚本在首次写入时通过 `ensure_table()` 动态创建：

| 表名 | 说明 | 同步脚本 |
|------|------|---------|
| `locked_shares` | 限售股解禁 | `sync_extended.py` |
| `margin_stocks` | 融资融券标的列表 | `sync_extended.py` |
| `concept_stocks` | 概念板块成分股 | `sync_extended.py` |
| `balance` | 资产负债表（季度） | `sync_fundamentals.py` |
| `income` | 利润表（季度） | `sync_fundamentals.py` |
| `cash_flow` | 现金流量表（季度） | `sync_fundamentals.py` |
| `indicator` | 财务指标（季度） | `sync_fundamentals.py` |
| `macro_cn_gdp` | GDP数据 | `sync_extended.py` |
| `macro_cn_cpi` | CPI数据 | `sync_extended.py` |
| `macro_cn_m2` | M2数据 | `sync_extended.py` |
| `macro_cn_pmi` | PMI数据 | `sync_extended.py` |

---

## 三、待探测/待确认

以下数据因试用账号限制或接口问题，**字段待正式账号确认后补充**：

| 数据类型 | 原因 | 状态 |
|---------|------|------|
| 财务数据 | `get_history_fundamentals` 返回"表不存在" | 待正式账号验证 |
| 资金流 | `get_money_flow` 提示"付费模块" | 待正式账号验证 |
| 基金详情 | `get_fund_info` 返回 dict，结构复杂 | 需进一步探测 |
| 期货信息 | `get_futures_info` 返回空 dict | 需进一步探测 |
| 限售解禁 | 返回空数据，字段类型不准确 | 需有数据时重新探测 |

---

## 四、命名规范

| 规则 | 示例 |
|------|------|
| 表名 | `stock_daily_pre`, `index_component` |
| 字段名 | `trade_date`, `circulating_market_cap` |
| 代码字段 | 统一使用聚宽格式 `000001.XSHE` |
| 金额字段 | 统一用 `amount`，不用 `money` |
| 日期字段 | 日线用 `trade_date`(Date)，分钟线用 `trade_time`(DateTime) |
| 布尔字段 | 用 `UInt8`（0/1），不用 Bool |

---

*最后更新：2026-05-19*  
*版本：v1.1.0 — 新增 stk_xr_xd / industry_stocks / macro_bond_yield_10y 表*

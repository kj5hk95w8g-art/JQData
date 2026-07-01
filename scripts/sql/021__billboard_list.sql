-- 文件: 021__billboard_list.sql
-- 表名: billboard_list
-- 说明: 龙虎榜明细（营业部维度）
-- 引擎: MergeTree
-- 回滚: DROP TABLE IF EXISTS jqdata.billboard_list;

CREATE TABLE IF NOT EXISTS jqdata.billboard_list (
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
    sync_date DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(trade_date)
ORDER BY (code, trade_date, direction, rank)
SETTINGS index_granularity = 8192;

-- 文件: 020__stk_xr_xd.sql
-- 表名: stk_xr_xd
-- 说明: 分红送转（权益分派）
-- 引擎: MergeTree
-- 备注: 支撑持仓收益计算与风控，需及时同步
-- 回滚: DROP TABLE IF EXISTS jqdata.stk_xr_xd;

CREATE TABLE IF NOT EXISTS jqdata.stk_xr_xd
(
    `id` UInt64,
    `company_id` UInt64,
    `company_name` String,
    `code` LowCardinality(String),
    `report_date` Date,
    `bonus_type` LowCardinality(String),
    `board_plan_pub_date` Date,
    `board_plan_bonusnote` String,
    `distributed_share_base_board` Float64,
    `shareholders_plan_pub_date` Date,
    `shareholders_plan_bonusnote` String,
    `distributed_share_base_shareholders` Float64,
    `implementation_pub_date` Date,
    `implementation_bonusnote` String,
    `distributed_share_base_implement` Float64,
    `dividend_ratio` Float64,
    `transfer_ratio` Float64,
    `bonus_ratio_rmb` Float64,
    `bonus_ratio_usd` Float64,
    `bonus_ratio_hkd` Float64,
    `at_bonus_ratio_rmb` Float64,
    `exchange_rate` Float64,
    `dividend_number` Float64,
    `transfer_number` Float64,
    `bonus_amount_rmb` Float64,
    `a_registration_date` Date,
    `b_registration_date` Date,
    `a_xr_date` Date,
    `b_xr_baseday` Date,
    `b_final_trade_date` Date,
    `a_bonus_date` Date,
    `b_bonus_date` Date,
    `dividend_arrival_date` Date,
    `a_increment_listing_date` Date,
    `b_increment_listing_date` Date,
    `total_capital_before_transfer` Float64,
    `total_capital_after_transfer` Float64,
    `float_capital_before_transfer` Float64,
    `float_capital_after_transfer` Float64,
    `note` String,
    `a_transfer_arrival_date` Date,
    `b_transfer_arrival_date` Date,
    `b_dividend_arrival_date` Date,
    `note_of_no_dividend` String,
    `plan_progress_code` UInt32,
    `plan_progress` LowCardinality(String),
    `bonus_cancel_pub_date` Date,
    `sync_date` DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (code, report_date, implementation_pub_date)
SETTINGS index_granularity = 8192

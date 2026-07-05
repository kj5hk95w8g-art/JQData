#!/usr/bin/env python3
"""JQData 财务数据同步 -> ClickHouse

同步范围:
  - 季度数据: balance / income / cash_flow / indicator (2019q1 ~ 当前最新季度)
  - 每日数据: stock_valuation (2020-01-01 ~ 今天)

用法:
    # 全量同步（默认）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals.py

    # 增量同步（只同步 stock_valuation 最近 N 天，跳过季度数据）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals.py --incremental --days 3

    # 增量同步 + 季度财报补充
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals.py --incremental --days 7 --quarterly

环境变量:
  JQ_USER / JQ_PASS / CH_HOST / CH_DB
"""
import os, sys, time, logging, argparse
from datetime import date, timedelta
import pandas as pd
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jqdata-fundamentals")

# ── 配置 ──
JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB   = os.getenv("CH_DB", "jqdata")

TABLE_MAP = {
    "balance":   jq.balance,
    "income":    jq.income,
    "cash_flow": jq.cash_flow,
    "indicator": jq.indicator,
}

CH_TYPE = {
    "int64":   "Float64",
    "float64": "Float64",
    "object":  "String",
}

# valuation -> stock_valuation 字段映射
VALUATION_COL_MAP = {
    "day": "trade_date",
    "capitalization": "total_shares",
    "circulating_cap": "circulating_shares",
    "free_cap": "free_shares",
    "a_cap": "a_shares",
}

def _ch_type(dtype):
    return CH_TYPE.get(str(dtype).lower(), "String")

def _clean_col(name: str) -> str:
    """ClickHouse 字段名清理：替换不合法字符"""
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _to_date(val):
    """把字符串/时间戳转换为 datetime.date，供 ClickHouse Date 列使用"""
    if val is None or val == '':
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    # pandas Timestamp / datetime
    if hasattr(val, 'date'):
        return val.date()
    return val

def _wait_mutations(ch: Client, table: str, timeout: int = 120):
    """等待 ClickHouse 异步 mutation 完成"""
    import time
    start = time.time()
    while time.time() - start < timeout:
        rows = ch.execute("SELECT count() FROM system.mutations WHERE table = %(t)s AND is_done=0", {"t": table})
        if rows[0][0] == 0:
            return
        time.sleep(0.5)
    raise TimeoutError(f"mutations for {table} not done")

def _ensure_stock_valuation_table(ch: Client):
    """创建/保留 stock_valuation 业务表固定 schema"""
    sql = """CREATE TABLE IF NOT EXISTS stock_valuation (
        code LowCardinality(String),
        trade_date Date,
        pe_ratio Float64,
        pb_ratio Float64,
        ps_ratio Float64,
        pcf_ratio Float64,
        turnover_ratio Float64,
        total_shares Float64,
        market_cap Float64,
        circulating_shares Float64,
        circulating_market_cap Float64,
        pe_ratio_lyr Float64,
        pcf_ratio2 Float64,
        dividend_ratio Float64,
        free_shares Float64,
        free_market_cap Float64,
        a_shares Float64,
        a_market_cap Float64,
        sync_date DateTime DEFAULT now()
    ) ENGINE = MergeTree
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (code, trade_date)
    SETTINGS index_granularity = 8192"""
    ch.execute(sql)

def ensure_table(ch: Client, table: str, df: pd.DataFrame):
    """根据 DataFrame 字段动态建表（列名已清理）"""
    if table == "stock_valuation":
        return _ensure_stock_valuation_table(ch)
    cols = []
    order_key = "(code, statDate)"
    for c in df.columns:
        if c == "id":
            continue
        t = _ch_type(df[c].dtype)
        cols.append(f"`{c}` {t}")
    sql = f"""CREATE TABLE IF NOT EXISTS {table} (
        {', '.join(cols)},
        `sync_date` DateTime DEFAULT now()
    ) ENGINE = MergeTree
    ORDER BY {order_key}
    SETTINGS index_granularity = 8192"""
    ch.execute(sql)

def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """通用 DataFrame 清洗"""
    if df is None or df.empty:
        return df
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})
    # ClickHouse driver 0.2.10 字符串列不支持 None
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].fillna('')
    return df

def sync_quarterly(ch: Client, table: str, stat_dates: list):
    """同步季度财报数据"""
    qobj = TABLE_MAP[table]
    total = 0
    for stat_date in stat_dates:
        try:
            df = jq.get_fundamentals(jq.query(qobj), statDate=stat_date)
            if df is None or df.empty:
                logger.info(f"{table} {stat_date}: no data")
                continue
            df = _prepare_df(df)
            ensure_table(ch, table, df)
            cols = [c for c in df.columns]
            records = [tuple(row) for row in df[cols].values]
            ch.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES", records)
            total += len(df)
            logger.info(f"{table} {stat_date}: {len(df)} rows, total={total}")
        except Exception as e:
            logger.error(f"{table} {stat_date} failed: {e}")
        time.sleep(0.3)
    return total

def sync_valuation(ch: Client, dates: list):
    """同步每日估值数据到业务表 stock_valuation"""
    _ensure_stock_valuation_table(ch)
    if dates is not None and len(dates) > 0:
        # 增量/全量写入前，先删除目标日期已有数据，避免重复
        min_d = min(dates)
        max_d = max(dates)
        ch.execute("ALTER TABLE stock_valuation DELETE WHERE trade_date >= %(min)s AND trade_date <= %(max)s", {"min": str(min_d)[:10], "max": str(max_d)[:10]})
        _wait_mutations(ch, "stock_valuation")
    total = 0
    for d in dates:
        try:
            df = jq.get_fundamentals(jq.query(jq.valuation), date=d)
            if df is None or df.empty:
                continue
            df = _prepare_df(df)
            # 字段名映射到业务表
            df = df.rename(columns=VALUATION_COL_MAP)
            # 确保 day 列存在并被映射为 trade_date
            if "trade_date" not in df.columns and "day" in df.columns:
                df = df.rename(columns={"day": "trade_date"})
            # ClickHouse Date 列需要 datetime.date 对象
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].apply(_to_date)
            cols = [c for c in df.columns]
            records = [tuple(row) for row in df[cols].values]
            ch.execute(f"INSERT INTO stock_valuation ({', '.join(cols)}) VALUES", records)
            total += len(df)
            if total % 100000 == 0:
                logger.info(f"stock_valuation: {total} rows so far")
        except Exception as e:
            logger.error(f"stock_valuation {d} failed: {e}")
        time.sleep(0.2)
    logger.info(f"stock_valuation completed: {total} rows")
    return total

def _build_quarters():
    """构建季度列表：2019q1 ~ 当前最新季度"""
    quarters = []
    today = date.today()
    end_year = today.year + 1  # 包含次年（财报有滞后）
    for y in range(2019, end_year + 1):
        for q in ["q1", "q2", "q3"]:
            quarters.append(f"{y}{q}")
        quarters.append(str(y))
    return quarters

def main():
    parser = argparse.ArgumentParser(description="JQData 财务数据同步")
    parser.add_argument("--incremental", action="store_true", help="增量模式（只同步 stock_valuation 最近 N 天）")
    parser.add_argument("--days", type=int, default=3, help="增量天数（默认3天）")
    parser.add_argument("--quarterly", action="store_true", help="同步季度财报数据（可与 --incremental 同时使用）")
    args = parser.parse_args()

    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER 和 JQ_PASS 必须设置")
    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)

    if args.incremental:
        # ── 增量模式：同步 stock_valuation 最近 N 天 ──
        end = date.today()
        start = end - timedelta(days=args.days + 5)  # 多查几天确保覆盖交易日
        trade_days = jq.get_trade_days(start.isoformat(), end.isoformat())
        # 只取最后 days 个交易日
        target_days = trade_days[-args.days:] if len(trade_days) > args.days else trade_days
        logger.info(f"=== 增量模式：stock_valuation 最近 {args.days} 个交易日 ({len(target_days)} 天) ===")
        sync_valuation(ch, target_days)

        if args.quarterly:
            quarters = _build_quarters()
            logger.info(f"=== 季度财报补充同步：{len(quarters)} 个季度 ===")
            for table in ["balance", "income", "cash_flow", "indicator"]:
                logger.info(f"=== 开始同步 {table} ===")
                sync_quarterly(ch, table, quarters)

        logger.info("=== 增量完成 ===")
    else:
        # ── 全量模式 ──
        quarters = _build_quarters()
        logger.info(f"季度列表: 2019q1 ~ {date.today().year + 1}q3, 共 {len(quarters)} 个")

        # ── 同步季度数据 ──
        for table in ["balance", "income", "cash_flow", "indicator"]:
            logger.info(f"=== 开始同步 {table} ===")
            sync_quarterly(ch, table, quarters)

        # ── 同步估值 ──
        trade_days = jq.get_trade_days("2020-01-01", date.today().isoformat())
        logger.info(f"=== 开始同步 stock_valuation，共 {len(trade_days)} 个交易日 ===")
        sync_valuation(ch, trade_days)

        logger.info("=== 全部完成 ===")

if __name__ == "__main__":
    main()

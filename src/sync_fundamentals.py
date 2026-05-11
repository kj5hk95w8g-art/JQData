#!/usr/bin/env python3
"""JQData 财务数据同步 -> ClickHouse

同步范围:
  - 季度数据: balance / income / cash_flow / indicator (2019q1 ~ 2025q3)
  - 每日数据: valuation (2020-01-01 ~ 今天)

环境变量:
  JQ_USER / JQ_PASS / CH_HOST / CH_DB
"""
import os, sys, time, logging
from datetime import date
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
    "valuation": jq.valuation,
}

CH_TYPE = {
    "int64":   "Float64",
    "float64": "Float64",
    "object":  "String",
}

def _ch_type(dtype):
    return CH_TYPE.get(str(dtype).lower(), "String")

def _clean_col(name: str) -> str:
    """ClickHouse 字段名清理：替换不合法字符"""
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def ensure_table(ch: Client, table: str, df: pd.DataFrame):
    """根据 DataFrame 字段动态建表（列名已清理）"""
    cols = []
    order_key = "(code, statDate)"
    for c in df.columns:
        if c == "id":
            continue
        t = _ch_type(df[c].dtype)
        cols.append(f"`{c}` {t}")
    if table == "valuation":
        order_key = "(code, day)"
    sql = f"""CREATE TABLE IF NOT EXISTS {table} (
        {', '.join(cols)},
        `sync_date` DateTime DEFAULT now()
    ) ENGINE = MergeTree
    ORDER BY {order_key}
    SETTINGS index_granularity = 8192"""
    ch.execute(sql)

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
            if "id" in df.columns:
                df = df.drop(columns=["id"])
            df = df.where(pd.notna(df), None)
            # 清理列名
            df = df.rename(columns={c: _clean_col(c) for c in df.columns})
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
    """同步每日估值数据"""
    total = 0
    for d in dates:
        try:
            df = jq.get_fundamentals(jq.query(jq.valuation), date=d)
            if df is None or df.empty:
                continue
            if "id" in df.columns:
                df = df.drop(columns=["id"])
            df = df.where(pd.notna(df), None)
            df = df.rename(columns={c: _clean_col(c) for c in df.columns})
            ensure_table(ch, "valuation", df)
            cols = [c for c in df.columns]
            records = [tuple(row) for row in df[cols].values]
            ch.execute(f"INSERT INTO valuation ({', '.join(cols)}) VALUES", records)
            total += len(df)
            if total % 100000 == 0:
                logger.info(f"valuation: {total} rows so far")
        except Exception as e:
            logger.error(f"valuation {d} failed: {e}")
        time.sleep(0.2)
    logger.info(f"valuation completed: {total} rows")
    return total

def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER 和 JQ_PASS 必须设置")
    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)

    # ── 季度列表: 2019q1 ~ 2025q3 ──
    quarters = []
    for y in range(2019, 2026):
        for q in ["q1", "q2", "q3"]:
            quarters.append(f"{y}{q}")
        quarters.append(str(y))
    logger.info(f"季度列表: {quarters}")

    # ── 同步季度数据 ──
    for table in ["balance", "income", "cash_flow", "indicator"]:
        logger.info(f"=== 开始同步 {table} ===")
        sync_quarterly(ch, table, quarters)

    # ── 同步估值 ──
    trade_days = jq.get_trade_days("2020-01-01", date.today().isoformat())
    logger.info(f"=== 开始同步 valuation，共 {len(trade_days)} 个交易日 ===")
    sync_valuation(ch, trade_days)

    logger.info("=== 全部完成 ===")

if __name__ == "__main__":
    main()

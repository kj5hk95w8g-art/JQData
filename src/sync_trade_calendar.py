#!/usr/bin/env python3
"""JQData 交易日历同步 -> ClickHouse trade_calendar 表

用法:
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_trade_calendar.py
"""
import os, sys, logging
from datetime import date
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trade-calendar")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")


def ensure_table(ch: Client):
    ch.execute("""
        CREATE TABLE IF NOT EXISTS trade_calendar (
            trade_date Date,
            is_trading_day UInt8,
            sync_date DateTime DEFAULT now()
        ) ENGINE = MergeTree
        ORDER BY trade_date
        SETTINGS index_granularity = 8192
    """)


def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER/JQ_PASS required")

    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)
    ensure_table(ch)

    logger.info("=== 同步交易日历 ===")

    # 获取 2005-01-01 ~ 今年底的所有交易日
    end_year = date.today().year + 1
    trade_days = jq.get_trade_days(
        start_date="2005-01-01", end_date=f"{end_year}-12-31"
    )
    trade_set = set(d.strftime("%Y-%m-%d") for d in (trade_days if hasattr(trade_days, 'tolist') else trade_days))

    # 生成所有日历日期
    import pandas as pd
    all_dates = pd.date_range("2005-01-01", f"{end_year}-12-31", freq="D")
    records = []
    for d in all_dates:
        d_str = d.strftime("%Y-%m-%d")
        is_trade = 1 if d_str in trade_set else 0
        records.append((d.date(), is_trade))

    # 幂等覆盖
    ch.execute("TRUNCATE TABLE IF EXISTS trade_calendar")
    ch.execute(
        "INSERT INTO trade_calendar (trade_date, is_trading_day) VALUES",
        records,
    )
    logger.info(f"trade_calendar 同步完成: {len(records)} 天, "
                f"其中交易日 {sum(1 for r in records if r[1] == 1)} 天")


if __name__ == "__main__":
    main()

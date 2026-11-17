#!/usr/bin/env python3
"""JQData 交易日历同步 -> sqlite trade_calendar 表（M6 sqlite 化版，与 sync_trade_calendar.py 逻辑等价）

由独立脚本（直接使用 clickhouse_driver.Client）改写为写 sqlite
（/data/jqdata-platform/data/jqdata.db，可用 JQDATA_DB 覆盖）。原 sync_trade_calendar.py 保留作为回退。

改写点（ClickHouse 特有 → sqlite 等价物，均以注释标注）：
  - clickhouse_driver.Client → sqlite3 连接
  - CREATE TABLE 的 Date/UInt8/DateTime -> TEXT/INTEGER
  - TRUNCATE TABLE IF EXISTS -> DELETE FROM（sqlite 无 TRUNCATE，语义等价）
  - INSERT INTO ... VALUES 用 ? 占位符 executemany（007 规范）

用法:
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_trade_calendar_sqlite.py
"""
import os, logging
from datetime import date
import sqlite3
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trade-calendar")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_calendar (
            trade_date TEXT,
            is_trading_day INTEGER,
            sync_date TEXT DEFAULT (datetime('now'))
        )
    """)


def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER/JQ_PASS required")

    jq.auth(JQ_USER, JQ_PASS)
    conn = _connect()
    ensure_table(conn)

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

    # 幂等覆盖（原 ClickHouse：TRUNCATE TABLE IF EXISTS）
    conn.execute("DELETE FROM trade_calendar")
    conn.commit()
    conn.executemany(
        "INSERT INTO trade_calendar (trade_date, is_trading_day) VALUES (?, ?)",
        records,
    )
    conn.commit()
    logger.info(f"trade_calendar 同步完成: {len(records)} 天, "
                f"其中交易日 {sum(1 for r in records if r[1] == 1)} 天")


if __name__ == "__main__":
    main()

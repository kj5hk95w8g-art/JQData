#!/usr/bin/env python3
"""同步 JQData 交易日历到 ClickHouse trade_calendar 表"""
import os
import logging
import jqdatasdk as jq
from clickhouse_driver import Client
from datetime import date
import calendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trade-calendar")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")


def is_month_end(d: date) -> int:
    return 1 if d.day == calendar.monthrange(d.year, d.month)[1] else 0


def is_quarter_end(d: date) -> int:
    return 1 if d.month in [3, 6, 9, 12] and is_month_end(d) else 0


def is_year_end(d: date) -> int:
    return 1 if d.month == 12 and d.day == 31 else 0


def sync_trade_calendar():
    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host="localhost", database="jqdata")

    # 获取所有交易日
    trade_days = jq.get_all_trade_days()
    logger.info(f"JQData 交易日总数: {len(trade_days)}, 范围: {trade_days[0]} ~ {trade_days[-1]}")

    trade_set = set(trade_days)

    # 生成完整日历（从第一个交易日到最后一个交易日的所有自然日）
    from datetime import timedelta
    start, end = trade_days[0], trade_days[-1]
    delta = (end - start).days

    records = []
    for i in range(delta + 1):
        d = start + timedelta(days=i)
        records.append((
            d,
            1 if d in trade_set else 0,
            d.year,
            (d.month - 1) // 3 + 1,
            d.month,
            d.isoweekday(),
            is_month_end(d),
            is_quarter_end(d),
            is_year_end(d),
        ))

    logger.info(f"生成日历记录: {len(records)} 条")

    # 清空并写入
    ch.execute("TRUNCATE TABLE trade_calendar")
    ch.execute(
        """INSERT INTO trade_calendar
        (trade_date, is_trading_day, year, quarter, month, day_of_week, is_month_end, is_quarter_end, is_year_end)
        VALUES""",
        records
    )

    # 验证
    cnt = ch.execute("SELECT count() FROM trade_calendar")[0][0]
    trading_cnt = ch.execute("SELECT count() FROM trade_calendar WHERE is_trading_day = 1")[0][0]
    logger.info(f"trade_calendar 写入完成: 总记录 {cnt}, 交易日 {trading_cnt}")


if __name__ == "__main__":
    sync_trade_calendar()

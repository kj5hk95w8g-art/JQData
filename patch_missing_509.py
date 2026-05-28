#!/usr/bin/env python3
"""补同步缺失的 509 只股票到 stock_daily_pre / stock_daily_post"""
import os
import sys
import logging
from datetime import date
import pandas as pd
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
TRIAL_START = os.getenv("TRIAL_START", "2020-01-01")
TRIAL_END = date.today().isoformat()
BATCH_CODES = 200
INSERT_BATCH = 10000

if not JQ_USER or not JQ_PASS:
    raise RuntimeError("JQ_USER / JQ_PASS 未设置")

jq.auth(JQ_USER, JQ_PASS)
ch = Client(host=CH_HOST, database=CH_DB)

def get_missing_codes():
    all_stocks = set(r[0] for r in ch.execute("SELECT code FROM security_info WHERE sec_type = 'stock'"))
    daily_codes = set(r[0] for r in ch.execute("SELECT DISTINCT code FROM stock_daily_pre"))
    missing = sorted(all_stocks - daily_codes)
    logger.info(f"security_info 股票总数: {len(all_stocks)}, stock_daily_pre 已有: {len(daily_codes)}, 缺失: {len(missing)}")
    return missing

def sync_codes(codes, fq):
    table = f"stock_daily_{fq}"
    logger.info(f"[{fq}] 开始同步 {len(codes)} 只股票...")
    df = jq.get_price(
        codes,
        start_date=TRIAL_START,
        end_date=TRIAL_END,
        frequency="daily",
        fields=["open", "close", "high", "low", "volume", "money", "factor",
                "high_limit", "low_limit", "avg", "pre_close", "paused"],
        skip_paused=False,
        fq=fq,
        panel=False,
    )
    if df is None or df.empty:
        logger.warning(f"[{fq}] 无数据返回")
        return 0

    df = df.reset_index()
    code_col = next((c for c in df.columns if c in ("code", "security", "level_0")), None)
    date_col = next((c for c in df.columns if c in ("time", "date", "trade_date", "level_1")), None)
    if code_col is None or date_col is None:
        logger.warning(f"[{fq}] 未知列: {df.columns.tolist()}")
        return 0

    df.rename(columns={code_col: "code", date_col: "trade_date"}, inplace=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

    records = []
    for _, row in df.iterrows():
        factor_val = float(row.get("factor", 1) or 1) or 1.0
        records.append((
            row["code"],
            row["trade_date"],
            float(row.get("open", 0) or 0),
            float(row.get("high", 0) or 0),
            float(row.get("low", 0) or 0),
            float(row.get("close", 0) or 0),
            int(row.get("volume", 0) or 0) if pd.notna(row.get("volume", 0)) else 0,
            float(row.get("money", 0) or 0),
            factor_val,
            float(row.get("high_limit", 0) or 0),
            float(row.get("low_limit", 0) or 0),
            float(row.get("avg", 0) or 0),
            float(row.get("pre_close", 0) or 0),
            int(row["paused"]) if pd.notna(row.get("paused")) else 0,
        ))
        if len(records) >= INSERT_BATCH:
            ch.execute(
                f"INSERT INTO {table} (code, trade_date, open, high, low, close, volume, amount, fq_factor, high_limit, low_limit, avg_price, pre_close, paused) VALUES",
                records,
            )
            records = []
    if records:
        ch.execute(
            f"INSERT INTO {table} (code, trade_date, open, high, low, close, volume, amount, fq_factor, high_limit, low_limit, avg_price, pre_close, paused) VALUES",
            records,
        )

    logger.info(f"[{fq}] 完成: {len(df)} 行")
    return len(df)

def main():
    missing = get_missing_codes()
    if not missing:
        logger.info("无缺失股票")
        return

    total_pre = 0
    total_post = 0
    for i in range(0, len(missing), BATCH_CODES):
        batch = missing[i:i + BATCH_CODES]
        logger.info(f"===== 批次 {i//BATCH_CODES + 1}/{(len(missing)-1)//BATCH_CODES + 1}: {len(batch)} 只 =====")
        try:
            total_pre += sync_codes(batch, "pre")
            total_post += sync_codes(batch, "post")
        except Exception as e:
            logger.error(f"批次失败: {e}")

    logger.info(f"===== 补同步完成 =====")
    logger.info(f"pre:  {total_pre:,} 行")
    logger.info(f"post: {total_post:,} 行")

    # 验证
    daily_codes = ch.execute("SELECT countDistinct(code) FROM stock_daily_pre")[0][0]
    logger.info(f"stock_daily_pre 当前股票数: {daily_codes}")

if __name__ == "__main__":
    main()

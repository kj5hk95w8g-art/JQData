#!/usr/bin/env python3
"""JQData -> ClickHouse 日线数据同步（适配真实表结构）"""
import os, sys, time, logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
import pandas as pd
from clickhouse_driver import Client
import redis
import jqdatasdk as jq

JQ_USER = "18918601977"
JQ_PASS = "Another123"
CH_HOST = "localhost"
CH_DB = "jqdata"
REDIS_HOST = "localhost"
REDIS_PORT = 6379
TRIAL_START = "2025-01-28"
TRIAL_END = "2026-02-04"
BATCH_CODES = 200
INSERT_BATCH = 10000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler()])
logger = logging.getLogger("jqdata-sync")


class JQDataSync:
    def __init__(self):
        self.ch = Client(host=CH_HOST, database=CH_DB)
        self.rd = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        self._auth_jq()

    def _auth_jq(self):
        jq.auth(JQ_USER, JQ_PASS)
        logger.info(f"JQData auth OK, quota: {jq.get_query_count()}")

    def sync_security_info(self) -> int:
        logger.info("=== Syncing security_info ===")
        stocks = jq.get_all_securities(types=["stock"], date=None)
        stocks["type"] = "stock"
        etfs = jq.get_all_securities(types=["etf"], date=None)
        etfs["type"] = "etf"
        indexes = jq.get_all_securities(types=["index"], date=None)
        indexes["type"] = "index"

        df = pd.concat([stocks, etfs, indexes], ignore_index=False)
        df.reset_index(inplace=True)
        df.rename(columns={"index": "code"}, inplace=True)
        for col in ["start_date", "end_date"]:
            df[col] = pd.to_datetime(df[col]).dt.date

        # 提取exchange
        df["exchange"] = df["code"].apply(lambda x: x.split(".")[-1] if "." in x else "")

        self.ch.execute("TRUNCATE TABLE IF EXISTS security_info")
        records = []
        for _, row in df.iterrows():
            records.append((
                row["code"],
                row.get("display_name", ""),
                row.get("name", ""),
                row["type"],
                row["exchange"],
                row["start_date"],
                row["end_date"],
            ))
        self.ch.execute(
            "INSERT INTO security_info (code, display_name, name, type, exchange, start_date, end_date) VALUES",
            records
        )
        logger.info(f"security_info synced: {len(records)} records")
        return len(records)

    def sync_stock_daily(self, fq: str = "pre", start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        table = f"stock_daily_{fq}"
        logger.info(f"=== Syncing {table} ===")
        if start_date is None: start_date = TRIAL_START
        if end_date is None: end_date = TRIAL_END

        stocks = jq.get_all_securities(types=["stock"], date=end_date)
        all_codes = stocks.index.tolist()
        logger.info(f"Total stocks: {len(all_codes)}, range: {start_date} ~ {end_date}")

        fields = ["open", "close", "high", "low", "volume", "money",
                  "factor", "high_limit", "low_limit", "avg", "pre_close", "paused"]
        total = 0
        code_batches = [all_codes[i:i+BATCH_CODES] for i in range(0, len(all_codes), BATCH_CODES)]

        for batch_idx, codes in enumerate(code_batches):
            logger.info(f"Batch {batch_idx+1}/{len(code_batches)}: {len(codes)} stocks")
            try:
                df = jq.get_price(codes, start_date=start_date, end_date=end_date,
                                  frequency="daily", fields=fields, skip_paused=False,
                                  fq=fq, panel=False)
                if df is None or df.empty:
                    continue

                df = df.reset_index()
                code_col = next((c for c in df.columns if c in ('code', 'security', 'level_0')), None)
                date_col = next((c for c in df.columns if c in ('time', 'date', 'trade_date', 'level_1')), None)
                if code_col is None or date_col is None:
                    logger.warning(f"Unknown columns: {df.columns.tolist()}")
                    continue

                df.rename(columns={code_col: "code", date_col: "trade_date"}, inplace=True)
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

                records = []
                for _, row in df.iterrows():
                    records.append((
                        row["code"], row["trade_date"],
                        float(row.get("open", 0) or 0),
                        float(row.get("high", 0) or 0),
                        float(row.get("low", 0) or 0),
                        float(row.get("close", 0) or 0),
                        float(row.get("volume", 0) or 0),
                        float(row.get("money", 0) or 0),
                        float(row.get("factor", 1) or 1),
                        float(row.get("high_limit", 0) or 0),
                        float(row.get("low_limit", 0) or 0),
                        float(row.get("avg", 0) or 0),
                        float(row.get("pre_close", 0) or 0),
                        int(row.get("paused", 0) or 0),
                    ))
                    if len(records) >= INSERT_BATCH:
                        self._insert_batch(table, records)
                        total += len(records)
                        records = []
                if records:
                    self._insert_batch(table, records)
                    total += len(records)

                logger.info(f"Batch {batch_idx+1}: inserted {len(df)} rows, total={total}")
            except Exception as e:
                logger.error(f"Batch {batch_idx+1} failed: {e}")

            quota = jq.get_query_count()
            logger.info(f"Quota remaining: {quota['spare']}/{quota['total']}")
            if quota['spare'] < 50000:
                logger.warning("Quota low, stopping")
                break
            time.sleep(0.3)

        logger.info(f"{table} completed: total={total}")
        return total

    def _insert_batch(self, table: str, records: List[Tuple]):
        self.ch.execute(
            f"""INSERT INTO {table} (
                code, trade_date, open, high, low, close, volume, amount,
                fq_factor, high_limit, low_limit, avg_price, pre_close, paused
            ) VALUES""", records)

    def sync_index_daily(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        logger.info("=== Syncing index_daily ===")
        if start_date is None: start_date = TRIAL_START
        if end_date is None: end_date = TRIAL_END

        index_codes = ["000001.XSHG", "000016.XSHG", "000300.XSHG", "000905.XSHG",
                       "399001.XSHE", "399006.XSHE", "399005.XSHE", "000688.XSHG", "000852.XSHG"]
        fields = ["open", "close", "high", "low", "volume", "money"]
        total = 0

        for code in index_codes:
            try:
                df = jq.get_price(code, start_date=start_date, end_date=end_date,
                                  frequency="daily", fields=fields, skip_paused=False,
                                  fq="pre", panel=False)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                date_col = next((c for c in df.columns if c in ('time', 'date')), df.columns[0])
                df.rename(columns={date_col: "trade_date"}, inplace=True)
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
                df["code"] = code

                records = [(code, row["trade_date"],
                            float(row.get("open", 0) or 0), float(row.get("high", 0) or 0),
                            float(row.get("low", 0) or 0), float(row.get("close", 0) or 0),
                            float(row.get("volume", 0) or 0), float(row.get("money", 0) or 0))
                           for _, row in df.iterrows()]
                self.ch.execute(
                    "INSERT INTO index_daily (code, trade_date, open, high, low, close, volume, amount) VALUES",
                    records)
                total += len(records)
                logger.info(f"Index {code}: {len(records)} rows")
            except Exception as e:
                logger.error(f"Index {code} failed: {e}")
            time.sleep(0.2)

        logger.info(f"index_daily completed: total={total}")
        return total

    def save_checkpoint(self, table: str, last_date: str):
        self.rd.hset(f"sync_checkpoint:{table}", mapping={
            "last_date": last_date, "updated_at": datetime.now().isoformat()
        })


def main():
    sync = JQDataSync()
    sync.sync_security_info()
    sync.sync_stock_daily(fq="pre", start_date=TRIAL_START, end_date=TRIAL_END)
    sync.sync_stock_daily(fq="post", start_date=TRIAL_START, end_date=TRIAL_END)
    sync.sync_index_daily(start_date=TRIAL_START, end_date=TRIAL_END)
    sync.save_checkpoint("stock_daily_pre", TRIAL_END)
    sync.save_checkpoint("stock_daily_post", TRIAL_END)
    sync.save_checkpoint("index_daily", TRIAL_END)
    logger.info("=== All sync tasks completed ===")


if __name__ == "__main__":
    main()

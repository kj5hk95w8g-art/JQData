#!/usr/bin/env python3
"""JQData -> ClickHouse 日线数据同步

支持模式：
  --full          全量同步（默认）
  --incremental   增量同步（从 checkpoint 到最新）
  --resume        从 Redis checkpoint 断点续传

环境变量：
  JQ_USER              JQData 账号（必填）
  JQ_PASS              JQData 密码（必填）
  CH_HOST              ClickHouse 地址，默认 localhost
  CH_DB                数据库名，默认 jqdata
  REDIS_HOST           Redis 地址，默认 localhost
  REDIS_PORT           Redis 端口，默认 6379
  DAILY_QUOTA_LIMIT    每日额度上限（条），默认 6_000_000
  TRIAL_START          全量起始日期，默认 2020-01-01
  TRIAL_END            全量结束日期，默认今天
"""
import os, sys, time, logging, argparse
from datetime import datetime, timedelta, date
from typing import List, Tuple, Optional
import pandas as pd
from clickhouse_driver import Client
import redis
import jqdatasdk as jq

# ── 配置 ──
JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
DAILY_QUOTA_LIMIT = int(os.getenv("DAILY_QUOTA_LIMIT", "5500000"))
TRIAL_START = os.getenv("TRIAL_START", "2020-01-01")
TRIAL_END = os.getenv("TRIAL_END", date.today().isoformat())

BATCH_CODES = 200
INSERT_BATCH = 10000
CHECKPOINT_KEY = "jqdata_sync:checkpoint"
QUOTA_USED_KEY = "jqdata_sync:quota_used_today"
QUOTA_DATE_KEY = "jqdata_sync:quota_date"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("jqdata-sync")


def _today_str() -> str:
    return date.today().isoformat()


class JQDataSync:
    def __init__(self):
        if not JQ_USER or not JQ_PASS:
            raise RuntimeError("环境变量 JQ_USER 和 JQ_PASS 必须设置")
        self.ch = Client(host=CH_HOST, database=CH_DB)
        self.rd = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True
        )
        self._auth_jq()
        self._quota_used_today = 0
        self._quota_date = _today_str()
        self._load_quota_state()

    def _auth_jq(self):
        jq.auth(JQ_USER, JQ_PASS)
        quota = jq.get_query_count()
        logger.info(f"JQData auth OK, quota: {quota}")

    def _load_quota_state(self):
        """加载今日已用额度"""
        stored_date = self.rd.get(QUOTA_DATE_KEY)
        if stored_date == self._quota_date:
            used = self.rd.get(QUOTA_USED_KEY)
            self._quota_used_today = int(used) if used else 0
        else:
            # 新的一天，重置计数
            self.rd.set(QUOTA_DATE_KEY, self._quota_date)
            self.rd.set(QUOTA_USED_KEY, 0)
            self._quota_used_today = 0
        logger.info(f"今日已用额度: {self._quota_used_today:,} / 上限: {DAILY_QUOTA_LIMIT:,}")

    def _add_quota(self, count: int):
        """累加今日额度并检查是否超限"""
        self._quota_used_today += count
        self.rd.set(QUOTA_USED_KEY, self._quota_used_today)
        if self._quota_used_today >= DAILY_QUOTA_LIMIT:
            logger.warning(
                f"额度超限: 已用 {self._quota_used_today:,} / 上限 {DAILY_QUOTA_LIMIT:,}，暂停同步"
            )
            return False
        return True

    def _quota_ok(self) -> bool:
        """检查额度是否还有余量（预留 10 万条缓冲）"""
        return self._quota_used_today < DAILY_QUOTA_LIMIT - 100000

    def _get_checkpoint(self, table: str) -> Optional[str]:
        """读取某表的最后同步日期"""
        val = self.rd.hget(CHECKPOINT_KEY, table)
        return val if val else None

    def _set_checkpoint(self, table: str, last_date: str):
        """保存某表的最后同步日期"""
        self.rd.hset(CHECKPOINT_KEY, table, last_date)
        logger.info(f"Checkpoint saved: {table} = {last_date}")

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

        df["exchange"] = df["code"].apply(
            lambda x: x.split(".")[-1] if "." in x else ""
        )

        self.ch.execute("TRUNCATE TABLE IF EXISTS security_info")
        records = []
        for _, row in df.iterrows():
            records.append(
                (
                    row["code"],
                    row.get("display_name", ""),
                    row.get("name", ""),
                    row["type"],
                    row["exchange"],
                    row["start_date"],
                    row["end_date"],
                )
            )
        self.ch.execute(
            "INSERT INTO security_info (code, display_name, name, type, exchange, start_date, end_date) VALUES",
            records,
        )
        logger.info(f"security_info synced: {len(records)} records")
        return len(records)

    def sync_stock_daily(
        self,
        fq: str = "pre",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        table = f"stock_daily_{fq}"
        logger.info(f"=== Syncing {table} ===")
        if start_date is None:
            start_date = TRIAL_START
        if end_date is None:
            end_date = TRIAL_END

        # 检查 JQData 额度
        quota = jq.get_query_count()
        if quota['spare'] <= 0:
            logger.warning(f"JQData 额度已耗尽 ({quota['spare']}/{quota['total']})，跳过同步")
            return 0

        stocks = jq.get_all_securities(types=["stock"], date=end_date)
        all_codes = stocks.index.tolist()
        logger.info(
            f"Total stocks: {len(all_codes)}, range: {start_date} ~ {end_date}"
        )

        fields = [
            "open",
            "close",
            "high",
            "low",
            "volume",
            "money",
            "factor",
            "high_limit",
            "low_limit",
            "avg",
            "pre_close",
            "paused",
        ]
        total = 0
        code_batches = [
            all_codes[i : i + BATCH_CODES]
            for i in range(0, len(all_codes), BATCH_CODES)
        ]

        for batch_idx, codes in enumerate(code_batches):
            if not self._quota_ok():
                logger.warning("额度接近上限，停止同步")
                self._set_checkpoint(table, end_date)
                break

            logger.info(f"Batch {batch_idx+1}/{len(code_batches)}: {len(codes)} stocks")
            try:
                df = jq.get_price(
                    codes,
                    start_date=start_date,
                    end_date=end_date,
                    frequency="daily",
                    fields=fields,
                    skip_paused=False,
                    fq=fq,
                    panel=False,
                )
                if df is None or df.empty:
                    continue

                df = df.reset_index()
                code_col = next(
                    (c for c in df.columns if c in ("code", "security", "level_0")),
                    None,
                )
                date_col = next(
                    (
                        c
                        for c in df.columns
                        if c in ("time", "date", "trade_date", "level_1")
                    ),
                    None,
                )
                if code_col is None or date_col is None:
                    logger.warning(f"Unknown columns: {df.columns.tolist()}")
                    continue

                df.rename(
                    columns={code_col: "code", date_col: "trade_date"}, inplace=True
                )
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

                records = []
                for _, row in df.iterrows():
                    records.append(
                        (
                            row["code"],
                            row["trade_date"],
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
                            int(row["paused"]) if pd.notna(row.get("paused")) else 0,
                        )
                    )
                    if len(records) >= INSERT_BATCH:
                        self._insert_batch(table, records)
                        total += len(records)
                        records = []
                if records:
                    self._insert_batch(table, records)
                    total += len(records)

                self._add_quota(len(df))
                logger.info(f"Batch {batch_idx+1}: inserted {len(df)} rows, total={total}")
            except Exception as e:
                logger.error(f"Batch {batch_idx+1} failed: {e}")

            quota = jq.get_query_count()
            logger.info(f"JQData quota remaining: {quota['spare']:,}/{quota['total']:,}")
            time.sleep(0.3)

        self._set_checkpoint(table, end_date)
        logger.info(f"{table} completed: total={total}")
        return total

    def _get_db_max_date(self, table: str) -> Optional[str]:
        """从 ClickHouse 查询某表的最大日期"""
        try:
            rows = self.ch.execute(f"SELECT max(trade_date) FROM {table}")
            if rows and rows[0][0]:
                val = str(rows[0][0])
                # 过滤 ClickHouse 空表默认值（如 1970-01-01 或 0000-00-00）
                if val >= "2000-01-01":
                    return val
        except Exception as e:
            logger.warning(f"查询 {table} 最大日期失败: {e}")
        return None

    def sync_stock_daily_incremental(self, fq: str = "pre") -> int:
        """增量同步：从 checkpoint 的次日到昨天"""
        table = f"stock_daily_{fq}"
        checkpoint = self._get_checkpoint(table)
        if checkpoint:
            start = (datetime.strptime(checkpoint, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        else:
            # checkpoint 不存在，从数据库最大日期推算
            max_date = self._get_db_max_date(table)
            if max_date:
                start = (datetime.strptime(max_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            else:
                logger.warning(f"{table} 无 checkpoint 且无数据，跳过增量同步（请先执行全量同步）")
                return 0
        end = date.today().isoformat()
        if start > end:
            logger.info(f"{table} 已是最新，无需增量同步")
            return 0
        logger.info(f"{table} 增量同步: {start} ~ {end}")
        return self.sync_stock_daily(fq=fq, start_date=start, end_date=end)

    def sync_index_daily(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        logger.info("=== Syncing index_daily ===")
        if start_date is None:
            start_date = TRIAL_START
        if end_date is None:
            end_date = TRIAL_END

        index_codes = [
            "000001.XSHG", "000016.XSHG", "000300.XSHG", "000905.XSHG",
            "399001.XSHE", "399006.XSHE", "399005.XSHE", "000688.XSHG", "000852.XSHG",
        ]
        fields = ["open", "close", "high", "low", "volume", "money"]
        total = 0

        for code in index_codes:
            try:
                df = jq.get_price(
                    code,
                    start_date=start_date,
                    end_date=end_date,
                    frequency="daily",
                    fields=fields,
                    skip_paused=False,
                    fq="pre",
                    panel=False,
                )
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                date_col = next(
                    (c for c in df.columns if c in ("time", "date")), df.columns[0]
                )
                df.rename(columns={date_col: "trade_date"}, inplace=True)
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
                df["code"] = code

                records = [
                    (
                        code,
                        row["trade_date"],
                        float(row.get("open", 0) or 0),
                        float(row.get("high", 0) or 0),
                        float(row.get("low", 0) or 0),
                        float(row.get("close", 0) or 0),
                        float(row.get("volume", 0) or 0),
                        float(row.get("money", 0) or 0),
                    )
                    for _, row in df.iterrows()
                ]
                self.ch.execute(
                    "INSERT INTO index_daily (code, trade_date, open, high, low, close, volume, amount) VALUES",
                    records,
                )
                total += len(records)
                logger.info(f"Index {code}: {len(records)} rows")
            except Exception as e:
                logger.error(f"Index {code} failed: {e}")
            time.sleep(0.2)

        self._set_checkpoint("index_daily", end_date)
        logger.info(f"index_daily completed: total={total}")
        return total

    def sync_index_daily_incremental(self) -> int:
        """指数增量同步"""
        checkpoint = self._get_checkpoint("index_daily")
        if checkpoint:
            start = (datetime.strptime(checkpoint, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        else:
            max_date = self._get_db_max_date("index_daily")
            if max_date:
                start = (datetime.strptime(max_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            else:
                logger.warning("index_daily 无 checkpoint 且无数据，跳过增量同步")
                return 0
        end = date.today().isoformat()
        if start > end:
            logger.info("index_daily 已是最新，无需增量同步")
            return 0
        logger.info(f"index_daily 增量同步: {start} ~ {end}")
        return self.sync_index_daily(start_date=start, end_date=end)

    def _insert_batch(self, table: str, records: List[Tuple]):
        cols = (
            "code, trade_date, open, high, low, close, volume, amount, "
            "fq_factor, high_limit, low_limit, avg_price, pre_close, paused"
        )
        sql = f"INSERT INTO {table} ({cols}) VALUES"
        self.ch.execute(sql, records)


def main():
    parser = argparse.ArgumentParser(description="JQData -> ClickHouse 同步")
    parser.add_argument("--full", action="store_true", help="全量同步")
    parser.add_argument("--incremental", action="store_true", help="增量同步")
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 断点续传")
    parser.add_argument("--fq", choices=["pre", "post", "both"], default="both", help="复权口径")
    parser.add_argument("--table", choices=["stock", "index", "all"], default="all", help="同步表")
    parser.add_argument("--no-quota-limit", action="store_true", help="不受 DAILY_QUOTA_LIMIT 限制，只受 JQData 真实额度约束（用于晚上定时任务把剩余额度用完）")
    args = parser.parse_args()

    # --no-quota-limit 时把限制设为一个极大值
    if args.no_quota_limit:
        global DAILY_QUOTA_LIMIT
        DAILY_QUOTA_LIMIT = 10_000_000_000
        logger.info("已放开额度限制，只受 JQData 真实额度约束")

    sync = JQDataSync()

    if args.incremental:
        logger.info("=== 增量同步模式 ===")
        if args.table in ("stock", "all"):
            if args.fq in ("pre", "both"):
                sync.sync_stock_daily_incremental(fq="pre")
            if args.fq in ("post", "both"):
                sync.sync_stock_daily_incremental(fq="post")
        if args.table in ("index", "all"):
            sync.sync_index_daily_incremental()
    elif args.resume:
        logger.info("=== 断点续传模式 ===")
        if args.table in ("stock", "all"):
            if args.fq in ("pre", "both"):
                cp = sync._get_checkpoint("stock_daily_pre")
                start = cp if cp else TRIAL_START
                sync.sync_stock_daily(fq="pre", start_date=start, end_date=TRIAL_END)
            if args.fq in ("post", "both"):
                cp = sync._get_checkpoint("stock_daily_post")
                start = cp if cp else TRIAL_START
                sync.sync_stock_daily(fq="post", start_date=start, end_date=TRIAL_END)
        if args.table in ("index", "all"):
            cp = sync._get_checkpoint("index_daily")
            start = cp if cp else TRIAL_START
            sync.sync_index_daily(start_date=start, end_date=TRIAL_END)
    else:
        logger.info("=== 全量同步模式 ===")
        if args.table in ("stock", "all"):
            if args.fq in ("pre", "both"):
                sync.sync_stock_daily(fq="pre")
            if args.fq in ("post", "both"):
                sync.sync_stock_daily(fq="post")
        if args.table in ("index", "all"):
            sync.sync_index_daily()

    logger.info("=== 同步任务结束 ===")


if __name__ == "__main__":
    main()

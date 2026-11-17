#!/usr/bin/env python3
"""JQData ETF 日线同步 -> sqlite etf_daily 表（M6 sqlite 化版，与 sync_etf.py 逻辑等价）

由 ClickHouse+Redis 版改写为写 sqlite。原 sync_etf.py 保留作为回退。

改写点（ClickHouse 特有 → sqlite 等价物，均以注释标注）：
  - CREATE TABLE 的 LowCardinality/MergeTree → TEXT/REAL/INTEGER 列
  - ALTER TABLE ... DELETE + _wait_for_mutations → 事务内精确 DELETE+INSERT
  - %(name)s / 字符串内联 -> ? 占位符（007 规范）

支持模式：
  --full          全量同步（默认）
  --incremental   增量同步（从 checkpoint 到最新）
  --resume        从 checkpoint 断点续传

环境变量：
  JQ_USER / JQ_PASS / JQDATA_DB / DAILY_QUOTA_LIMIT / TRIAL_START / TRIAL_END
"""
import os, time, logging, argparse
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
import pandas as pd
import jqdatasdk as jq

from sync_base_sqlite import SyncBaseSqlite, INSERT_BATCH_SIZE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("jqdata-etf")


class ETFSync(SyncBaseSqlite):
    """ETF 日线同步（sqlite 版）"""

    def __init__(self):
        super().__init__(logger_name="jqdata-etf")

    def _ensure_table(self):
        # sqlite：列类型对齐导出（Date->TEXT、Float->REAL、UInt64->INTEGER）
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS etf_daily (
                code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                amount REAL,
                sync_date TEXT DEFAULT (datetime('now'))
            )
        """)
        logger.info("etf_daily 表就绪")

    def sync_etf_daily(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        table = "etf_daily"
        logger.info(f"=== Syncing {table} ===")
        if start_date is None:
            start_date = self.trial_start
        if end_date is None:
            end_date = self.trial_end

        quota = jq.get_query_count()
        if quota.get("spare", 0) <= 0:
            logger.warning(
                f"JQData 额度已耗尽 ({quota.get('spare', 0)}/{quota.get('total', '?')})，跳过同步"
            )
            return 0

        etfs = jq.get_all_securities(types=["etf"], date=end_date)
        all_codes = etfs.index.tolist()
        logger.info(f"Total ETFs: {len(all_codes)}, range: {start_date} ~ {end_date}")

        fields = ["open", "close", "high", "low", "volume", "money"]
        total = 0
        failed_batches = 0
        BATCH_CODES = 200

        code_batches = [
            all_codes[i : i + BATCH_CODES]
            for i in range(0, len(all_codes), BATCH_CODES)
        ]

        for batch_idx, codes in enumerate(code_batches):
            if not self._quota_ok():
                logger.warning("额度接近上限，停止同步")
                break

            logger.info(
                f"Batch {batch_idx + 1}/{len(code_batches)}: {len(codes)} ETFs"
            )

            # ── 带重试的批次处理 ──
            batch_success = False
            for attempt in range(3):
                try:
                    df = jq.get_price(
                        codes,
                        start_date=start_date,
                        end_date=end_date,
                        frequency="daily",
                        fields=fields,
                        skip_paused=False,
                        fq="pre",
                        panel=False,
                    )
                    batch_success = True
                    break
                except Exception as e:
                    logger.warning(
                        f"Batch {batch_idx + 1} attempt {attempt + 1}/3 failed: {e}"
                    )
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))

            if not batch_success:
                failed_batches += 1
                logger.error(
                    f"Batch {batch_idx + 1} 最终失败，"
                    f"丢弃 {len(codes)} 只 ETF 的本段数据"
                )
                continue

            if df is None or df.empty:
                self._add_quota(len(codes))
                continue

            df = df.reset_index()
            code_col = next(
                (c for c in df.columns if c in ("code", "security", "level_0")), None
            )
            date_col = next(
                (c for c in df.columns
                 if c in ("time", "date", "trade_date", "level_1")), None
            )
            if code_col is None or date_col is None:
                logger.warning(f"Unknown columns: {df.columns.tolist()}")
                continue

            df.rename(columns={code_col: "code", date_col: "trade_date"}, inplace=True)
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
                        int(row.get("volume", 0) or 0)
                        if pd.notna(row.get("volume", 0))
                        else 0,
                        float(row.get("money", 0) or 0),
                    )
                )
                if len(records) >= INSERT_BATCH_SIZE:
                    self._insert_etf_batch(records)
                    total += len(records)
                    records = []
            if records:
                self._insert_etf_batch(records)
                total += len(records)

            self._add_quota(len(df))
            logger.info(
                f"Batch {batch_idx + 1}: inserted {len(df)} rows, total={total}"
            )

            quota = jq.get_query_count()
            logger.info(
                f"JQData quota remaining: {quota.get('spare', 0):,}/{quota.get('total', '?'):,}"
            )
            time.sleep(0.3)

        # ── checkpoint ──
        if failed_batches > 0:
            logger.warning(
                f"本次同步 {failed_batches} 个 batch 失败，checkpoint 不推进"
            )
        else:
            actual_max = self._get_db_max_date(table) or end_date
            self._set_checkpoint(table, actual_max)

        logger.info(
            f"{table} completed: total={total}, failed_batches={failed_batches}"
        )
        return total

    def _insert_etf_batch(self, records: List[Tuple]):
        """ETF 日线批量插入（字段固定），委托统一 _insert_batch
        （原实现为 ALTER TABLE DELETE WHERE code IN (...) AND trade_date IN (...)
         + 等 mutation + INSERT，sqlite 版为事务内精确 DELETE+INSERT）"""
        table = "etf_daily"
        cols = "code, trade_date, open, high, low, close, volume, amount"
        self._insert_batch(table, records, cols, dedup_keys=("code", "trade_date"))

    def sync_etf_daily_incremental(self) -> int:
        table = "etf_daily"
        checkpoint = self._get_checkpoint(table)
        if checkpoint:
            start = (
                datetime.strptime(checkpoint, "%Y-%m-%d").date()
                + timedelta(days=1)
            ).isoformat()
        else:
            max_date = self._get_db_max_date(table)
            if max_date:
                start = (
                    datetime.strptime(max_date, "%Y-%m-%d").date()
                    + timedelta(days=1)
                ).isoformat()
            else:
                logger.warning(
                    f"{table} 无 checkpoint 且无数据，跳过增量同步（请先执行全量同步）"
                )
                return 0

        end = self._last_trade_day()
        if start > end:
            logger.info(f"{table} 已是最新，无需增量同步")
            return 0

        logger.info(f"{table} 增量同步: {start} ~ {end}")
        return self.sync_etf_daily(start_date=start, end_date=end)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="JQData ETF -> sqlite 同步")
    parser.add_argument("--full", action="store_true", help="全量同步")
    parser.add_argument("--incremental", action="store_true", help="增量同步")
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 断点续传")
    parser.add_argument(
        "--no-quota-limit",
        action="store_true",
        help="不受 DAILY_QUOTA_LIMIT 限制",
    )
    args = parser.parse_args()

    if args.no_quota_limit:
        os.environ["DAILY_QUOTA_LIMIT"] = "10000000000"
        logger.info("已放开额度限制，只受 JQData 真实额度约束")

    sync = ETFSync()
    sync._ensure_table()

    if args.incremental:
        logger.info("=== 增量同步模式 ===")
        sync.sync_etf_daily_incremental()
    elif args.resume:
        logger.info("=== 断点续传模式 ===")
        cp = sync._get_checkpoint("etf_daily")
        start = cp if cp else sync.trial_start
        sync.sync_etf_daily(start_date=start)
    else:
        logger.info("=== 全量同步模式 ===")
        sync.sync_etf_daily()

    logger.info("=== ETF 同步任务结束 ===")


if __name__ == "__main__":
    main()

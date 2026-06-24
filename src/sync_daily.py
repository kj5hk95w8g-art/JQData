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
  DAILY_QUOTA_LIMIT    每日额度上限（条），默认 5_500_000
  TRIAL_START          全量起始日期，默认 2020-01-01
  TRIAL_END            全量结束日期，默认今天
"""
import os, sys, time, logging, argparse, random
from datetime import datetime, timedelta, date
from typing import List, Tuple, Optional
import pandas as pd
import jqdatasdk as jq

from sync_base import SyncBase, INSERT_BATCH_SIZE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("jqdata-sync")


class JQDataSync(SyncBase):
    """股票 & 指数日线同步"""

    def __init__(self):
        super().__init__(logger_name="jqdata-sync")

    # ═══════════════════════════════════════════════════════════════
    # security_info
    # ═══════════════════════════════════════════════════════════════

    def sync_security_info(self) -> int:
        logger.info("=== Syncing security_info ===")
        stocks = jq.get_all_securities(types=["stock"], date=None)
        stocks["sec_type"] = "stock"
        etfs = jq.get_all_securities(types=["etf"], date=None)
        etfs["sec_type"] = "etf"
        indexes = jq.get_all_securities(types=["index"], date=None)
        indexes["sec_type"] = "index"

        df = pd.concat([stocks, etfs, indexes], ignore_index=False)
        df.reset_index(inplace=True)
        df.rename(columns={"index": "code"}, inplace=True)
        for col in ["start_date", "end_date"]:
            df[col] = pd.to_datetime(df[col]).dt.date

        df["exchange"] = df["code"].apply(
            lambda x: x.split(".")[-1] if "." in x else ""
        )
        # list_status: 0=已退市, 1=正常上市
        today = date.today()
        df["list_status"] = df["end_date"].apply(
            lambda x: 0 if x and x < today else 1
        )

        self.ch.execute("ALTER TABLE security_info DELETE WHERE 1=1")
        self._wait_for_mutations("security_info")
        records = []
        for _, row in df.iterrows():
            records.append(
                (
                    row["code"],
                    row.get("display_name", ""),
                    row.get("name", ""),
                    row["sec_type"],
                    row["exchange"],
                    row["start_date"],
                    row["end_date"],
                    row["list_status"],
                )
            )
        self.ch.execute(
            "INSERT INTO security_info "
            "(code, display_name, name, sec_type, exchange, start_date, end_date, list_status) VALUES",
            records,
        )
        logger.info(f"security_info synced: {len(records)} records")
        return len(records)

    # ═══════════════════════════════════════════════════════════════
    # 股票日线
    # ═══════════════════════════════════════════════════════════════

    def sync_stock_daily(
        self,
        fq: str = "pre",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        table = f"stock_daily_{fq}"
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

        stocks = jq.get_all_securities(types=["stock"], date=end_date)
        all_codes = stocks.index.tolist()
        logger.info(f"Total stocks: {len(all_codes)}, range: {start_date} ~ {end_date}")

        fields = [
            "open", "close", "high", "low", "volume", "money", "factor",
            "high_limit", "low_limit", "avg", "pre_close", "paused",
        ]
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
                f"Batch {batch_idx + 1}/{len(code_batches)}: {len(codes)} stocks"
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
                        fq=fq,
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
                    f"丢弃 {len(codes)} 只股票的本段数据"
                )
                continue

            # ── 空数据处理 ──
            if df is None or df.empty:
                self._add_quota(len(codes))  # 空查询也计额度
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

            # ── factor 数据校验 ──
            if "factor" not in df.columns:
                logger.error(
                    f"Batch {batch_idx + 1}: jq.get_price 返回数据缺少 factor 列，跳过"
                )
                continue

            factor_values = df["factor"].dropna()
            if len(factor_values) > 0:
                factor_1_ratio = (factor_values == 1.0).sum() / len(factor_values)
                logger.info(
                    f"Batch {batch_idx + 1}: factor分布 "
                    f"min={factor_values.min():.6f}, max={factor_values.max():.6f}, "
                    f"factor=1.0: {(factor_values == 1.0).sum()}/{len(factor_values)} "
                    f"({factor_1_ratio:.2%})"
                )
                if factor_1_ratio == 1.0:
                    sample_codes = df["code"].dropna().unique()
                    if len(sample_codes) > 0:
                        sample_code = random.choice(sample_codes)
                        logger.warning(
                            f"Batch {batch_idx + 1}: 全部 factor=1.0，"
                            f"抽查 {sample_code} 验证..."
                        )
                        try:
                            self._retry(
                                lambda: self._verify_factor(
                                    sample_code, df, fq
                                )
                            )
                        except Exception as e:
                            logger.error(f"抽查验证失败: {e}")

            # ── 构造记录 ──
            records = []
            for _, row in df.iterrows():
                # factor 为 0/None/NaN 时视为 1（未复权），记录警告
                raw_factor = row.get("factor", 1)
                if raw_factor is None or (isinstance(raw_factor, float) and pd.isna(raw_factor)):
                    raw_factor = 1.0
                factor_val = float(raw_factor) if raw_factor else 1.0
                if factor_val == 0.0:
                    logger.warning(f"factor=0 for {row.get('code', '?')} on {row.get('trade_date', '?')}, treating as 1.0")
                    factor_val = 1.0
                records.append(
                    (
                        row["code"],
                        row["trade_date"],
                        float(row.get("open", 0) or 0) / factor_val,
                        float(row.get("high", 0) or 0) / factor_val,
                        float(row.get("low", 0) or 0) / factor_val,
                        float(row.get("close", 0) or 0) / factor_val,
                        int(row.get("volume", 0) or 0)
                        if pd.notna(row.get("volume", 0))
                        else 0,
                        float(row.get("money", 0) or 0),
                        factor_val,
                        float(row.get("high_limit", 0) or 0) / factor_val,
                        float(row.get("low_limit", 0) or 0) / factor_val,
                        float(row.get("avg", 0) or 0) / factor_val,
                        float(row.get("pre_close", 0) or 0) / factor_val,
                        int(row["paused"]) if pd.notna(row.get("paused")) else 0,
                    )
                )
                if len(records) >= INSERT_BATCH_SIZE:
                    self._insert_stock_batch(table, records)
                    total += len(records)
                    records = []
            if records:
                self._insert_stock_batch(table, records)
                total += len(records)

            self._add_quota(len(df))
            logger.info(
                f"Batch {batch_idx + 1}: inserted {len(df)} rows, total={total}"
            )

            quota = jq.get_query_count()
            logger.info(f"JQData quota remaining: {quota.get('spare', 0):,}/{quota.get('total', '?'):,}")
            time.sleep(0.3)

        # ── checkpoint 处理 ──
        if failed_batches > 0:
            logger.warning(
                f"本次同步 {failed_batches} 个 batch 失败，checkpoint 不推进，"
                f"下次同步将重试"
            )
        else:
            actual_max = self._get_db_max_date(table) or end_date
            self._set_checkpoint(table, actual_max)

        logger.info(
            f"{table} completed: total={total}, failed_batches={failed_batches}"
        )
        return total

    def _verify_factor(self, sample_code: str, df: pd.DataFrame, fq: str):
        """抽查验证 factor 数据"""
        check_df = jq.get_price(
            [sample_code],
            start_date=df["trade_date"].min(),
            end_date=df["trade_date"].max(),
            frequency="daily",
            fields=["open", "close", "high", "low", "volume", "money", "factor"],
            skip_paused=False,
            fq=fq,
            panel=False,
        )
        if check_df is not None and "factor" in check_df.columns:
            check_factors = check_df["factor"].dropna()
            if len(check_factors) > 0 and check_factors.min() < 1.0:
                raise ValueError(
                    f"数据异常！JQ云 {sample_code} factor={check_factors.min():.6f}，"
                    f"但本批次全部为 1.0"
                )

    def _insert_stock_batch(self, table: str, records: List[Tuple]):
        """股票日线专用批量插入（字段固定）"""
        cols = (
            "code, trade_date, open, high, low, close, volume, amount, "
            "fq_factor, high_limit, low_limit, avg_price, pre_close, paused"
        )
        # 内存去重
        seen = {}
        for r in records:
            key = (r[0], r[1])  # (code, trade_date)
            seen[key] = r
        deduped = list(seen.values())
        if len(deduped) < len(records):
            logger.info(f"去重: {len(records)} -> {len(deduped)}")

        if not deduped:
            return

        # DELETE 旧数据（股票代码和日期结构安全，单引号转义防注入）
        codes = list(set(self._safe_str(r[0]) for r in deduped))
        dates = list(set(self._safe_str(r[1]) for r in deduped))
        codes_str = ",".join(f"'{c}'" for c in codes)
        dates_str = ",".join(f"'{d}'" for d in dates)
        self.ch.execute(
            f"ALTER TABLE {table} DELETE "
            f"WHERE code IN ({codes_str}) AND trade_date IN ({dates_str})"
        )
        self._wait_for_mutations(table)

        # INSERT
        sql = f"INSERT INTO {table} ({cols}) VALUES"
        self.ch.execute(sql, deduped)

    # ═══════════════════════════════════════════════════════════════
    # 股票增量
    # ═══════════════════════════════════════════════════════════════

    def sync_stock_daily_incremental(self, fq: str = "pre") -> int:
        table = f"stock_daily_{fq}"
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

        # 使用最近交易日而非今天（修复周末/节假日无效请求）
        end = self._last_trade_day()
        if start > end:
            logger.info(f"{table} 已是最新，无需增量同步")
            return 0

        logger.info(f"{table} 增量同步: {start} ~ {end}")
        return self.sync_stock_daily(fq=fq, start_date=start, end_date=end)

    # ═══════════════════════════════════════════════════════════════
    # 指数日线
    # ═══════════════════════════════════════════════════════════════

    def sync_index_daily(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        logger.info("=== Syncing index_daily ===")
        if start_date is None:
            start_date = self.trial_start
        if end_date is None:
            end_date = self.trial_end

        index_codes = [
            "000001.XSHG", "000016.XSHG", "000300.XSHG", "000905.XSHG",
            "399001.XSHE", "399006.XSHE", "399005.XSHE", "000688.XSHG",
            "000852.XSHG", "399303.XSHE", "000510.XSHG", "932000.XSHG",
        ]
        fields = ["open", "close", "high", "low", "volume", "money"]
        total = 0
        failed_count = 0

        for code in index_codes:
            success = False
            for attempt in range(3):
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
                    success = True
                    break
                except Exception as e:
                    logger.warning(
                        f"Index {code} attempt {attempt + 1}/3 failed: {e}"
                    )
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))

            if not success:
                failed_count += 1
                logger.error(f"Index {code} 最终失败，跳过")
                continue

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
                    int(row.get("volume", 0) or 0)
                    if pd.notna(row.get("volume", 0))
                    else 0,
                    float(row.get("money", 0) or 0),
                )
                for _, row in df.iterrows()
            ]
            self.ch.execute(
                "INSERT INTO index_daily "
                "(code, trade_date, open, high, low, close, volume, amount) VALUES",
                records,
            )
            total += len(records)
            logger.info(f"Index {code}: {len(records)} rows")
            time.sleep(0.2)

        if failed_count > 0:
            logger.warning(f"指数同步 {failed_count} 个指数失败，checkpoint 不推进")
        else:
            actual_max = self._get_db_max_date("index_daily") or end_date
            self._set_checkpoint("index_daily", actual_max)

        logger.info(
            f"index_daily completed: total={total}, failed={failed_count}"
        )
        return total

    def sync_index_daily_incremental(self) -> int:
        checkpoint = self._get_checkpoint("index_daily")
        if checkpoint:
            start = (
                datetime.strptime(checkpoint, "%Y-%m-%d").date()
                + timedelta(days=1)
            ).isoformat()
        else:
            max_date = self._get_db_max_date("index_daily")
            if max_date:
                start = (
                    datetime.strptime(max_date, "%Y-%m-%d").date()
                    + timedelta(days=1)
                ).isoformat()
            else:
                logger.warning(
                    "index_daily 无 checkpoint 且无数据，跳过增量同步"
                )
                return 0

        end = self._last_trade_day()
        if start > end:
            logger.info("index_daily 已是最新，无需增量同步")
            return 0

        logger.info(f"index_daily 增量同步: {start} ~ {end}")
        return self.sync_index_daily(start_date=start, end_date=end)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="JQData -> ClickHouse 同步")
    parser.add_argument("--full", action="store_true", help="全量同步")
    parser.add_argument("--incremental", action="store_true", help="增量同步")
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 断点续传")
    parser.add_argument(
        "--fq", choices=["pre", "post", "both"], default="both", help="复权口径"
    )
    parser.add_argument(
        "--table", choices=["stock", "index", "all"], default="all", help="同步表"
    )
    parser.add_argument(
        "--no-quota-limit",
        action="store_true",
        help="不受 DAILY_QUOTA_LIMIT 限制",
    )
    args = parser.parse_args()

    if args.no_quota_limit:
        os.environ["DAILY_QUOTA_LIMIT"] = "10000000000"
        logger.info("已放开额度限制，只受 JQData 真实额度约束")

    sync = JQDataSync()

    if args.incremental:
        logger.info("=== 增量同步模式 ===")
        sync.sync_security_info()  # 每天刷新标的信息（新股/退市）
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
                start = cp if cp else sync.trial_start
                sync.sync_stock_daily(fq="pre", start_date=start)
            if args.fq in ("post", "both"):
                cp = sync._get_checkpoint("stock_daily_post")
                start = cp if cp else sync.trial_start
                sync.sync_stock_daily(fq="post", start_date=start)
        if args.table in ("index", "all"):
            cp = sync._get_checkpoint("index_daily")
            start = cp if cp else sync.trial_start
            sync.sync_index_daily(start_date=start)
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
    try:
        main()
    except Exception as e:
        logger.error(f"同步任务异常退出: {e}", exc_info=True)
        # 尝试发告警（即使构造 sync 对象失败也尽量通知）
        try:
            import requests as _r
            webhook = os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
            if webhook:
                msg = f"JQData 同步异常\n异常: {e}\n服务器: D服务器 (101.132.161.52)"
                payload = (
                    {"msg_type": "text", "content": {"text": msg}}
                    if "feishu" in webhook
                    else {"msgtype": "text", "text": {"content": msg}}
                )
                _r.post(webhook, json=payload, timeout=5)
        except Exception:
            pass
        raise

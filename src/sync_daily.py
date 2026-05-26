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
import os, sys, time, logging, argparse, random
import requests
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

    def _send_feishu(self, title: str, content: str):
        """发送飞书/企业微信/钉钉通知（兼容多种 webhook 格式）"""
        webhook = os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
        if not webhook:
            return
        try:
            msg = f"{title}\n{content}"
            # 根据域名判断平台，使用对应格式
            if "feishu" in webhook or "larksuite" in webhook:
                payload = {"msg_type": "text", "content": {"text": msg}}
            else:
                # 企业微信/钉钉格式
                payload = {"msgtype": "text", "text": {"content": msg}}
            requests.post(webhook, json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"飞书推送失败: {e}")

    def _send_sync_report(self, mode: str = "full"):
        """发送同步完成报告（保留原有表格样式）"""
        webhook = os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
        if not webhook:
            return
        
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 查询额度
        try:
            quota = jq.get_query_count()
            quota_str = f"{quota['spare']:,} / {quota['total']:,}"
        except Exception:
            quota_str = "未知"
        
        # 各表日期字段映射（trade_date / statDate / date / report_date）
        DATE_COL_MAP = {
            "stock_daily_pre": "trade_date",
            "stock_daily_post": "trade_date",
            "index_daily": "trade_date",
            "index_weights": "date",
            "stk_xr_xd": "report_date",
            "margin_stocks": "trade_date",
            "margin_trading": "trade_date",
            "billboard": "trade_date",
            "stock_valuation": "trade_date",
            "balance": "statDate",
            "income": "statDate",
            "cash_flow": "statDate",
            "indicator": "statDate",
        }

        # 构建表格数据
        tables_info = [
            ("股票日线(前复权)", "stock_daily_pre"),
            ("股票日线(后复权)", "stock_daily_post"),
            ("指数日线", "index_daily"),
            ("指数成分权重", "index_weights"),
            ("除权除息", "stk_xr_xd"),
            ("融资融券标的", "margin_stocks"),
            ("融资融券明细", "margin_trading"),
            ("龙虎榜", "billboard"),
            ("每日估值", "stock_valuation"),
            ("资产负债表", "balance"),
            ("利润表", "income"),
            ("现金流量表", "cash_flow"),
            ("财务指标", "indicator"),
        ]
        
        lines = [
            f"JQData 每日同步 {mode}完成",
            f"服务器: D服务器 (101.132.161.52)",
            f"时间: {now}",
            f"额度: {quota_str}",
            "",
            "| 数据表 | 最新日期 | 今日同步 | 总数据量 |",
        ]
        
        for name, table in tables_info:
            try:
                # 先检查表是否存在
                exists = self.ch.execute(f"SELECT count() FROM system.tables WHERE database = '{CH_DB}' AND name = '{table}'")
                if not exists or exists[0][0] == 0:
                    lines.append(f"| {name} | N/A | — | 未创建 |")
                    continue
                
                # 总数据量
                total_rows = self.ch.execute(f"SELECT count() FROM {table}")
                total = total_rows[0][0] if total_rows else 0
                
                # 最新日期
                date_col = DATE_COL_MAP.get(table, "trade_date")
                max_date_rows = self.ch.execute(f"SELECT max({date_col}) FROM {table}")
                max_date = str(max_date_rows[0][0]) if max_date_rows and max_date_rows[0][0] else "N/A"
            except Exception:
                total = 0
                max_date = "N/A"
            
            lines.append(f"| {name} | {max_date} | — | {total:,} |")
        
        msg = "\n".join(lines)
        self._send_feishu(f"JQData 每日同步 {mode}完成", msg)

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

                # === factor 数据校验 ===
                if "factor" not in df.columns:
                    logger.error(f"Batch {batch_idx+1}: jq.get_price 返回数据缺少 factor 列，跳过")
                    continue
                
                factor_values = df["factor"].dropna()
                if len(factor_values) > 0:
                    factor_min = factor_values.min()
                    factor_max = factor_values.max()
                    factor_1_count = (factor_values == 1.0).sum()
                    factor_1_ratio = factor_1_count / len(factor_values)
                    logger.info(
                        f"Batch {batch_idx+1}: factor分布 "
                        f"min={factor_min:.6f}, max={factor_max:.6f}, "
                        f"factor=1.0: {factor_1_count}/{len(factor_values)} ({factor_1_ratio:.2%})"
                    )
                    # 全部 factor=1.0 时抽查验证
                    if factor_1_ratio == 1.0:
                        sample_codes = df["code"].dropna().unique()
                        if len(sample_codes) > 0:
                            sample_code = random.choice(sample_codes)
                            logger.warning(
                                f"Batch {batch_idx+1}: 全部 factor=1.0，抽查 {sample_code} 验证..."
                            )
                            try:
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
                                        logger.error(
                                            f"数据异常！JQ云 {sample_code} factor={check_factors.min():.6f}，"
                                            f"但本批次全部为 1.0。跳过本批次。"
                                        )
                                        continue
                            except Exception as e:
                                logger.error(f"抽查验证失败: {e}")

                records = []
                for _, row in df.iterrows():
                    factor_val = float(row.get("factor", 1) or 1) or 1.0
                    records.append(
                        (
                            row["code"],
                            row["trade_date"],
                            float(row.get("open", 0) or 0) / factor_val,
                            float(row.get("high", 0) or 0) / factor_val,
                            float(row.get("low", 0) or 0) / factor_val,
                            float(row.get("close", 0) or 0) / factor_val,
                            int(row.get("volume", 0) or 0) if pd.notna(row.get("volume", 0)) else 0,
                            float(row.get("money", 0) or 0),
                            factor_val,
                            float(row.get("high_limit", 0) or 0) / factor_val,
                            float(row.get("low_limit", 0) or 0) / factor_val,
                            float(row.get("avg", 0) or 0) / factor_val,
                            float(row.get("pre_close", 0) or 0) / factor_val,
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

        # checkpoint 应该以实际入库的最大日期为准，而非传入的 end_date
        actual_max = self._get_db_max_date(table) or end_date
        self._set_checkpoint(table, actual_max)
        logger.info(f"{table} completed: total={total}, checkpoint={actual_max}")
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

    def _get_codes_with_new_xr_xd(self, fq: str) -> List[str]:
        """检测最近 30 天内是否有新的除权除息事件，返回受影响的 JQ 代码列表

        策略：
        查询 stk_xr_xd 中最近 30 天内已实施的除权除息记录，
        不依赖 checkpoint（checkpoint 推进后检测不到早于 checkpoint 的除权日）。
        """
        # 只检测已实施的除权除息（plan_progress = '实施方案'）
        try:
            rows = self.ch.execute(
                """
                SELECT DISTINCT code FROM stk_xr_xd
                WHERE plan_progress = '实施方案'
                  AND a_xr_date >= today() - 30
                  AND a_xr_date <= today()
                """,
            )
            codes = [r[0] for r in rows if r[0]]
            if codes:
                logger.info(f"检测到 {len(codes)} 只股票在最近 30 天内有除权除息: {codes}")
            return codes
        except Exception as e:
            logger.warning(f"查询 stk_xr_xd 失败: {e}")
            return []

    def _delete_stock_history(self, table: str, code: str) -> str:
        """删除 ClickHouse 中指定股票的所有历史数据，返回 mutation_id

        ClickHouse 的 ALTER TABLE DELETE 是异步 mutation，
        删除后必须等待完成才能插入新数据。
        """
        # 生成一个标识用的 mutation 条件字符串（用于后续轮询）
        try:
            # ClickHouse ALTER TABLE DELETE 不支持参数化，直接拼接（code 来自内部查询，安全）
            self.ch.execute(f"ALTER TABLE {table} DELETE WHERE code = '{code}'")
            logger.info(f"已提交删除 {table} 中 {code} 的历史数据")
            return code  # 用 code 作为标识，轮询时匹配
        except Exception as e:
            logger.error(f"删除 {table} 中 {code} 失败: {e}")
            raise

    def _wait_for_mutation(self, table: str, code: str, timeout: int = 300) -> bool:
        """轮询等待 ClickHouse mutation 完成

        Args:
            table: 表名
            code: 股票代码（用于匹配 mutation 条件）
            timeout: 最大等待秒数

        Returns:
            True: mutation 已完成
            False: 超时
        """
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                rows = self.ch.execute(
                    f"""
                    SELECT mutation_id, is_done
                    FROM system.mutations
                    WHERE database = '{CH_DB}' AND table = '{table}'
                      AND command LIKE '%DELETE WHERE code = \\'{code}\\'%'
                    ORDER BY create_time DESC
                    LIMIT 1
                    """
                )
                if rows and rows[0][1]:  # is_done = 1
                    logger.info(f"Mutation 完成: {table} {code}")
                    return True
            except Exception as e:
                logger.warning(f"查询 mutation 状态失败: {e}")
            time.sleep(1)
        logger.warning(f"Mutation 等待超时: {table} {code} (>{timeout}s)")
        return False

    def _resync_affected_codes(self, fq: str, codes: List[str]) -> int:
        """对受除权除息影响的股票：删除历史数据 → 等待 mutation → 全量重新同步

        Returns:
            同步的总行数
        """
        table = f"stock_daily_{fq}"
        total = 0
        for code in codes:
            logger.info(f"开始重跑 {code} 的历史数据（{fq}）...")
            try:
                self._delete_stock_history(table, code)
                if not self._wait_for_mutation(table, code):
                    logger.error(f"删除 {code} 历史数据超时，跳过重跑")
                    continue

                # 全量重新拉取该股票的历史数据
                # 从 2020-01-01 到今天，确保覆盖所有历史
                df = jq.get_price(
                    [code],
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
                    logger.warning(f"{code} 无数据")
                    continue

                df = df.reset_index()
                code_col = next(
                    (c for c in df.columns if c in ("code", "security", "level_0")),
                    None,
                )
                date_col = next(
                    (c for c in df.columns if c in ("time", "date", "trade_date", "level_1")),
                    None,
                )
                if code_col is None or date_col is None:
                    logger.warning(f"{code} 未知列: {df.columns.tolist()}")
                    continue

                df.rename(columns={code_col: "code", date_col: "trade_date"}, inplace=True)
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

                records = []
                for _, row in df.iterrows():
                    factor_val = float(row.get("factor", 1) or 1) or 1.0
                    records.append(
                        (
                            row["code"],
                            row["trade_date"],
                            float(row.get("open", 0) or 0) / factor_val,
                            float(row.get("high", 0) or 0) / factor_val,
                            float(row.get("low", 0) or 0) / factor_val,
                            float(row.get("close", 0) or 0) / factor_val,
                            int(row.get("volume", 0) or 0) if pd.notna(row.get("volume", 0)) else 0,
                            float(row.get("money", 0) or 0),
                            factor_val,
                            float(row.get("high_limit", 0) or 0) / factor_val,
                            float(row.get("low_limit", 0) or 0) / factor_val,
                            float(row.get("avg", 0) or 0) / factor_val,
                            float(row.get("pre_close", 0) or 0) / factor_val,
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
                logger.info(f"{code} 重跑完成: {len(df)} 行")
            except Exception as e:
                logger.error(f"重跑 {code} 失败: {e}")
        return total

    def sync_stock_daily_incremental(self, fq: str = "pre") -> int:
        """增量同步：从 checkpoint 的次日到昨天

        前置检测：
        1. 查询 stk_xr_xd，找出最近 30 天内已实施的除权除息股票
        2. 对这些股票：删除历史数据 → 等待 mutation → 全量重新同步
        3. 然后再执行正常的增量同步
        """
        table = f"stock_daily_{fq}"

        # === 前置检测：除权除息导致的重跑 ===
        affected_codes = self._get_codes_with_new_xr_xd(fq)
        if affected_codes:
            logger.info(f"检测到 {len(affected_codes)} 只股票需要重跑历史数据（除权除息）")
            resynced = self._resync_affected_codes(fq, affected_codes)
            logger.info(f"重跑完成: {resynced} 行")

        # === 正常的增量同步 ===
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
                        int(row.get("volume", 0) or 0) if pd.notna(row.get("volume", 0)) else 0,
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

        actual_max = self._get_db_max_date("index_daily") or end_date
        self._set_checkpoint("index_daily", actual_max)
        logger.info(f"index_daily completed: total={total}, checkpoint={actual_max}")
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
        # 按 (code, trade_date) 去重，保留最后一条
        seen = {}
        for r in records:
            key = (r[0], r[1])  # code, trade_date
            seen[key] = r
        deduped = list(seen.values())
        if len(deduped) < len(records):
            logger.warning(f"去重: {len(records)} -> {len(deduped)}")
        
        cols = (
            "code, trade_date, open, high, low, close, volume, amount, "
            "fq_factor, high_limit, low_limit, avg_price, pre_close, paused"
        )
        sql = f"INSERT INTO {table} ({cols}) VALUES"
        self.ch.execute(sql, deduped)


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

    sync._send_sync_report(mode="full")
    logger.info("=== 同步任务结束 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"同步任务异常退出: {e}")
        sync = JQDataSync()
        sync._send_feishu(
            "JQData 同步异常",
            f"异常信息: {e}\n"
            f"服务器: D服务器 (101.132.161.52)",
        )
        raise

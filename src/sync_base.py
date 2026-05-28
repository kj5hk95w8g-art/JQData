#!/usr/bin/env python3
"""JQData 同步公共基类

所有同步脚本（sync_daily / sync_etf 等）共享的：
- 环境变量读取
- ClickHouse / Redis 连接管理
- JQData 认证
- 额度管理（Redis 共享计数器）
- Checkpoint 管理
- 批量插入（带去重 + mutation 等待）
- 重试机制
- 交易日判定
"""

import os
import time
import logging
from datetime import date, timedelta
from typing import List, Tuple, Optional
import pandas as pd
from clickhouse_driver import Client
import redis
import jqdatasdk as jq

logger = logging.getLogger("jqdata-sync-base")

# ── Redis Key 常量 ──
CHECKPOINT_KEY = "jqdata_sync:checkpoint"
QUOTA_USED_KEY = "jqdata_sync:quota_used_today"
QUOTA_DATE_KEY = "jqdata_sync:quota_date"

# ── 默认配置 ──
DEFAULT_QUOTA_LIMIT = 5_500_000
DEFAULT_TRIAL_START = "2020-01-01"
INSERT_BATCH_SIZE = 10000


class SyncBase:
    """同步脚本公共基类"""

    def __init__(self, logger_name: str = "jqdata-sync"):
        self.logger = logging.getLogger(logger_name)

        # ── 环境变量 ──
        self.jq_user = os.getenv("JQ_USER")
        self.jq_pass = os.getenv("JQ_PASS")
        self.ch_host = os.getenv("CH_HOST", "localhost")
        self.ch_db = os.getenv("CH_DB", "jqdata")
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.quota_limit = int(os.getenv("DAILY_QUOTA_LIMIT", str(DEFAULT_QUOTA_LIMIT)))
        self.trial_start = os.getenv("TRIAL_START", DEFAULT_TRIAL_START)
        self.trial_end = os.getenv("TRIAL_END", date.today().isoformat())

        if not self.jq_user or not self.jq_pass:
            raise RuntimeError("环境变量 JQ_USER 和 JQ_PASS 必须设置")

        # ── 连接 ──
        self.ch = Client(host=self.ch_host, database=self.ch_db)
        self.rd = redis.Redis(
            host=self.redis_host, port=self.redis_port, db=0, decode_responses=True
        )

        # ── 认证 ──
        self._auth_jq()

        # ── 额度 ──
        self._quota_used_today = 0
        self._quota_date = date.today().isoformat()
        self._load_quota_state()

    # ═══════════════════════════════════════════════════════════════
    # JQData 认证
    # ═══════════════════════════════════════════════════════════════

    def _auth_jq(self):
        jq.auth(self.jq_user, self.jq_pass)
        quota = jq.get_query_count()
        self.logger.info(f"JQData auth OK, quota: {quota}")

    # ═══════════════════════════════════════════════════════════════
    # 额度管理
    # ═══════════════════════════════════════════════════════════════

    def _load_quota_state(self):
        stored_date = self.rd.get(QUOTA_DATE_KEY)
        if stored_date == self._quota_date:
            used = self.rd.get(QUOTA_USED_KEY)
            self._quota_used_today = int(used) if used else 0
        else:
            self.rd.set(QUOTA_DATE_KEY, self._quota_date)
            self.rd.set(QUOTA_USED_KEY, 0)
            self._quota_used_today = 0
        self.logger.info(
            f"今日已用额度: {self._quota_used_today:,} / 上限: {self.quota_limit:,}"
        )

    def _add_quota(self, count: int) -> bool:
        self._quota_used_today += count
        self.rd.set(QUOTA_USED_KEY, self._quota_used_today)
        if self._quota_used_today >= self.quota_limit:
            self.logger.warning(
                f"额度超限: 已用 {self._quota_used_today:,} / 上限 {self.quota_limit:,}"
            )
            return False
        return True

    def _quota_ok(self) -> bool:
        return self._quota_used_today < self.quota_limit - 100_000

    # ═══════════════════════════════════════════════════════════════
    # Checkpoint 管理
    # ═══════════════════════════════════════════════════════════════

    def _get_checkpoint(self, table: str) -> Optional[str]:
        val = self.rd.hget(CHECKPOINT_KEY, table)
        return val if val else None

    def _set_checkpoint(self, table: str, last_date: str):
        self.rd.hset(CHECKPOINT_KEY, table, last_date)
        self.logger.info(f"Checkpoint saved: {table} = {last_date}")

    # ═══════════════════════════════════════════════════════════════
    # ClickHouse 工具
    # ═══════════════════════════════════════════════════════════════

    def _get_db_max_date(self, table: str, date_col: str = "trade_date") -> Optional[str]:
        try:
            rows = self.ch.execute(f"SELECT max({date_col}) FROM {table}")
            if rows and rows[0][0]:
                val = str(rows[0][0])
                if val >= "2000-01-01":
                    return val
        except Exception as e:
            self.logger.warning(f"查询 {table} 最大日期失败: {e}")
        return None

    def _wait_for_mutations(self, table: str, timeout: int = 60):
        """等待 ClickHouse mutation 完成（ALTER TABLE DELETE 等异步操作）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.ch.execute(
                "SELECT count() FROM system.mutations "
                "WHERE table = %(t)s AND database = %(d)s AND is_done = 0",
                {"t": table, "d": self.ch_db},
            )
            if result and result[0][0] == 0:
                return
            time.sleep(0.5)
        self.logger.warning(
            f"Mutations on {table} still pending after {timeout}s, continuing anyway"
        )

    # ═══════════════════════════════════════════════════════════════
    # 重试
    # ═══════════════════════════════════════════════════════════════

    def _retry(self, func, *args, max_retries: int = 3, base_delay: float = 1.0, **kwargs):
        """带指数退避的重试"""
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                delay = base_delay * (2 ** attempt)
                self.logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {delay:.0f}s: {e}"
                )
                time.sleep(delay)
        return None  # unreachable

    # ═══════════════════════════════════════════════════════════════
    # 交易日
    # ═══════════════════════════════════════════════════════════════

    def _last_trade_day(self) -> str:
        """获取最近一个交易日（周末/节假日返回上周五）"""
        try:
            days = jq.get_trade_days(
                start_date=(date.today() - timedelta(days=10)).isoformat(),
                end_date=date.today().isoformat(),
            )
            if len(days) > 0:
                return days[-1].strftime("%Y-%m-%d")
        except Exception as e:
            self.logger.warning(f"获取交易日失败: {e}")
        return date.today().isoformat()

    # ═══════════════════════════════════════════════════════════════
    # 批量插入（统一去重逻辑）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _safe_str(v) -> str:
        """安全转字符串，SQL 字面量防注入（股票代码/日期专用）"""
        return str(v).replace("'", "''")

    def _insert_batch(
        self,
        table: str,
        records: List[Tuple],
        cols: str,
        dedup_keys: Tuple[str, ...] = ("code", "trade_date"),
    ):
        """
        统一的批量插入方法：
        1. 按 dedup_keys 内存去重
        2. DELETE 旧数据
        3. 等待 mutation 完成
        4. INSERT 新数据
        """
        if not records:
            return

        # ── 1. 内存去重：按 dedup_keys 保留最后一条 ──
        seen = {}
        key_indices = []
        col_list = [c.strip() for c in cols.split(",")]
        for dk in dedup_keys:
            if dk in col_list:
                key_indices.append(col_list.index(dk))

        if key_indices:
            for r in records:
                key = tuple(r[i] for i in key_indices)
                seen[key] = r
            deduped = list(seen.values())
            if len(deduped) < len(records):
                self.logger.info(f"去重: {len(records)} -> {len(deduped)}")
        else:
            deduped = records

        # ── 2. DELETE 旧数据 ──
        if dedup_keys and key_indices:
            # 收集所有要覆盖的 key 值
            key_values = set()
            for r in deduped:
                key_values.add(tuple(r[i] for i in key_indices))

            # 对每个 dedup_key 构建 IN 条件
            for dk_idx, dk_name in zip(key_indices, dedup_keys):
                vals = sorted(set(self._safe_str(r[dk_idx]) for r in deduped))
                vals_str = ",".join(f"'{v}'" for v in vals)
                self.ch.execute(
                    f"ALTER TABLE {table} DELETE WHERE {dk_name} IN ({vals_str})"
                )
            self._wait_for_mutations(table)

        # ── 3. INSERT ──
        sql = f"INSERT INTO {table} ({cols}) VALUES"
        self.ch.execute(sql, deduped)
        self.logger.debug(f"Inserted {len(deduped)} rows into {table}")

#!/usr/bin/env python3
"""JQData 同步公共基类（sqlite 版，M6 去 ClickHouse/docker 化）

与 sync_base.SyncBase 保持相同公共接口，数据层由 ClickHouse+Redis 收敛为 sqlite：
  - 数据库文件：/data/jqdata-platform/data/jqdata.db（可用环境变量 JQDATA_DB 覆盖）
  - 额度计数 / checkpoint 存 sqlite 表 sync_meta（key TEXT PRIMARY KEY, value TEXT）
  - 批量插入：内存去重 + DELETE WHERE key IN (?,...) + INSERT（同一事务）
  - ClickHouse 的 ALTER TABLE ... DELETE + 等 mutation + INSERT 改为 sqlite 事务化 DELETE+INSERT

共享逻辑原样保留：JQData 认证、额度管理、checkpoint、重试、交易日判定、安全转字符串。

SQL 一律 ? 占位符（007 规范，禁止 f-string 拼接值）；表名/列名来自代码常量白名单可拼接。
"""

import os
import time
import logging
import sqlite3
from datetime import date, timedelta
from typing import List, Tuple, Optional
import jqdatasdk as jq

from sql_ident import ident, ident_list

logger = logging.getLogger("jqdata-sync-base-sqlite")

# ── sync_meta 表 key 常量 ──
CHECKPOINT_KEY_PREFIX = "checkpoint:"
QUOTA_USED_KEY = "quota_used_today"
QUOTA_DATE_KEY = "quota_date"

# ── 默认配置（与 ClickHouse 版一致） ──
DEFAULT_QUOTA_LIMIT = 5_500_000
DEFAULT_TRIAL_START = "2020-01-01"
INSERT_BATCH_SIZE = 10000

# ── sqlite 库文件 ──
DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")


class SyncBaseSqlite:
    """同步脚本公共基类（sqlite 后端）"""

    def __init__(self, logger_name: str = "jqdata-sync", db_path: Optional[str] = None):
        self.logger = logging.getLogger(logger_name)

        # ── 环境变量（与 ClickHouse 版一致，仅去掉 CH_*/REDIS_*） ──
        self.jq_user = os.getenv("JQ_USER")
        self.jq_pass = os.getenv("JQ_PASS")
        self.quota_limit = int(os.getenv("DAILY_QUOTA_LIMIT", str(DEFAULT_QUOTA_LIMIT)))
        self.trial_start = os.getenv("TRIAL_START", DEFAULT_TRIAL_START)
        self.trial_end = os.getenv("TRIAL_END", date.today().isoformat())

        if not self.jq_user or not self.jq_pass:
            raise RuntimeError("环境变量 JQ_USER 和 JQ_PASS 必须设置")

        # ── sqlite 连接 ──
        self.db_path = db_path or DB_PATH
        self.conn = sqlite3.connect(self.db_path, timeout=60)
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_sync_meta()

        # ── 认证 ──
        self._auth_jq()

        # ── 额度 ──
        self._quota_used_today = 0
        self._quota_date = date.today().isoformat()
        self._load_quota_state()

    def close(self):
        """显式关闭连接（长期运行的守护进程用）"""
        try:
            self.conn.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # sync_meta 表
    # ═══════════════════════════════════════════════════════════════

    def _ensure_sync_meta(self):
        """首次使用创建 sync_meta 表（额度计数 + checkpoint 水位）"""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.commit()

    def _meta_get(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ═══════════════════════════════════════════════════════════════
    # JQData 认证（原样保留）
    # ═══════════════════════════════════════════════════════════════

    def _auth_jq(self):
        jq.auth(self.jq_user, self.jq_pass)
        quota = jq.get_query_count()
        self.logger.info(f"JQData auth OK, quota: {quota}")

    # ═══════════════════════════════════════════════════════════════
    # 额度管理（存 sync_meta）
    # ═══════════════════════════════════════════════════════════════

    def _load_quota_state(self):
        stored_date = self._meta_get(QUOTA_DATE_KEY)
        if stored_date == self._quota_date:
            used = self._meta_get(QUOTA_USED_KEY)
            self._quota_used_today = int(used) if used else 0
        else:
            self._meta_set(QUOTA_DATE_KEY, self._quota_date)
            self._meta_set(QUOTA_USED_KEY, "0")
            self._quota_used_today = 0
        self.logger.info(
            f"今日已用额度: {self._quota_used_today:,} / 上限: {self.quota_limit:,}"
        )

    def _add_quota(self, count: int) -> bool:
        self._quota_used_today += count
        self._meta_set(QUOTA_USED_KEY, str(self._quota_used_today))
        if self._quota_used_today >= self.quota_limit:
            self.logger.warning(
                f"额度超限: 已用 {self._quota_used_today:,} / 上限 {self.quota_limit:,}"
            )
            return False
        return True

    def _quota_ok(self) -> bool:
        return self._quota_used_today < self.quota_limit - 100_000

    # ═══════════════════════════════════════════════════════════════
    # Checkpoint 管理（存 sync_meta，key 带表名前缀）
    # ═══════════════════════════════════════════════════════════════

    def _get_checkpoint(self, table: str) -> Optional[str]:
        return self._meta_get(f"{CHECKPOINT_KEY_PREFIX}{table}")

    def _set_checkpoint(self, table: str, last_date: str):
        self._meta_set(f"{CHECKPOINT_KEY_PREFIX}{table}", last_date)
        self.logger.info(f"Checkpoint saved: {table} = {last_date}")

    # ═══════════════════════════════════════════════════════════════
    # sqlite 工具
    # ═══════════════════════════════════════════════════════════════

    def _get_db_max_date(self, table: str, date_col: str = "trade_date") -> Optional[str]:
        """查表最大日期。sqlite 中日期列为 TEXT(YYYY-MM-DD)，max() 字典序即时间序。"""
        try:
            row = self.conn.execute(
                "SELECT max({date_col}) FROM {table}".format(
                    date_col=ident(date_col), table=ident(table)
                )
            ).fetchone()
            if row and row[0]:
                val = str(row[0])[:10]
                if val >= "2000-01-01":
                    return val
        except Exception as e:
            self.logger.warning(f"查询 {table} 最大日期失败: {e}")
        return None

    # ═══════════════════════════════════════════════════════════════
    # 重试（原样保留）
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
    # 交易日（原样保留）
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
        """安全转字符串（sqlite 用 ? 占位符后无需转义，保留此接口以对齐 ClickHouse 版）"""
        return str(v)

    def _insert_batch(
        self,
        table: str,
        records: List[Tuple],
        cols: str,
        dedup_keys: Tuple[str, ...] = ("code", "trade_date"),
    ):
        """
        统一的批量插入方法（sqlite）：
        1. 按 dedup_keys 内存去重
        2. 按 dedup key 精确 DELETE 已存在行（? 占位符，多 key 用行值 IN 精确到组合）
        3. INSERT 新数据
        DELETE + INSERT 在同一事务内，任一失败回滚，保证幂等（重复插入同 key 不产生重复行）。
        （ClickHouse 版为 ALTER TABLE DELETE + 等 mutation + INSERT，sqlite 无异步 mutation，同步删除）
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

        if not deduped:
            return

        placeholders = ", ".join("?" * len(col_list))
        insert_sql = "INSERT INTO {table} ({cols}) VALUES ({placeholders})".format(
            table=ident(table), cols=ident_list(cols), placeholders=placeholders
        )

        try:
            # ── 2. DELETE 已存在的 dedup key 行（仅精确删除本次覆盖的组合，不影响其他行） ──
            if dedup_keys and key_indices:
                key_cols = [col_list[i] for i in key_indices]
                key_rows = [tuple(r[i] for i in key_indices) for r in deduped]
                if len(key_indices) == 1:
                    # 单 key：DELETE WHERE col IN (?,...)
                    key_col = key_cols[0]
                    vals = sorted(set(kr[0] for kr in key_rows))
                    ph = ", ".join("?" * len(vals))
                    self.conn.execute(
                        "DELETE FROM {table} WHERE {key_col} IN ({ph})".format(
                            table=ident(table), key_col=ident(key_col), ph=ph
                        ),
                        list(vals),
                    )
                else:
                    # 多 key：DELETE WHERE (k1, k2) IN (VALUES (?,?), ...) —— 精确到组合
                    row_ph = ", ".join("?" * len(key_indices))
                    rows_ph = ", ".join(f"({row_ph})" for _ in key_rows)
                    self.conn.execute(
                        "DELETE FROM {table} WHERE ({key_cols}) "
                        "IN (VALUES {rows_ph})".format(
                            table=ident(table),
                            key_cols=", ".join(ident(c) for c in key_cols),
                            rows_ph=rows_ph,
                        ),
                        [v for kr in key_rows for v in kr],
                    )
            # ── 3. INSERT ──
            self.conn.executemany(insert_sql, deduped)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.logger.debug(f"Inserted {len(deduped)} rows into {table}")

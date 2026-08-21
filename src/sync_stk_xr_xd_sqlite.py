#!/usr/bin/env python3
"""分红送转数据同步（STK_XR_XD）-> sqlite（M6 sqlite 化版，与 sync_stk_xr_xd.py 逻辑等价）

由独立脚本（直接使用 clickhouse_driver.Client）改写为写 sqlite
（/data/jqdata-platform/data/jqdata.db，可用 JQDATA_DB 覆盖）。原 sync_stk_xr_xd.py 保留作为回退。

改写点（ClickHouse 特有 → sqlite 等价物，均以注释标注）：
  - clickhouse_driver.Client → sqlite3 连接
  - DESCRIBE TABLE 动态列类型 -> 由 DataFrame dtype 推断 sqlite 列类型
  - INSERT INTO ... VALUES 用 ? 占位符 executemany（007 规范）
  - TRUNCATE TABLE IF EXISTS -> DELETE FROM（sqlite 无 TRUNCATE，语义等价）
  - ALTER TABLE ... DELETE WHERE id IN %(ids)s -> DELETE WHERE id IN (?,...)
  - 表存在性检查 SELECT ... LIMIT 0 等价保留

同步策略：
  1. 首次：全量分页拉取（每页 5000 行）
  2. 日常：每日增量（查询最近 2 个季度的记录，与本地对比后插入新增）
  3. 兜底：每月 1 号全量覆盖（DELETE + INSERT）
"""
import os, sys, time, logging
from datetime import date, timedelta, datetime
import sqlite3
import pandas as pd
import jqdatasdk as jq

from sql_ident import ident, ident_list

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync-stk-xr-xd")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")

SQLITE_TYPE = {"int64": "INTEGER", "int32": "INTEGER", "uint64": "INTEGER",
               "float64": "REAL", "object": "TEXT", "bool": "INTEGER"}

def _sqlite_type(dtype):
    return SQLITE_TYPE.get(str(dtype).lower(), "TEXT")

def _clean_col(name):
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def _convert_value(v, col_type="TEXT"):
    """清洗数值，处理 None / NaN / 日期 -> sqlite 值"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        if col_type in ("INTEGER", "REAL"):
            return 0 if col_type == "INTEGER" else 0.0
        return ""
    if isinstance(v, bool):
        return int(v)
    if hasattr(v, "item"):
        v = v.item()
    if col_type == "INTEGER":
        return int(v) if v is not None else 0
    if col_type == "REAL":
        return float(v) if v is not None else 0.0
    # TEXT：日期对象统一转 'YYYY-MM-DD'
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    return v if v is not None else ""

def insert_df(conn, table, df):
    """将 DataFrame 写入 sqlite，自动对齐列（按 df 列写入，列类型由 dtype 推断）"""
    if df is None or df.empty:
        return 0
    # 保留 JQData 返回的 id 作为去键，禁止丢弃
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})

    cols = [c for c in df.columns]
    col_types = {c: _sqlite_type(df[c].dtype) for c in cols}

    # Date/Datetime 列识别 -> TEXT
    for c in cols:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            col_types[c] = "TEXT"
        else:
            sample = df[c].dropna().head(1).tolist()
            if sample and isinstance(sample[0], date):
                col_types[c] = "TEXT"

    # 填充空值（日期/文本列由 _convert_value 统一转 'YYYY-MM-DD' / 空串）
    for c in cols:
        if col_types[c] in ("REAL", "INTEGER"):
            df[c] = df[c].fillna(0)
        elif col_types[c] == "TEXT":
            df[c] = df[c].fillna('')

    # 统一转 sqlite 值并生成记录
    records = []
    for row in df[cols].values:
        records.append(tuple(_convert_value(v, col_types[c]) for v, c in zip(row, cols)))

    ph = ", ".join("?" * len(cols))
    conn.executemany(
        "INSERT INTO {table} ({cols}) VALUES ({ph})".format(
            table=ident(table), cols=ident_list(cols), ph=ph
        ),
        records,
    )
    conn.commit()
    return len(df)

def _get_recent_quarters(n=2):
    """获取最近 N 个季度的报告期日期（如 2025-12-31, 2025-09-30）"""
    quarters = []
    d = date.today()
    for _ in range(n):
        y, m = d.year, d.month
        if m <= 3:
            qd = date(y - 1, 12, 31)
        elif m <= 6:
            qd = date(y, 3, 31)
        elif m <= 9:
            qd = date(y, 6, 30)
        else:
            qd = date(y, 9, 30)
        quarters.append(qd)
        d = qd - timedelta(days=1)
    # 去重并保持顺序
    seen = set()
    result = []
    for q in quarters:
        if q not in seen:
            seen.add(q)
            result.append(q)
    return result

def sync_stk_xr_xd_full(conn, jq_auth=True, truncate=True):
    """全量同步 STK_XR_XD，分页拉取所有历史数据

    Args:
        conn: sqlite3 连接
        jq_auth: 是否需要重新 auth（外部已 auth 时设为 False）
        truncate: 是否先清空表（每月兜底用 True，首次用 False 因为表是空的）
    """
    if jq_auth:
        jq.auth(JQ_USER, JQ_PASS)

    batch_size = 5000
    offset = 0
    total = 0
    all_dfs = []

    logger.info("=== STK_XR_XD 全量同步开始 ===")

    while True:
        try:
            q = jq.query(jq.finance.STK_XR_XD).offset(offset).limit(batch_size)
            df = jq.finance.run_query(q)
        except Exception as e:
            logger.error(f"查询失败 offset={offset}: {e}")
            break

        if df is None or df.empty:
            break

        all_dfs.append(df)
        n = len(df)
        total += n
        offset += n
        logger.info(f"STK_XR_XD: offset={offset}, batch={n}, total={total}")

        if n < batch_size:
            break
        time.sleep(0.3)

    if not all_dfs:
        logger.info("STK_XR_XD: 无数据")
        return 0

    full_df = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"STK_XR_XD: 合并后共 {len(full_df)} 行")

    if truncate:
        # 原 ClickHouse：TRUNCATE TABLE IF EXISTS；sqlite 用 DELETE 全清（语义等价）
        logger.info("STK_XR_XD: DELETE 全量覆盖")
        conn.execute("DELETE FROM stk_xr_xd")
        conn.commit()

    inserted = insert_df(conn, "stk_xr_xd", full_df)
    logger.info(f"=== STK_XR_XD 全量同步完成: {inserted} 行 ===")
    return inserted

def sync_stk_xr_xd_incremental(conn, jq_auth=True):
    """每日增量同步 STK_XR_XD

    策略：查询最近 2 个季度的所有记录，与本地对比 id，只插入新增记录。
    额度消耗：每季度约几千条，每天 < 1 万额度。
    """
    if jq_auth:
        jq.auth(JQ_USER, JQ_PASS)

    quarters = _get_recent_quarters(2)
    logger.info(f"STK_XR_XD 增量: 查询季度 {quarters}")

    all_dfs = []
    for qd in quarters:
        try:
            q = jq.query(jq.finance.STK_XR_XD).filter(
                jq.finance.STK_XR_XD.report_date == qd.isoformat()
            )
            df = jq.finance.run_query(q)
            if df is not None and not df.empty:
                all_dfs.append(df)
                logger.info(f"STK_XR_XD 增量: report_date={qd} -> {len(df)} 行")
        except Exception as e:
            logger.error(f"STK_XR_XD 增量查询失败 report_date={qd}: {e}")
        time.sleep(0.2)

    if not all_dfs:
        logger.info("STK_XR_XD 增量: 无数据")
        return 0

    combined = pd.concat(all_dfs, ignore_index=True)
    # 去重（同一记录可能跨季度出现多次）
    combined = combined.drop_duplicates(subset=["id"])
    logger.info(f"STK_XR_XD 增量: 去重后共 {len(combined)} 行")

    # 获取本地已有 id
    try:
        local_ids = set(r[0] for r in conn.execute("SELECT id FROM stk_xr_xd").fetchall())
    except Exception:
        local_ids = set()

    to_insert = combined[~combined["id"].isin(local_ids)]

    if to_insert.empty:
        logger.info("STK_XR_XD 增量: 无新增记录")
        return 0

    # 幂等写入：对同一 id 先删除旧记录，再插入新记录，避免重复。
    ids = to_insert["id"].tolist()
    logger.info(f"STK_XR_XD 增量: 覆盖 {len(ids)} 条记录")
    # 原 ClickHouse：ALTER TABLE ... DELETE WHERE id IN %(ids)s；sqlite 用 ? 占位符
    ph = ", ".join("?" * len(ids))
    conn.execute("DELETE FROM stk_xr_xd WHERE id IN ({ph})".format(ph=ph), ids)
    conn.commit()

    inserted = insert_df(conn, "stk_xr_xd", to_insert)
    logger.info(f"STK_XR_XD 增量: 新增 {inserted} 行")
    return inserted

def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER and JQ_PASS required")

    jq.auth(JQ_USER, JQ_PASS)
    conn = _connect()

    # 检查表是否存在
    try:
        conn.execute("SELECT 1 FROM stk_xr_xd LIMIT 1")
    except Exception:
        logger.error("stk_xr_xd 表不存在，请先执行 SQL migration: scripts/sql/020__stk_xr_xd.sql")
        sys.exit(1)

    # 根据参数决定全量或增量
    mode = os.getenv("SYNC_MODE", "incremental")
    if mode == "full":
        sync_stk_xr_xd_full(conn, jq_auth=False, truncate=True)
    else:
        sync_stk_xr_xd_incremental(conn, jq_auth=False)

if __name__ == "__main__":
    main()

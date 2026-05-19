#!/usr/bin/env python3
"""分红送转数据同步（STK_XR_XD）

同步策略：
  1. 首次：全量分页拉取（每页 5000 行）
  2. 日常：每日增量（查询最近 2 个季度的记录，与本地对比后插入新增）
  3. 兜底：每月 1 号全量覆盖（TRUNCATE + INSERT）

额度控制：
  - 全量：约 5-15 万行，额度 5-15 万（日额度 2 亿，占比 < 0.08%）
  - 增量：每天查最近 2 个季度，约几千到一万行，额度 < 1 万/天
  - 分页间隔 0.3 秒限速

风控要求：
  - 新公告（新 id）次日进库
  - 状态变化（预案→实施）每月兜底修正
"""
import os, sys, time, logging
from datetime import date, timedelta
import pandas as pd
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync-stk-xr-xd")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")

CH_TYPE = {"int64": "Float64", "float64": "Float64", "object": "String", "uint64": "UInt64"}

def _ch_type(dtype):
    return CH_TYPE.get(str(dtype).lower(), "String")

def _clean_col(name):
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _convert_value(v, col_type="String"):
    """清洗数值，处理 None / NaN / 日期"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        if col_type in ("UInt64", "Int64", "UInt32"):
            return 0
        if col_type in ("Float64",):
            return 0.0
        if col_type == "Date":
            return date(1970, 1, 1)
        return ""
    if isinstance(v, bool):
        return int(v)
    if hasattr(v, "item"):
        v = v.item()
    if col_type in ("UInt64", "Int64", "UInt32"):
        return int(v) if v is not None else 0
    if col_type == "Float64":
        return float(v) if v is not None else 0.0
    if col_type == "Date":
        if isinstance(v, date):
            return v
        if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
            return date.fromisoformat(v)
        return date(1970, 1, 1)
    return v

def _safe_date(val):
    """将各种日期格式转为 date 对象，失败返回 1970-01-01"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return date(1970, 1, 1)
    if isinstance(val, date):
        return val
    if isinstance(val, str) and len(val) >= 10 and val[4] == "-" and val[7] == "-":
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return date(1970, 1, 1)
    return date(1970, 1, 1)

def insert_df(ch, table, df):
    """将 DataFrame 写入 ClickHouse，自动对齐列"""
    if df is None or df.empty:
        return 0
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})

    try:
        ch_schema = {c[0]: c[1] for c in ch.execute(f"DESCRIBE {table}")}
        df = df[[c for c in df.columns if c in ch_schema]]
    except Exception:
        ch_schema = {}

    cols = [c for c in df.columns]
    col_types = {c: ch_schema.get(c, _ch_type(df[c].dtype)) for c in cols}

    # Date/Datetime 列识别
    for c in cols:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            col_types[c] = "Date"
        else:
            sample = df[c].dropna().head(1).tolist()
            if sample and isinstance(sample[0], date):
                col_types[c] = "Date"

    # 填充空值
    for c in cols:
        if col_types[c] in ("Float64", "UInt64", "Int64", "UInt32"):
            df[c] = df[c].fillna(0)
        elif col_types[c] == "Date":
            df[c] = df[c].apply(_safe_date)
        else:
            df[c] = df[c].fillna("")

    records = []
    for row in df[cols].values:
        records.append(tuple(_convert_value(v, col_types[c]) for v, c in zip(row, cols)))

    ch.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES", records)
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

def sync_stk_xr_xd_full(ch, jq_auth=True, truncate=True):
    """全量同步 STK_XR_XD，分页拉取所有历史数据

    Args:
        ch: ClickHouse Client
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
        logger.info("STK_XR_XD: TRUNCATE + INSERT 全量覆盖")
        ch.execute("TRUNCATE TABLE IF EXISTS stk_xr_xd")

    inserted = insert_df(ch, "stk_xr_xd", full_df)
    logger.info(f"=== STK_XR_XD 全量同步完成: {inserted} 行 ===")
    return inserted

def sync_stk_xr_xd_incremental(ch, jq_auth=True):
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
        local_ids = set(r[0] for r in ch.execute("SELECT id FROM stk_xr_xd"))
    except Exception:
        local_ids = set()

    to_insert = combined[~combined["id"].isin(local_ids)]

    if to_insert.empty:
        logger.info("STK_XR_XD 增量: 无新增记录")
        return 0

    inserted = insert_df(ch, "stk_xr_xd", to_insert)
    logger.info(f"STK_XR_XD 增量: 新增 {inserted} 行")
    return inserted

def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER and JQ_PASS required")

    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)

    # 检查表是否存在
    try:
        ch.execute("SELECT 1 FROM stk_xr_xd LIMIT 0")
    except Exception:
        logger.error("stk_xr_xd 表不存在，请先执行 SQL migration: scripts/sql/020__stk_xr_xd.sql")
        sys.exit(1)

    # 根据参数决定全量或增量
    mode = os.getenv("SYNC_MODE", "incremental")
    if mode == "full":
        sync_stk_xr_xd_full(ch, jq_auth=False, truncate=True)
    else:
        sync_stk_xr_xd_incremental(ch, jq_auth=False)

if __name__ == "__main__":
    main()

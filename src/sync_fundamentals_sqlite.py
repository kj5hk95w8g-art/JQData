#!/usr/bin/env python3
"""JQData 财务数据同步 -> sqlite（M6 sqlite 化版，与 sync_fundamentals.py 逻辑等价）

由独立脚本（直接使用 clickhouse_driver.Client）改写为写 sqlite
（/data/jqdata-platform/data/jqdata.db，可用 JQDATA_DB 覆盖）。原 sync_fundamentals.py 保留作为回退。

改写点（ClickHouse 特有 → sqlite 等价物，均以注释标注）：
  - clickhouse_driver.Client → sqlite3 连接
  - CREATE TABLE 的 Date/Float64/LowCardinality/DateTime -> TEXT/REAL/INTEGER
  - INSERT INTO ... VALUES 用 ? 占位符 executemany（007 规范）
  - ALTER TABLE ... DELETE + _wait_mutations（system.mutations 轮询，删除）-> 同步 DELETE + 事务提交

同步范围:
  - 季度数据: balance / income / cash_flow / indicator (2019q1 ~ 当前最新季度)
  - 每日数据: stock_valuation (2020-01-01 ~ 今天)

用法:
    # 全量同步（默认）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals_sqlite.py

    # 增量同步（只同步 stock_valuation 最近 N 天，跳过季度数据）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals_sqlite.py --incremental --days 3

    # 增量同步 + 季度财报补充
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_fundamentals_sqlite.py --incremental --days 7 --quarterly
"""
import os, time, logging, argparse
from datetime import date, timedelta, datetime
import sqlite3
import pandas as pd
import jqdatasdk as jq

from sql_ident import ident, ident_list

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jqdata-fundamentals")

# ── 配置 ──
JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")

TABLE_MAP = {
    "balance":   jq.balance,
    "income":    jq.income,
    "cash_flow": jq.cash_flow,
    "indicator": jq.indicator,
}

SQLITE_TYPE = {
    "int64":   "INTEGER",
    "float64": "REAL",
    "object":  "TEXT",
}

# valuation -> stock_valuation 字段映射
VALUATION_COL_MAP = {
    "day": "trade_date",
    "capitalization": "total_shares",
    "circulating_cap": "circulating_shares",
    "free_cap": "free_shares",
    "a_cap": "a_shares",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _sqlite_type(dtype):
    return SQLITE_TYPE.get(str(dtype).lower(), "TEXT")

def _clean_col(name: str) -> str:
    """字段名清理：替换不合法字符"""
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _to_date(val):
    """把字符串/时间戳转换为 datetime.date（sqlite 存为 'YYYY-MM-DD' TEXT）

    注意：pd.Timestamp 是 datetime.date 的子类，必须先于 isinstance(date) 判断，
    否则会原样返回 Timestamp（python sqlite3 无法绑定该类型）。
    """
    if val is None or val == '':
        return None
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    if isinstance(val, (pd.Timestamp, datetime)) or hasattr(val, 'date'):
        # pandas Timestamp / datetime / 其他时间类型 -> 仅日期
        return val.date()
    if isinstance(val, date):
        return val
    return val

def _ensure_stock_valuation_table(conn: sqlite3.Connection):
    """创建/保留 stock_valuation 业务表固定 schema（sqlite 列类型对齐导出）"""
    sql = """CREATE TABLE IF NOT EXISTS stock_valuation (
        code TEXT,
        trade_date TEXT,
        pe_ratio REAL,
        pb_ratio REAL,
        ps_ratio REAL,
        pcf_ratio REAL,
        turnover_ratio REAL,
        total_shares REAL,
        market_cap REAL,
        circulating_shares REAL,
        circulating_market_cap REAL,
        pe_ratio_lyr REAL,
        pcf_ratio2 REAL,
        dividend_ratio REAL,
        free_shares REAL,
        free_market_cap REAL,
        a_shares REAL,
        a_market_cap REAL,
        sync_date TEXT DEFAULT (datetime('now'))
    )"""
    conn.execute(sql)
    conn.commit()

def ensure_table(conn: sqlite3.Connection, table: str, df: pd.DataFrame):
    """根据 DataFrame 字段动态建表（列名已清理）"""
    if table == "stock_valuation":
        return _ensure_stock_valuation_table(conn)
    cols = []
    for c in df.columns:
        if c == "id":
            continue
        t = _sqlite_type(df[c].dtype)
        cols.append('"{c}" {t}'.format(c=ident(c), t=t))
    sql = """CREATE TABLE IF NOT EXISTS {table} (
        {cols},
        `sync_date` TEXT DEFAULT (datetime('now'))
    )""".format(table=ident(table), cols=", ".join(cols))
    conn.execute(sql)
    conn.commit()

def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """通用 DataFrame 清洗"""
    if df is None or df.empty:
        return df
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})
    # 时间列转 'YYYY-MM-DD' 文本（sqlite 无法绑定 pandas Timestamp）；字符串列空值统一为空串
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime('%Y-%m-%d')
        elif df[c].dtype == object:
            df[c] = df[c].fillna('')
    return df

def sync_quarterly(conn: sqlite3.Connection, table: str, stat_dates: list):
    """同步季度财报数据"""
    qobj = TABLE_MAP[table]
    total = 0
    for stat_date in stat_dates:
        try:
            df = jq.get_fundamentals(jq.query(qobj), statDate=stat_date)
            if df is None or df.empty:
                logger.info(f"{table} {stat_date}: no data")
                continue
            df = _prepare_df(df)
            ensure_table(conn, table, df)
            cols = [c for c in df.columns]
            records = [tuple(row) for row in df[cols].values]
            ph = ", ".join("?" * len(cols))
            conn.executemany(
                "INSERT INTO {table} ({cols}) VALUES ({ph})".format(
                    table=ident(table), cols=ident_list(cols), ph=ph
                ),
                records,
            )
            conn.commit()
            total += len(df)
            logger.info(f"{table} {stat_date}: {len(df)} rows, total={total}")
        except Exception as e:
            logger.error(f"{table} {stat_date} failed: {e}")
        time.sleep(0.3)
    return total

def sync_valuation(conn: sqlite3.Connection, dates: list):
    """同步每日估值数据到业务表 stock_valuation"""
    _ensure_stock_valuation_table(conn)
    if dates is not None and len(dates) > 0:
        # 增量/全量写入前，先删除目标日期已有数据，避免重复
        # （原 ClickHouse ALTER TABLE ... DELETE WHERE trade_date BETWEEN + 等 mutation）
        min_d = min(dates)
        max_d = max(dates)
        conn.execute(
            "DELETE FROM stock_valuation WHERE trade_date >= ? AND trade_date <= ?",
            (str(min_d)[:10], str(max_d)[:10]),
        )
        conn.commit()
    total = 0
    for d in dates:
        try:
            df = jq.get_fundamentals(jq.query(jq.valuation), date=d)
            if df is None or df.empty:
                continue
            df = _prepare_df(df)
            # 字段名映射到业务表
            df = df.rename(columns=VALUATION_COL_MAP)
            # 确保 day 列存在并被映射为 trade_date
            if "trade_date" not in df.columns and "day" in df.columns:
                df = df.rename(columns={"day": "trade_date"})
            # trade_date 存 sqlite TEXT(YYYY-MM-DD)
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].apply(_to_date)
            cols = [c for c in df.columns]
            records = [tuple(row) for row in df[cols].values]
            ph = ", ".join("?" * len(cols))
            conn.executemany(
                "INSERT INTO stock_valuation ({cols}) VALUES ({ph})".format(
                    cols=ident_list(cols), ph=ph
                ),
                records,
            )
            conn.commit()
            total += len(df)
            if total % 100000 == 0:
                logger.info(f"stock_valuation: {total} rows so far")
        except Exception as e:
            logger.error(f"stock_valuation {d} failed: {e}")
        time.sleep(0.2)
    logger.info(f"stock_valuation completed: {total} rows")
    return total

def _build_quarters():
    """构建季度列表：2019q1 ~ 当前最新季度"""
    quarters = []
    today = date.today()
    end_year = today.year + 1  # 包含次年（财报有滞后）
    for y in range(2019, end_year + 1):
        for q in ["q1", "q2", "q3"]:
            quarters.append(f"{y}{q}")
        quarters.append(str(y))
    return quarters

def main():
    parser = argparse.ArgumentParser(description="JQData 财务数据同步")
    parser.add_argument("--incremental", action="store_true", help="增量模式（只同步 stock_valuation 最近 N 天）")
    parser.add_argument("--days", type=int, default=3, help="增量天数（默认3天）")
    parser.add_argument("--quarterly", action="store_true", help="同步季度财报数据（可与 --incremental 同时使用）")
    args = parser.parse_args()

    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER 和 JQ_PASS 必须设置")
    jq.auth(JQ_USER, JQ_PASS)
    conn = _connect()

    if args.incremental:
        # ── 增量模式：同步 stock_valuation 最近 N 天 ──
        end = date.today()
        start = end - timedelta(days=args.days + 5)  # 多查几天确保覆盖交易日
        trade_days = jq.get_trade_days(start.isoformat(), end.isoformat())
        # 只取最后 days 个交易日
        target_days = trade_days[-args.days:] if len(trade_days) > args.days else trade_days
        logger.info(f"=== 增量模式：stock_valuation 最近 {args.days} 个交易日 ({len(target_days)} 天) ===")
        sync_valuation(conn, target_days)

        if args.quarterly:
            quarters = _build_quarters()
            logger.info(f"=== 季度财报补充同步：{len(quarters)} 个季度 ===")
            for table in ["balance", "income", "cash_flow", "indicator"]:
                logger.info(f"=== 开始同步 {table} ===")
                sync_quarterly(conn, table, quarters)

        logger.info("=== 增量完成 ===")
    elif args.quarterly:
        # ── 仅同步季度财报 ──
        quarters = _build_quarters()
        logger.info(f"=== 季度财报同步：{len(quarters)} 个季度 ===")
        for table in ["balance", "income", "cash_flow", "indicator"]:
            logger.info(f"=== 开始同步 {table} ===")
            sync_quarterly(conn, table, quarters)
        logger.info("=== 季度财报完成 ===")
    else:
        # ── 全量模式 ──
        quarters = _build_quarters()
        logger.info(f"季度列表: 2019q1 ~ {date.today().year + 1}q3, 共 {len(quarters)} 个")

        # ── 同步季度数据 ──
        for table in ["balance", "income", "cash_flow", "indicator"]:
            logger.info(f"=== 开始同步 {table} ===")
            sync_quarterly(conn, table, quarters)

        # ── 同步估值 ──
        trade_days = jq.get_trade_days("2020-01-01", date.today().isoformat())
        logger.info(f"=== 开始同步 stock_valuation，共 {len(trade_days)} 个交易日 ===")
        sync_valuation(conn, trade_days)

        logger.info("=== 全部完成 ===")

if __name__ == "__main__":
    main()

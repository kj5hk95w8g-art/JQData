#!/usr/bin/env python3
"""JQData 特色数据 + 行业概念 + 宏观数据同步 -> sqlite（M6 sqlite 化版，与 sync_extended.py 逻辑等价）

由独立脚本（直接使用 clickhouse_driver.Client）改写为写 sqlite
（/data/jqdata-platform/data/jqdata.db，可用 JQDATA_DB 覆盖）。原 sync_extended.py 保留作为回退。

改写点（ClickHouse 特有 → sqlite 等价物，均以注释标注）：
  - clickhouse_driver.Client → sqlite3 连接（WAL/busy_timeout/synchronous）
  - DESCRIBE TABLE 动态类型 -> 由 DataFrame dtype 推断 sqlite 列类型
  - INSERT INTO ... VALUES 用 ? 占位符 executemany（007 规范）
  - ALTER TABLE ... DELETE + _wait_mutations（system.mutations 轮询，删除）-> 同步 DELETE + 事务提交
  - sync_date 由 DateTime 改 TEXT(DATETIME)，_days_since 按文本前缀解析日期

用法:
    # 全量同步（默认）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_extended_sqlite.py

    # 增量同步（最近 N 天，高频数据）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_extended_sqlite.py --incremental --days 3
"""
import os, time, logging, argparse
from datetime import date, timedelta, datetime
import sqlite3
import pandas as pd
import jqdatasdk as jq

from sql_ident import ident, ident_list

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jqdata-extended")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")
TRIAL_START = os.getenv("TRIAL_START", "2020-01-01")
TRIAL_END   = date.today().isoformat()


def _connect() -> sqlite3.Connection:
    """sqlite 连接（写库：WAL / busy_timeout / synchronous=NORMAL）"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


SQLITE_TYPE = {"int64": "INTEGER", "float64": "REAL", "object": "TEXT"}
def _sqlite_type(dtype): return SQLITE_TYPE.get(str(dtype).lower(), "TEXT")

def _clean_col(name: str) -> str:
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _cell_to_text(v):
    """单值转 sqlite 友好文本：date/Timestamp -> 'YYYY-MM-DD'，其余原样"""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v

def ensure_table(conn: sqlite3.Connection, table: str, df: pd.DataFrame, order_by: str):
    """按 DataFrame 字段动态建表（列名已清理）。order_by 为 ClickHouse 排序键，sqlite 无需，忽略。"""
    cols = []
    for c in df.columns:
        if c == "id":
            continue
        cols.append('"{c}" {t}'.format(c=ident(_clean_col(c)), t=_sqlite_type(df[c].dtype)))
    sql = """CREATE TABLE IF NOT EXISTS {table} (
        {cols}, `sync_date` TEXT DEFAULT (datetime('now'))
    )""".format(table=ident(table), cols=", ".join(cols))
    conn.execute(sql)
    conn.commit()

def insert_df(conn: sqlite3.Connection, table: str, df: pd.DataFrame):
    if df is None or df.empty:
        return 0
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})
    if "day" in df.columns:
        df = df.rename(columns={"day": "trade_date"})
    # 各列转为 sqlite 友好值：日期列 -> 'YYYY-MM-DD' 文本，数值列保留，字符串列 None -> ''
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif df[c].dtype == object:
            df[c] = df[c].apply(_cell_to_text)
            df[c] = df[c].fillna('')
        else:
            df[c] = df[c].where(pd.notna(df[c]), None)
    if "code" in df.columns and "day" in df.columns:
        order_by = "(code, day)"
    elif "code" in df.columns and "date" in df.columns:
        order_by = "(code, date)"
    elif "code" in df.columns and "trade_date" in df.columns:
        order_by = "(code, trade_date)"
    elif "sec_code" in df.columns and "date" in df.columns:
        order_by = "(sec_code, date)"
    elif "industry_code" in df.columns and "stock_code" in df.columns:
        order_by = "(industry_code, stock_code)"
    elif "concept_code" in df.columns and "stock_code" in df.columns:
        order_by = "(concept_code, stock_code)"
    else:
        order_by = "(code)"
    ensure_table(conn, table, df, order_by)
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
    return len(df)

# ── P1: 特色数据 ──

def sync_mtss(conn: sqlite3.Connection, days: int = None):
    """融资融券历史数据（批量查询）-> 业务表 margin_trading"""
    logger.info(f"=== 开始同步 margin_trading {'(增量 '+str(days)+'天)' if days else '(全量)'} ===")
    stocks = jq.get_all_securities(types=["stock"]).index.tolist()
    batch_size = 200
    total = 0

    if days:
        end_date = TRIAL_END
        start_date = (date.today() - timedelta(days=days)).isoformat()
        # 原 ClickHouse：ALTER TABLE ... DELETE WHERE trade_date >= %(d)s + 等 mutation
        conn.execute("DELETE FROM margin_trading WHERE trade_date >= ?", (start_date,))
        conn.commit()
    else:
        end_date = TRIAL_END
        start_date = None
        conn.execute("DELETE FROM margin_trading")
        conn.commit()

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]
        try:
            if start_date:
                df = jq.get_mtss(batch, start_date=start_date, end_date=end_date)
            else:
                df = jq.get_mtss(batch, count=10000, end_date=end_date)
            # 字段映射到业务表 margin_trading
            df = df.rename(columns={"sec_code": "code", "date": "trade_date"})
            n = insert_df(conn, "margin_trading", df)
            total += n
            logger.info(f"margin_trading batch {i//batch_size+1}/{(len(stocks)-1)//batch_size+1}: {n} rows, total={total}")
        except Exception as e:
            logger.error(f"mtss batch failed: {e}")
        time.sleep(0.3)
    logger.info(f"margin_trading completed: {total} rows")
    return total

def sync_billboard(conn: sqlite3.Connection, days: int = None):
    """龙虎榜数据（按月分段查询 / 增量模式）-> 业务表 billboard"""
    if days:
        logger.info(f"=== 开始同步 billboard (增量 {days} 天) ===")
        start = date.today() - timedelta(days=days)
        end = date.today()
        conn.execute("DELETE FROM billboard WHERE trade_date >= ?", (start.isoformat(),))
        conn.commit()
        total = 0
        try:
            df = jq.get_billboard_list(start_date=start.isoformat(), end_date=end.isoformat())
            n = insert_df(conn, "billboard", df)
            total += n
            logger.info(f"billboard {start}~{end}: {n} rows")
        except Exception as e:
            logger.error(f"billboard failed: {e}")
        logger.info(f"billboard completed: {total} rows")
        return total
    else:
        logger.info("=== 开始同步 billboard (全量) ===")
        conn.execute("DELETE FROM billboard")
        conn.commit()
        start = date(2020, 1, 1)
        end = date.today()
        total = 0
        cur = start
        while cur <= end:
            seg_end = min(cur + timedelta(days=30), end)
            try:
                df = jq.get_billboard_list(start_date=cur.isoformat(), end_date=seg_end.isoformat())
                n = insert_df(conn, "billboard", df)
                total += n
                logger.info(f"billboard {cur}~{seg_end}: {n} rows, total={total}")
            except Exception as e:
                logger.error(f"billboard {cur}~{seg_end} failed: {e}")
            cur = seg_end + timedelta(days=1)
            time.sleep(0.3)
        logger.info(f"billboard completed: {total} rows")
        return total

def sync_locked_shares(conn: sqlite3.Connection, days: int = None):
    """限售股解禁（逐只查询）"""
    logger.info(f"=== 开始同步 locked_shares {'(增量 '+str(days)+'天)' if days else '(全量)'} ===")
    stocks = jq.get_all_securities(types=["stock"]).index.tolist()
    total = 0

    if days:
        start_date = (date.today() - timedelta(days=days)).isoformat()
        end_date = TRIAL_END
    else:
        start_date = TRIAL_START
        end_date = TRIAL_END

    for idx, code in enumerate(stocks):
        try:
            df = jq.get_locked_shares(code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                # 防御 jqdatasdk/NumPy 兼容性问题导致的日期类型异常
                if "day" in df.columns:
                    df["day"] = df["day"].astype(str)
                # locked_shares.num 对应 INTEGER，需为整数
                if "num" in df.columns:
                    df["num"] = df["num"].fillna(0).astype("int64")
                n = insert_df(conn, "locked_shares", df)
                total += n
            if (idx + 1) % 500 == 0:
                logger.info(f"locked_shares: {idx+1}/{len(stocks)} done, total={total}")
        except Exception as e:
            logger.error(f"locked_shares {code} failed: {e}")
        time.sleep(0.1)
    logger.info(f"locked_shares completed: {total} rows")
    return total

def sync_margin_stocks(conn: sqlite3.Connection, days: int = None):
    """融资/融券标的列表（每日）"""
    logger.info(f"=== 开始同步 margin_stocks {'(增量 '+str(days)+'天)' if days else '(全量)'} ===")

    if days:
        # 增量：只查最近 N 个交易日
        end = date.today()
        start = end - timedelta(days=days+5)  # 多查几天确保覆盖交易日
        trade_days = jq.get_trade_days(start.isoformat(), end.isoformat())
        # 只取最后 days 个交易日
        trade_days = trade_days[-days:] if len(trade_days) > days else trade_days
    else:
        trade_days = jq.get_trade_days(TRIAL_START, TRIAL_END)

    total_cash = 0
    total_sec = 0
    for d in trade_days:
        d_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
        try:
            cash = jq.get_margincash_stocks(d_str)
            sec = jq.get_marginsec_stocks(d_str)
            if cash:
                df = pd.DataFrame({"code": cash, "margin_type": "cash", "trade_date": d})
                n = insert_df(conn, "margin_stocks", df)
                total_cash += n
            if sec:
                df = pd.DataFrame({"code": sec, "margin_type": "sec", "trade_date": d})
                n = insert_df(conn, "margin_stocks", df)
                total_sec += n
        except Exception as e:
            logger.error(f"margin_stocks {d_str} failed: {e}")
        time.sleep(0.1)
    logger.info(f"margin_stocks completed: cash={total_cash}, sec={total_sec}")
    return total_cash + total_sec

# ── P2: 行业与概念 ──

def sync_industries(conn: sqlite3.Connection):
    """申万行业成分股 -> 业务表 industry_component"""
    logger.info("=== 开始同步 industry_component ===")
    # 避免同一日期重复写入
    conn.execute("DELETE FROM industry_component WHERE trade_date >= ?", (TRIAL_END,))
    conn.commit()
    total = 0
    for level in ["sw_l1", "sw_l2", "sw_l3"]:
        try:
            inds = jq.get_industries(name=level)
            for code, row in inds.iterrows():
                try:
                    stocks = jq.get_industry_stocks(code, date=TRIAL_END)
                    if stocks:
                        df = pd.DataFrame({"industry_code": code, "industry_name": row.get("name", ""),
                                           "stock_code": stocks, "level": level, "trade_date": TRIAL_END})
                        n = insert_df(conn, "industry_component", df)
                        total += n
                except Exception as e:
                    logger.error(f"industry {code} failed: {e}")
                time.sleep(0.05)
            logger.info(f"industries {level}: {len(inds)} industries")
        except Exception as e:
            logger.error(f"industries {level} failed: {e}")
    logger.info(f"industry_component completed: {total} rows")
    return total

def sync_concepts(conn: sqlite3.Connection):
    """概念板块成分股 -> 业务表 concept_component"""
    logger.info("=== 开始同步 concept_component ===")
    # 避免同一日期重复写入
    conn.execute("DELETE FROM concept_component WHERE trade_date >= ?", (TRIAL_END,))
    conn.commit()
    total = 0
    try:
        concepts = jq.get_concepts()
        for code, row in concepts.iterrows():
            try:
                stocks = jq.get_concept_stocks(code, date=TRIAL_END)
                if stocks:
                    df = pd.DataFrame({"concept_code": code, "concept_name": row.get("name", ""),
                                       "stock_code": stocks, "trade_date": TRIAL_END})
                    n = insert_df(conn, "concept_component", df)
                    total += n
            except Exception as e:
                logger.error(f"concept {code} failed: {e}")
            time.sleep(0.05)
        logger.info(f"concepts: {len(concepts)} concepts, total={total}")
    except Exception as e:
        logger.error(f"concepts failed: {e}")
    logger.info(f"concept_component completed: {total} rows")
    return total

def main():
    parser = argparse.ArgumentParser(description="JQData 扩展数据同步")
    parser.add_argument("--incremental", action="store_true", help="增量模式（只同步高频变化数据）")
    parser.add_argument("--days", type=int, default=3, help="增量天数（默认3天）")
    args = parser.parse_args()

    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER/JQ_PASS required")
    jq.auth(JQ_USER, JQ_PASS)
    conn = _connect()

    if args.incremental:
        logger.info(f"=== 增量模式：高频数据最近 {args.days} 天 ===")
        # 增量只跑日频变化的数据
        sync_margin_stocks(conn, days=args.days)
        sync_mtss(conn, days=args.days)
        sync_billboard(conn, days=args.days)

        # ── 低频数据：检查是否需要更新 ──
        def _days_since(table: str, col: str = "sync_date") -> int:
            """查询某表上次同步距今多少天（sqlite sync_date 为 TEXT 'YYYY-MM-DD HH:MM:SS'）"""
            try:
                r = conn.execute(
                    "SELECT max({col}) FROM {table}".format(col=ident(col), table=ident(table))
                ).fetchone()
                if r and r[0]:
                    last = date.fromisoformat(str(r[0])[:10])
                    return (date.today() - last).days
            except Exception:
                pass
            return 999

        # locked_shares: 每周同步一次
        if _days_since("locked_shares") > 7:
            logger.info("locked_shares 超过7天未更新，执行增量同步(30天)")
            sync_locked_shares(conn, days=30)

        # industries: 每月同步一次
        if _days_since("industry_component") > 30:
            logger.info("industry_component 超过30天未更新，执行全量同步")
            sync_industries(conn)

        # concepts: 每月同步一次
        if _days_since("concept_component") > 30:
            logger.info("concept_component 超过30天未更新，执行全量同步")
            sync_concepts(conn)

        logger.info("=== 增量同步完成 ===")
    else:
        logger.info("=== 全量模式 ===")
        # P1
        sync_mtss(conn)
        sync_billboard(conn)
        sync_locked_shares(conn)
        sync_margin_stocks(conn)

        # P2
        sync_industries(conn)
        sync_concepts(conn)

        logger.info("=== 全部扩展数据同步完成 ===")

if __name__ == "__main__":
    main()

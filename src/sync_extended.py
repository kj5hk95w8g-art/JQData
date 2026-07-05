#!/usr/bin/env python3
"""JQData 特色数据 + 行业概念 + 宏观数据同步 -> ClickHouse

用法:
    # 全量同步（默认）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_extended.py

    # 增量同步（最近 N 天，高频数据）
    JQ_USER=xxx JQ_PASS=xxx python3 src/sync_extended.py --incremental --days 3
"""
import os, sys, time, logging, argparse
from datetime import date, timedelta
import pandas as pd
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jqdata-extended")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB   = os.getenv("CH_DB", "jqdata")
TRIAL_START = os.getenv("TRIAL_START", "2020-01-01")
TRIAL_END   = date.today().isoformat()

CH_TYPE = {"int64": "Float64", "float64": "Float64", "object": "String"}
def _ch_type(dtype): return CH_TYPE.get(str(dtype).lower(), "String")

def _clean_col(name: str) -> str:
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")

def _to_date(val):
    """把字符串/时间戳转换为 datetime.date，供 ClickHouse Date 列使用"""
    if val is None or val == '':
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    if hasattr(val, 'date'):
        return val.date()
    return val

def ensure_table(ch: Client, table: str, df: pd.DataFrame, order_by: str):
    cols = []
    for c in df.columns:
        if c == "id": continue
        cols.append(f"`{_clean_col(c)}` {_ch_type(df[c].dtype)}")
    sql = f"""CREATE TABLE IF NOT EXISTS {table} (
        {', '.join(cols)}, `sync_date` DateTime DEFAULT now()
    ) ENGINE = MergeTree ORDER BY {order_by} SETTINGS index_granularity = 8192"""
    ch.execute(sql)

def insert_df(ch: Client, table: str, df: pd.DataFrame):
    if df is None or df.empty: return 0
    if "id" in df.columns: df = df.drop(columns=["id"])
    df = df.where(pd.notna(df), None)
    df = df.rename(columns={c: _clean_col(c) for c in df.columns})
    if "day" in df.columns:
        df = df.rename(columns={"day": "trade_date"})
    # 查询 ClickHouse 表结构，确定各列类型
    ch_types = {}
    try:
        rows = ch.execute(f"DESCRIBE TABLE {table}")
        for row in rows:
            col_name = row[0]
            col_type = row[1]
            ch_types[col_name] = col_type
    except Exception:
        pass
    for c in df.columns:
        ch_type = ch_types.get(c, 'String')
        if 'Date' in ch_type and 'DateTime' not in ch_type:
            # Date 类型：统一转为 datetime.date
            df[c] = df[c].apply(lambda x: _to_date(x) if pd.notna(x) and x != '' else None)
        elif pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].astype(str)
        elif df[c].dtype == object:
            # String 等其他类型：datetime.date -> str
            if df[c].apply(lambda x: isinstance(x, date)).any():
                df[c] = df[c].astype(str)
            # ClickHouse driver 0.2.10 字符串列不支持 None，替换为空字符串
            df[c] = df[c].fillna('')
    cols = [c for c in df.columns]
    if "code" in cols and "day" in cols:
        order_by = "(code, day)"
    elif "code" in cols and "date" in cols:
        order_by = "(code, date)"
    elif "code" in cols and "trade_date" in cols:
        order_by = "(code, trade_date)"
    elif "sec_code" in cols and "date" in cols:
        order_by = "(sec_code, date)"
    elif "industry_code" in cols and "stock_code" in cols:
        order_by = "(industry_code, stock_code)"
    elif "concept_code" in cols and "stock_code" in cols:
        order_by = "(concept_code, stock_code)"
    else:
        order_by = "(code)"
    ensure_table(ch, table, df, order_by)
    records = [tuple(row) for row in df[cols].values]
    ch.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES", records)
    return len(df)

# ── P1: 特色数据 ──

def sync_mtss(ch: Client, days: int = None):
    """融资融券历史数据（批量查询）-> 业务表 margin_trading"""
    logger.info(f"=== 开始同步 margin_trading {'(增量 '+str(days)+'天)' if days else '(全量)'} ===")
    stocks = jq.get_all_securities(types=["stock"]).index.tolist()
    batch_size = 200
    total = 0

    if days:
        end_date = TRIAL_END
        start_date = (date.today() - timedelta(days=days)).isoformat()
    else:
        end_date = TRIAL_END
        start_date = None

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]
        try:
            if start_date:
                df = jq.get_mtss(batch, start_date=start_date, end_date=end_date)
            else:
                df = jq.get_mtss(batch, count=10000, end_date=end_date)
            # 字段映射到业务表 margin_trading
            df = df.rename(columns={"sec_code": "code", "date": "trade_date"})
            n = insert_df(ch, "margin_trading", df)
            total += n
            logger.info(f"margin_trading batch {i//batch_size+1}/{(len(stocks)-1)//batch_size+1}: {n} rows, total={total}")
        except Exception as e:
            logger.error(f"mtss batch failed: {e}")
        time.sleep(0.3)
    logger.info(f"margin_trading completed: {total} rows")
    return total

def sync_billboard(ch: Client, days: int = None):
    """龙虎榜数据（按月分段查询 / 增量模式）-> 业务表 billboard"""
    if days:
        logger.info(f"=== 开始同步 billboard (增量 {days} 天) ===")
        start = date.today() - timedelta(days=days)
        end = date.today()
        total = 0
        try:
            df = jq.get_billboard_list(start_date=start.isoformat(), end_date=end.isoformat())
            n = insert_df(ch, "billboard", df)
            total += n
            logger.info(f"billboard {start}~{end}: {n} rows")
        except Exception as e:
            logger.error(f"billboard failed: {e}")
        logger.info(f"billboard completed: {total} rows")
        return total
    else:
        logger.info("=== 开始同步 billboard (全量) ===")
        start = date(2020, 1, 1)
        end = date.today()
        total = 0
        cur = start
        while cur <= end:
            seg_end = min(cur + timedelta(days=30), end)
            try:
                df = jq.get_billboard_list(start_date=cur.isoformat(), end_date=seg_end.isoformat())
                n = insert_df(ch, "billboard", df)
                total += n
                logger.info(f"billboard {cur}~{seg_end}: {n} rows, total={total}")
            except Exception as e:
                logger.error(f"billboard {cur}~{seg_end} failed: {e}")
            cur = seg_end + timedelta(days=1)
            time.sleep(0.3)
        logger.info(f"billboard completed: {total} rows")
        return total

def sync_locked_shares(ch: Client, days: int = None):
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
                n = insert_df(ch, "locked_shares", df)
                total += n
            if (idx + 1) % 500 == 0:
                logger.info(f"locked_shares: {idx+1}/{len(stocks)} done, total={total}")
        except Exception as e:
            logger.error(f"locked_shares {code} failed: {e}")
        time.sleep(0.1)
    logger.info(f"locked_shares completed: {total} rows")
    return total

def sync_margin_stocks(ch: Client, days: int = None):
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
                n = insert_df(ch, "margin_stocks", df)
                total_cash += n
            if sec:
                df = pd.DataFrame({"code": sec, "margin_type": "sec", "trade_date": d})
                n = insert_df(ch, "margin_stocks", df)
                total_sec += n
        except Exception as e:
            logger.error(f"margin_stocks {d_str} failed: {e}")
        time.sleep(0.1)
    logger.info(f"margin_stocks completed: cash={total_cash}, sec={total_sec}")
    return total_cash + total_sec

# ── P2: 行业与概念 ──

def sync_industries(ch: Client):
    """申万行业成分股 -> 业务表 industry_component"""
    logger.info("=== 开始同步 industry_component ===")
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
                        n = insert_df(ch, "industry_component", df)
                        total += n
                except Exception as e:
                    logger.error(f"industry {code} failed: {e}")
                time.sleep(0.05)
            logger.info(f"industries {level}: {len(inds)} industries")
        except Exception as e:
            logger.error(f"industries {level} failed: {e}")
    logger.info(f"industries completed: {total} rows")
    return total

def sync_concepts(ch: Client):
    """概念板块成分股 -> 业务表 concept_component"""
    logger.info("=== 开始同步 concept_component ===")
    total = 0
    try:
        concepts = jq.get_concepts()
        for code, row in concepts.iterrows():
            try:
                stocks = jq.get_concept_stocks(code, date=TRIAL_END)
                if stocks:
                    df = pd.DataFrame({"concept_code": code, "concept_name": row.get("name", ""),
                                       "stock_code": stocks, "trade_date": TRIAL_END})
                    n = insert_df(ch, "concept_component", df)
                    total += n
            except Exception as e:
                logger.error(f"concept {code} failed: {e}")
            time.sleep(0.05)
        logger.info(f"concepts: {len(concepts)} concepts, total={total}")
    except Exception as e:
        logger.error(f"concepts failed: {e}")
    logger.info(f"concepts completed: {total} rows")
    return total

def main():
    parser = argparse.ArgumentParser(description="JQData 扩展数据同步")
    parser.add_argument("--incremental", action="store_true", help="增量模式（只同步高频变化数据）")
    parser.add_argument("--days", type=int, default=3, help="增量天数（默认3天）")
    args = parser.parse_args()

    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER/JQ_PASS required")
    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)

    if args.incremental:
        logger.info(f"=== 增量模式：高频数据最近 {args.days} 天 ===")
        # 增量只跑日频变化的数据
        sync_margin_stocks(ch, days=args.days)
        sync_mtss(ch, days=args.days)
        sync_billboard(ch, days=args.days)

        # ── 低频数据：检查是否需要更新 ──
        from datetime import date
        def _days_since(table: str, col: str = "sync_date") -> int:
            """查询某表上次同步距今多少天"""
            try:
                r = ch.execute(f"SELECT max({col}) FROM {table}")
                if r and r[0][0]:
                    last = r[0][0]
                    if hasattr(last, 'date'):
                        last = last.date()
                    return (date.today() - last).days if hasattr(last, 'days') else 999
            except Exception:
                pass
            return 999

        # locked_shares: 每周同步一次
        if _days_since("locked_shares") > 7:
            logger.info("locked_shares 超过7天未更新，执行增量同步(30天)")
            sync_locked_shares(ch, days=30)

        # industries: 每月同步一次
        if _days_since("industry_component") > 30:
            logger.info("industry_component 超过30天未更新，执行全量同步")
            sync_industries(ch)

        # concepts: 每月同步一次
        if _days_since("concept_component") > 30:
            logger.info("concept_component 超过30天未更新，执行全量同步")
            sync_concepts(ch)

        logger.info("=== 增量同步完成 ===")
    else:
        logger.info("=== 全量模式 ===")
        # P1
        sync_mtss(ch)
        sync_billboard(ch)
        sync_locked_shares(ch)
        sync_margin_stocks(ch)

        # P2
        sync_industries(ch)
        sync_concepts(ch)

        logger.info("=== 全部扩展数据同步完成 ===")

if __name__ == "__main__":
    main()

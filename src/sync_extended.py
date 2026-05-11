#!/usr/bin/env python3
"""JQData 特色数据 + 行业概念 + 宏观数据同步 -> ClickHouse

P1 特色: mtss, billboard_list, locked_shares, margin_stocks
P2 分类: industries, concepts
P3 宏观: 核心宏观指标
"""
import os, sys, time, logging
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
    cols = [c for c in df.columns]
    records = [tuple(row) for row in df[cols].values]
    ch.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES", records)
    return len(df)

# ── P1: 特色数据 ──

def sync_mtss(ch: Client):
    """融资融券历史数据（批量查询）"""
    logger.info("=== 开始同步 mtss ===")
    stocks = jq.get_all_securities(types=["stock"]).index.tolist()
    batch_size = 200
    total = 0
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]
        try:
            df = jq.get_mtss(batch, count=10000, end_date=TRIAL_END)
            n = insert_df(ch, "mtss", df)
            total += n
            logger.info(f"mtss batch {i//batch_size+1}/{(len(stocks)-1)//batch_size+1}: {n} rows, total={total}")
        except Exception as e:
            logger.error(f"mtss batch failed: {e}")
        time.sleep(0.3)
    logger.info(f"mtss completed: {total} rows")
    return total

def sync_billboard(ch: Client):
    """龙虎榜数据（按月分段查询）"""
    logger.info("=== 开始同步 billboard_list ===")
    # 按月分段: 2020-01 到 2026-05
    start = date(2020, 1, 1)
    end = date.today()
    total = 0
    cur = start
    while cur <= end:
        seg_end = min(cur + timedelta(days=30), end)
        try:
            df = jq.get_billboard_list(start_date=cur.isoformat(), end_date=seg_end.isoformat())
            n = insert_df(ch, "billboard_list", df)
            total += n
            logger.info(f"billboard {cur}~{seg_end}: {n} rows, total={total}")
        except Exception as e:
            logger.error(f"billboard {cur}~{seg_end} failed: {e}")
        cur = seg_end + timedelta(days=1)
        time.sleep(0.3)
    logger.info(f"billboard completed: {total} rows")
    return total

def sync_locked_shares(ch: Client):
    """限售股解禁（逐只查询）"""
    logger.info("=== 开始同步 locked_shares ===")
    stocks = jq.get_all_securities(types=["stock"]).index.tolist()
    total = 0
    for idx, code in enumerate(stocks):
        try:
            df = jq.get_locked_shares(code, start_date=TRIAL_START, end_date=TRIAL_END)
            n = insert_df(ch, "locked_shares", df)
            total += n
            if (idx + 1) % 500 == 0:
                logger.info(f"locked_shares: {idx+1}/{len(stocks)} done, total={total}")
        except Exception as e:
            logger.error(f"locked_shares {code} failed: {e}")
        time.sleep(0.1)
    logger.info(f"locked_shares completed: {total} rows")
    return total

def sync_margin_stocks(ch: Client):
    """融资/融券标的列表（每日）"""
    logger.info("=== 开始同步 margin_stocks ===")
    trade_days = jq.get_trade_days(TRIAL_START, TRIAL_END)
    total_cash = 0
    total_sec = 0
    for d in trade_days:
        d_str = d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10]
        try:
            cash = jq.get_margincash_stocks(d_str)
            sec = jq.get_marginsec_stocks(d_str)
            if cash:
                df = pd.DataFrame({"code": cash, "type": "cash", "date": d_str})
                n = insert_df(ch, "margin_stocks", df)
                total_cash += n
            if sec:
                df = pd.DataFrame({"code": sec, "type": "sec", "date": d_str})
                n = insert_df(ch, "margin_stocks", df)
                total_sec += n
            if (total_cash + total_sec) % 100000 == 0:
                logger.info(f"margin_stocks: {total_cash+total_sec} rows so far")
        except Exception as e:
            logger.error(f"margin_stocks {d_str} failed: {e}")
        time.sleep(0.1)
    logger.info(f"margin_stocks completed: cash={total_cash}, sec={total_sec}")
    return total_cash + total_sec

# ── P2: 行业与概念 ──

def sync_industries(ch: Client):
    """申万行业成分股"""
    logger.info("=== 开始同步 industries ===")
    total = 0
    for level in ["sw_l1", "sw_l2", "sw_l3"]:
        try:
            inds = jq.get_industries(name=level)
            for code, row in inds.iterrows():
                try:
                    stocks = jq.get_industry_stocks(code, date=TRIAL_END)
                    if stocks:
                        df = pd.DataFrame({"industry_code": code, "industry_name": row.get("name", ""), 
                                           "stock_code": stocks, "level": level, "date": TRIAL_END})
                        n = insert_df(ch, "industry_stocks", df)
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
    """概念板块成分股"""
    logger.info("=== 开始同步 concepts ===")
    total = 0
    try:
        concepts = jq.get_concepts()
        for code, row in concepts.iterrows():
            try:
                stocks = jq.get_concept_stocks(code, date=TRIAL_END)
                if stocks:
                    df = pd.DataFrame({"concept_code": code, "concept_name": row.get("name", ""),
                                       "stock_code": stocks, "date": TRIAL_END})
                    n = insert_df(ch, "concept_stocks", df)
                    total += n
            except Exception as e:
                logger.error(f"concept {code} failed: {e}")
            time.sleep(0.05)
        logger.info(f"concepts: {len(concepts)} concepts, total={total}")
    except Exception as e:
        logger.error(f"concepts failed: {e}")
    logger.info(f"concepts completed: {total} rows")
    return total

# ── P3: 宏观数据 ──

def sync_macro(ch: Client):
    """同步核心宏观指标"""
    logger.info("=== 开始同步 macro ===")
    # 核心表: GDP, CPI, M2, 社融
    tables = [
        ("macro_cn_gdp", "国内生产总值", "select * from macro where table_name='gdp'"),
        ("macro_cn_cpi", "居民消费价格指数", "select * from macro where table_name='cpi'"),
        ("macro_cn_m2", "广义货币供应量", "select * from macro where table_name='m2'"),
        ("macro_cn_pmi", "制造业PMI", "select * from macro where table_name='pmi'"),
    ]
    total = 0
    for table_name, desc, _ in tables:
        try:
            # JQData macro 模块通过 run_query 查询
            q = getattr(jq.macro, table_name.upper().replace("MACRO_CN_", ""), None)
            if q is None:
                logger.warning(f"macro table {table_name} not found")
                continue
            df = jq.macro.run_query(jq.query(q).limit(10000))
            n = insert_df(ch, table_name, df)
            total += n
            logger.info(f"macro {table_name}: {n} rows")
        except Exception as e:
            logger.error(f"macro {table_name} failed: {e}")
        time.sleep(0.3)
    logger.info(f"macro completed: {total} rows")
    return total

def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("JQ_USER/JQ_PASS required")
    jq.auth(JQ_USER, JQ_PASS)
    ch = Client(host=CH_HOST, database=CH_DB)

    # P1
    sync_mtss(ch)
    sync_billboard(ch)
    sync_locked_shares(ch)
    sync_margin_stocks(ch)

    # P2
    sync_industries(ch)
    sync_concepts(ch)

    # P3
    sync_macro(ch)

    logger.info("=== 全部扩展数据同步完成 ===")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""JQData 指数成分权重同步 -> ClickHouse index_component 表

覆盖沃土全部14个基准指数，按日快照写入。
支持全量同步和增量更新模式。

用法:
    # 全量同步（所有指数、全部历史日期）
    JQ_USER=xxx JQ_PASS=xxx python sync_index_weights.py

    # 增量同步（仅最新日期）
    JQ_USER=xxx JQ_PASS=xxx SYNC_MODE=incremental python sync_index_weights.py
"""
import os
import sys
import time
import logging
from datetime import date, timedelta
import pandas as pd
from clickhouse_driver import Client
import jqdatasdk as jq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jqdata-index-weights")

JQ_USER = os.getenv("JQ_USER")
JQ_PASS = os.getenv("JQ_PASS")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
SYNC_MODE = os.getenv("SYNC_MODE", "full")  # full | incremental
TRIAL_END = date.today().isoformat()

# ── 沃土全部14个基准指数 ──
BENCHMARKS = [
    ("000300.XSHG", "沪深300"),
    ("000905.XSHG", "中证500"),
    ("000852.XSHG", "中证1000"),
    ("000001.XSHG", "上证指数"),
    ("000016.XSHG", "上证50"),
    ("000906.XSHG", "中证800"),
    ("399001.XSHE", "深证成指"),
    ("399330.XSHE", "深证100"),
    ("399006.XSHE", "创业板指"),
    ("000688.XSHG", "科创50"),
    ("000012.XSHG", "国债指数"),
    ("932000.XSHG", "中证2000"),
    ("000919.XSHG", "300价值"),
    ("000918.XSHG", "300成长"),
    ("000922.XSHG", "中证红利"),
]


def ensure_index_weights_table(ch: Client):
    """创建 index_weights 表（如不存在）"""
    ch.execute("""
        CREATE TABLE IF NOT EXISTS index_weights (
            code String,
            date String,
            weight Float64,
            display_name String,
            index_code String,
            index_name String,
            sync_date DateTime DEFAULT now()
        ) ENGINE = MergeTree
        ORDER BY (index_code, date, code)
        SETTINGS index_granularity = 8192
    """)
    logger.info("index_weights 表就绪")


def sync_full(ch: Client):
    """全量同步：遍历所有指数，拉取全部可用日期的权重"""
    logger.info("=== 全量同步 index_component ===")
    # 获取交易日历
    try:
        trade_days = jq.get_trade_days(start_date="2015-01-01", end_date=TRIAL_END)
        logger.info(f"交易日区间: 2015-01-01 ~ {TRIAL_END}，共 {len(trade_days)} 天")
    except Exception as e:
        logger.error(f"获取交易日历失败: {e}")
        # 降级：按月取最后一天
        trade_days = pd.date_range("2015-01-01", TRIAL_END, freq="ME")
        logger.warning(f"降级为月末日期，共 {len(trade_days)} 天")

    total = 0
    for jq_code, name in BENCHMARKS:
        logger.info(f"正在同步 {name} ({jq_code})...")
        idx_total = 0
        for d in trade_days:
            d_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
            try:
                df = jq.get_index_weights(jq_code, date=d_str)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                # jqdatasdk get_index_weights 返回: index=code, columns=[display_name, date, weight]
                code_col = "code" if "code" in df.columns else df.columns[0]
                df = df.rename(columns={code_col: "code"})
                df["index_code"] = jq_code
                df["index_name"] = name
                # 转换 date 列为字符串（ClickHouse 列类型为 String）
                if "date" in df.columns:
                    df["date"] = df["date"].astype(str)
                cols = ["code", "date", "weight", "display_name", "index_code", "index_name"]
                cols = [c for c in cols if c in df.columns]
                records = [tuple(row) for row in df[cols].values]
                ch.execute(
                    f"INSERT INTO index_weights ({', '.join(cols)}) VALUES", records
                )
                idx_total += len(records)
            except Exception as e:
                # 非交易日或数据缺失是正常的，静默跳过
                if "index" not in str(e).lower():
                    logger.warning(f"  {d_str} {name} 失败: {e}")
            time.sleep(0.05)  # 限速
        logger.info(f"  {name} 完成: {idx_total} 条")
        total += idx_total
    logger.info(f"=== 全量同步完成: {total} 条 ===")
    return total


def sync_incremental(ch: Client):
    """增量同步：仅同步最近30天的权重数据"""
    logger.info("=== 增量同步 index_component ===")
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    try:
        trade_days = jq.get_trade_days(
            start_date=start_date.isoformat(), end_date=end_date.isoformat()
        )
    except Exception:
        trade_days = pd.date_range(start_date, end_date, freq="B")
    logger.info(f"增量区间: {start_date} ~ {end_date}，共 {len(trade_days)} 天")

    total = 0
    for jq_code, name in BENCHMARKS:
        logger.info(f"正在增量同步 {name} ({jq_code})...")
        for d in trade_days:
            d_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
            try:
                df = jq.get_index_weights(jq_code, date=d_str)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                code_col = "code" if "code" in df.columns else df.columns[0]
                df = df.rename(columns={code_col: "code"})
                df["index_code"] = jq_code
                df["index_name"] = name
                if "date" in df.columns:
                    df["date"] = df["date"].astype(str)
                cols = ["code", "date", "weight", "display_name", "index_code", "index_name"]
                cols = [c for c in cols if c in df.columns]
                records = [tuple(row) for row in df[cols].values]
                # 幂等：删除旧数据再插入
                ch.execute(
                    "ALTER TABLE index_weights DELETE WHERE index_code=%(idx)s AND date=%(d)s",
                    {"idx": jq_code, "d": d_str},
                )
                ch.execute(
                    f"INSERT INTO index_weights ({', '.join(cols)}) VALUES", records
                )
                total += len(records)
            except Exception as e:
                if "index" not in str(e).lower():
                    logger.warning(f"  {d_str} {name} 失败: {e}")
            time.sleep(0.05)
    logger.info(f"=== 增量同步完成: {total} 条 ===")
    return total


def main():
    if not JQ_USER or not JQ_PASS:
        raise RuntimeError("请设置 JQ_USER / JQ_PASS 环境变量")
    jq.auth(JQ_USER, JQ_PASS)
    logger.info("JQData 认证成功")
    ch = Client(host=CH_HOST, database=CH_DB)
    ensure_index_weights_table(ch)

    if SYNC_MODE == "incremental":
        sync_incremental(ch)
    else:
        sync_full(ch)

    logger.info("=== sync_index_weights 全部完成 ===")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""JQData 同步状态检查 — 快速查看各表数据新鲜度

用法:
    python3 src/check_sync_status.py           # 打印所有表状态
    python3 src/check_sync_status.py --alert    # 有异常时 exit 1（供 cron 用）
"""
import os
import sys
import argparse
from datetime import date, timedelta, datetime
from clickhouse_driver import Client

CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")

TABLES = [
    ("stock_daily_pre",  "股票日线(前复权)", "trade_date",  "日"),
    ("stock_daily_post", "股票日线(后复权)", "trade_date",  "日"),
    ("index_daily",      "指数日线",        "trade_date",  "日"),
    ("etf_daily",        "ETF日线",         "trade_date",  "日"),
    ("stock_valuation",  "每日估值",        "trade_date",  "日"),
    ("margin_stocks",    "融资融券标的",     "trade_date",  "日"),
    ("margin_trading",   "融资融券明细",     "trade_date",  "T+1"),
    ("billboard",        "龙虎榜",          "trade_date",  "T+1"),
    ("industry_component", "申万行业成分",   "trade_date",  "月"),
    ("concept_component", "概念板块成分",    "trade_date",  "月"),
    ("index_weights",    "指数成分权重",     "date",        "月"),
    ("stk_xr_xd",        "除权除息",        "a_xr_date",   "月"),
    ("security_info",    "标的信息",        "start_date",  "—"),
]

THRESHOLDS = {"日": 2, "月": 32, "—": 9999}

# 残留备份/临时表(_bakYYYYMMDD)保留超过该天数后，巡检升级为可清理提醒
BACKUP_RETAIN_DAYS = 7


def _check_backup_tables(ch: Client):
    """扫描 jqdata 库中带日期的备份表(*_bakYYYYMMDD)，返回 (打印行, 可清理列表)。

    用于日常巡检提醒：备份表保留 BACKUP_RETAIN_DAYS 天后提示可 DROP，避免长期残留。
    """
    try:
        rows = ch.execute(
            "SELECT name, metadata_modification_time, total_rows "
            "FROM system.tables "
            "WHERE database = %(db)s AND match(name, '_bak[0-9]{8}$') "
            "ORDER BY metadata_modification_time",
            {"db": CH_DB},
        )
    except Exception:
        return [], []
    lines, actionable = [], []
    now = datetime.now()
    for name, mdt, total_rows in rows:
        age = (now - mdt).days if isinstance(mdt, datetime) else -1
        rows_str = f"{total_rows:,}" if isinstance(total_rows, int) else "?"
        if age >= BACKUP_RETAIN_DAYS:
            lines.append(f"  ⚠️  {name}  已保留 {age} 天, {rows_str} 行 — 可清理: DROP TABLE jqdata.{name}")
            actionable.append(name)
        else:
            left = BACKUP_RETAIN_DAYS - age
            lines.append(f"  ℹ️  {name}  保留中({age}天), {rows_str} 行 — 约 {left} 天后可清理")
    return lines, actionable



def _get_last_trade_day(ch: Client) -> date:
    """从 stock_daily_pre 获取最近交易日；失败则回退到今天"""
    try:
        r = ch.execute("SELECT max(trade_date) FROM stock_daily_pre")
        if r and r[0][0]:
            d = r[0][0]
            return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    except Exception:
        pass
    return date.today()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert", action="store_true", help="异常时 exit 1")
    args = parser.parse_args()

    ch = Client(host=CH_HOST, database=CH_DB)
    last_trade_day = _get_last_trade_day(ch)
    today = date.today()
    issues = []

    print(f"{'数据表':<20} {'最新日期':<12} {'延迟天数':<10} {'状态':<10} {'总行数':>12}")
    print("-" * 70)

    for table, name, date_col, freq in TABLES:
        try:
            exists = ch.execute(
                f"SELECT count() FROM system.tables "
                f"WHERE database = '{CH_DB}' AND name = '{table}'"
            )
            if not exists or exists[0][0] == 0:
                print(f"{name:<20} {'—':<12} {'—':<10} {'未创建':<10} {'—':>12}")
                issues.append(f"{name}: 表不存在")
                continue

            r = ch.execute(f"SELECT max({date_col}) FROM {table}")
            max_d = r[0][0] if r and r[0][0] else None
            r2 = ch.execute(f"SELECT count() FROM {table}")
            total = r2[0][0] if r2 else 0

            if max_d is None:
                print(f"{name:<20} {'—':<12} {'—':<10} {'无数据':<10} {total:>12,}")
                issues.append(f"{name}: 空表")
                continue

            if isinstance(max_d, date):
                d = max_d
            elif isinstance(max_d, str):
                d = date.fromisoformat(max_d[:10])
            else:
                d = max_d

            # 日频表按最近交易日判断；T+1 表按最近交易日-1判断；月频/无频率表按自然日判断
            if freq == "日":
                ref_day = last_trade_day
            elif freq == "T+1":
                ref_day = last_trade_day - timedelta(days=1)
            else:
                ref_day = today
            delay = (ref_day - d).days
            threshold = THRESHOLDS.get("日" if freq in ("日", "T+1") else freq, 2)

            if isinstance(delay, int) and delay > threshold:
                status = "❌ 延迟"
                issues.append(f"{name}: 延迟 {delay} 天 (最新={d}, 最近交易日={last_trade_day})")
            elif isinstance(delay, int) and delay > 0:
                status = "⚠️ 稍旧"
            else:
                status = "✅ 正常"

            print(f"{name:<20} {str(d)[:10]:<12} {str(delay):<10} {status:<10} {total:>12,}")
        except Exception as e:
            print(f"{name:<20} {'ERR':<12} {'—':<10} {'查询失败':<10} {'—':>12}")
            issues.append(f"{name}: {e}")

    print("-" * 70)

    # 残留备份/临时表巡检提醒（到期后升级为 issue，--alert 会非零退出）
    bak_lines, bak_actionable = _check_backup_tables(ch)
    if bak_lines:
        print("\n备份表残留提醒:")
        for ln in bak_lines:
            print(ln)
        for t in bak_actionable:
            issues.append(f"备份表 {t} 已超 {BACKUP_RETAIN_DAYS} 天，可清理")

    if issues:
        print(f"\n⚠️ 发现 {len(issues)} 个异常:")
        for i in issues:
            print(f"  - {i}")
        if args.alert:
            sys.exit(1)
    else:
        print("\n✅ 所有表数据正常")


if __name__ == "__main__":
    main()

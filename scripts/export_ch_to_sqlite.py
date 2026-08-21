#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ClickHouse `jqdata` 库 → sqlite 一次性全量导出（M6 平台 sqlite 化）

- ReplacingMergeTree 表带 FINAL 去重导出
- View（stock_daily_none）在 sqlite 里重建为同名 VIEW
- 内部临时/备份表跳过（margin_stocks_dedup_tmp、index_daily_bak20260710）
- Date → TEXT(YYYY-MM-DD)，DateTime → TEXT，Enum/LowCardinality → TEXT，
  Int/UInt → INTEGER，Float → REAL（NaN/Inf → NULL）
- 导出后按 ClickHouse sorting_key 前两列建 sqlite 索引

运行：D 机 /data/jqdata-platform/.venv/bin/python scripts/export_ch_to_sqlite.py
"""
import math
import sqlite3
import sys
import time
from pathlib import Path

from clickhouse_driver import Client

# 使 src/sql_ident.py 可导入（脚本位于 scripts/ 下，仓库根/sys.path 可能不含 src）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sql_ident import ident

OUT = sys.argv[1] if len(sys.argv) > 1 else "/data/jqdata-platform/data/jqdata.db"
FINAL_TABLES = {"concept_component", "index_component", "industry_component", "margin_stocks", "stk_xr_xd"}
SKIP_TABLES = {"margin_stocks_dedup_tmp", "index_daily_bak20260710"}
VIEWS = {"stock_daily_none": "SELECT * FROM stock_daily_pre"}
BATCH = 50_000


def map_type(ch_type: str) -> str:
    t = ch_type.replace("LowCardinality(String)", "String")
    if t.startswith("Nullable("):
        t = t[len("Nullable("):-1]
    if t.startswith("Enum"):
        return "TEXT"
    if t in ("Date", "DateTime", "String", "IPv4", "IPv6"):
        return "TEXT"
    if t.startswith(("Int", "UInt")):
        return "INTEGER"
    if t.startswith(("Float", "Decimal")):
        return "REAL"
    return "TEXT"


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "isoformat"):  # date / datetime → ISO 文本
        return v.isoformat(sep=" ") if isinstance(v, type(v)) and " " in v.isoformat() else v.isoformat()
    return v


def main():
    ch = Client(host="localhost", database="jqdata")
    tables = [
        r[0]
        for r in ch.execute(
            "SELECT name FROM system.tables WHERE database='jqdata' AND engine != 'View' ORDER BY name"
        )
        if r[0] not in SKIP_TABLES
    ]
    print(f"[export] 目标 {OUT}，共 {len(tables)} 表", flush=True)

    out_path = Path(OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    db = sqlite3.connect(OUT)
    db.execute("PRAGMA journal_mode=OFF")  # 一次性导入，不需要 WAL 开销
    db.execute("PRAGMA synchronous=OFF")

    for t in tables:
        t0 = time.time()
        t_i = ident(t)
        desc = ch.execute("DESCRIBE jqdata.`{t}`".format(t=t_i))
        cols = [(name, map_type(tt)) for name, tt, *_ in desc]
        col_names = [c for c, _ in cols]
        ddl = ", ".join('"{c}" {tt}'.format(c=ident(c), tt=tt) for c, tt in cols)
        db.execute('CREATE TABLE "{t}" ({ddl})'.format(t=t_i, ddl=ddl))

        final = " FINAL" if t in FINAL_TABLES else ""
        cols_q = ", ".join("`{c}`".format(c=ident(c)) for c in col_names)
        sql = "SELECT {cols} FROM jqdata.`{t}`{final}".format(
            cols=cols_q, t=t_i, final=final
        )
        ph = ", ".join("?" * len(col_names))
        n = 0
        batch = []
        for row in ch.execute_iter(sql, settings={"max_block_size": 100_000}):
            batch.append(tuple(clean(v) for v in row))
            if len(batch) >= BATCH:
                db.executemany('INSERT INTO "{t}" VALUES ({ph})'.format(t=t_i, ph=ph), batch)
                db.commit()
                n += len(batch)
                batch = []
                print(f"[export] {t} {n}...", flush=True)
        if batch:
            db.executemany('INSERT INTO "{t}" VALUES ({ph})'.format(t=t_i, ph=ph), batch)
            db.commit()
            n += len(batch)

        # 按 sorting_key 前两列建索引
        sk = ch.execute(
            "SELECT sorting_key FROM system.tables "
            "WHERE database='jqdata' AND name='{t}'".format(t=t_i)
        )[0][0]
        if sk:
            key_cols = [c.strip() for c in sk.split(",")[:2] if c.strip() in col_names]
            if key_cols:
                idx = ", ".join('"{c}"'.format(c=ident(c)) for c in key_cols)
                db.execute(
                    'CREATE INDEX "idx_{t}" ON "{t}" ({idx})'.format(t=t_i, idx=idx)
                )
                db.commit()
        print(f"[export] {t} 完成 {n} 行，{time.time()-t0:.0f}s", flush=True)

    for vname, vsql in VIEWS.items():
        db.execute(
            'CREATE VIEW "{vname}" AS {vsql}'.format(vname=ident(vname), vsql=vsql)
        )
    db.commit()
    db.execute("PRAGMA journal_mode=DELETE")
    db.close()
    print("[export] 全部完成", flush=True)


if __name__ == "__main__":
    main()

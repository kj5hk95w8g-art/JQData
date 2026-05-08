from fastapi import FastAPI, Query
from clickhouse_driver import Client
import redis, os

app = FastAPI()
ch = Client(host='localhost')
rd = redis.Redis(host='localhost', port=6379, db=0)

@app.get("/health")
def health():
    ch_ok = False
    rd_ok = False
    try:
        ch.execute("SELECT 1")
        ch_ok = True
    except Exception:
        pass
    try:
        rd.ping()
        rd_ok = True
    except Exception:
        pass
    return {"status": "ok" if (ch_ok and rd_ok) else "degraded",
            "clickhouse": ch_ok, "redis": rd_ok}

@app.get("/v1/daily/{code}")
def get_daily(code: str, start: str, end: str,
              fq: str = Query("pre", pattern="^(pre|post|none)$"),
              fields: str = Query(None)):
    table = f"stock_daily_{fq}"
    cols = fields or "trade_date,open,close,high,low,volume,money"
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end}
    )
    return {"code": code, "count": len(rows), "data": rows}

@app.get("/v1/security/{code}")
def get_security(code: str):
    rows = ch.execute(
        "SELECT * FROM security_info WHERE code = %(code)s",
        {"code": code}
    )
    return {"code": code, "data": rows[0] if rows else None}

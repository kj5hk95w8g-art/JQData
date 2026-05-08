import os
from fastapi import FastAPI, Query
from clickhouse_driver import Client
import redis

app = FastAPI()

# 从环境变量读取连接配置（compose容器名通信）
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

ch = Client(host=CH_HOST, database=CH_DB)
rd = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

@app.get("/health")
def health():
    ch_ok = rd_ok = False
    try:
        ch.execute("SELECT 1")
        ch_ok = True
    except: pass
    try:
        rd.ping()
        rd_ok = True
    except: pass
    return {"status": "ok" if (ch_ok and rd_ok) else "degraded",
            "clickhouse": ch_ok, "redis": rd_ok}

@app.get("/v1/daily/{code}")
def get_daily(code: str, start: str, end: str,
              fq: str = Query("pre", pattern="^(pre|post|none)$"),
              fields: str = Query(None)):
    table = f"stock_daily_{fq}"
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end}
    )
    return {"code": code, "count": len(rows), "data": rows}

@app.get("/v1/index/{code}")
def get_index(code: str, start: str, end: str,
              fields: str = Query(None)):
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM index_daily WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end}
    )
    return {"code": code, "count": len(rows), "data": rows}

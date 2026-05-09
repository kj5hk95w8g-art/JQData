"""
JQData Platform API
提供 RESTful 接口查询 ClickHouse 中的金融数据
"""
import hashlib
import os
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from clickhouse_driver import Client
import redis

app = FastAPI(
    title="JQData Platform API",
    description="公司内部金融数据查询服务",
    version="2.1.0"
)

# ── 配置 ──
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
SIGNATURE_SALT = os.getenv("SIGNATURE_SALT", "default-salt")

# ── 连接 ──
ch = Client(host=CH_HOST, database=CH_DB)
rd = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


# ── 签名验证中间件 ──
@app.middleware("http")
async def signature_auth(request: Request, call_next):
    """请求签名验证，/health 除外"""
    if request.url.path == "/health":
        return await call_next(request)

    signature = request.headers.get("X-Signature", "")
    timestamp = request.headers.get("X-Timestamp", "")

    if not signature or not timestamp:
        return JSONResponse({"detail": "Missing signature"}, status_code=401)

    expected = hashlib.md5(f"{SIGNATURE_SALT}{timestamp}".encode()).hexdigest()
    if signature != expected:
        return JSONResponse({"detail": "Invalid signature"}, status_code=401)

    return await call_next(request)


# ── 健康检查 ──
@app.get("/health")
def health():
    ch_ok = rd_ok = False
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
    return {
        "status": "ok" if (ch_ok and rd_ok) else "degraded",
        "clickhouse": ch_ok,
        "redis": rd_ok,
    }


# ── 单股票日线 ──
@app.get("/v1/daily/{code}")
def get_daily(
    code: str,
    start: str,
    end: str,
    fq: str = Query("pre", pattern="^(pre|post|none)$"),
    fields: str = Query(None),
):
    table = f"stock_daily_{fq}"
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end},
    )
    return {"code": code, "count": len(rows), "data": rows}


# ── 批量股票日线 ──
class BatchDailyRequest(BaseModel):
    codes: List[str]
    start: str
    end: str
    fq: str = "pre"
    fields: Optional[str] = None


@app.post("/v1/daily/batch")
def get_daily_batch(req: BatchDailyRequest):
    """批量查询多只股票日线"""
    table = f"stock_daily_{req.fq}"
    cols = req.fields or "code,trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code IN %(codes)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY code, trade_date",
        {"codes": req.codes, "start": req.start, "end": req.end},
    )
    return {"codes": req.codes, "count": len(rows), "data": rows}


# ── 指数日线 ──
@app.get("/v1/index/{code}")
def get_index(
    code: str,
    start: str,
    end: str,
    fields: str = Query(None),
):
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM index_daily WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end},
    )
    return {"code": code, "count": len(rows), "data": rows}


# ── 标的信息 ──
@app.get("/v1/securities")
def get_securities(
    types: str = Query(None, description="过滤类型：stock,etf,index，逗号分隔"),
):
    """获取全市场标的信息"""
    query = "SELECT code, display_name, name, type, exchange, start_date, end_date FROM security_info"
    params = {}
    if types:
        type_list = [t.strip() for t in types.split(",")]
        query += " WHERE type IN %(types)s"
        params["types"] = type_list
    query += " ORDER BY code"
    rows = ch.execute(query, params)
    return {"count": len(rows), "data": rows}


# ── 交易日历 ──
@app.get("/v1/trade_days")
def get_trade_days(start: str, end: str):
    """获取交易日历"""
    rows = ch.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_pre WHERE trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"start": start, "end": end},
    )
    dates = [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in rows]
    return {"start": start, "end": end, "count": len(dates), "trade_days": dates}


# ── 指数成分股 ──
@app.get("/v1/index/{code}/stocks")
def get_index_stocks(
    code: str,
    trade_date: str = Query(None, description="权重日期，默认最新"),
):
    """获取指数成分股列表（待 index_weights 表同步后实现）"""
    return {
        "code": code,
        "trade_date": trade_date,
        "count": 0,
        "data": [],
        "note": "index_weights 表尚未同步，请先执行正式版数据同步",
    }


# ── 额度查询 ──
@app.get("/v1/query_count")
def query_count():
    """查询当日调用统计（内部使用）"""
    return {"note": "内部系统，暂无额度限制"}

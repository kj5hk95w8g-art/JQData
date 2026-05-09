"""
JQData Platform API
提供 RESTful 接口查询 ClickHouse 中的金融数据
"""
import os
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, Query, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from clickhouse_driver import Client
import redis

app = FastAPI(
    title="JQData Platform API",
    description="公司内部金融数据查询服务",
    version="2.0.0"
)

# ── 配置 ──
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
# 允许的 API Key 列表（逗号分隔），先内置一个默认 key
API_KEYS = set(os.getenv("API_KEYS", "jqdata-default-key-2026").split(","))

# ── 连接 ──
ch = Client(host=CH_HOST, database=CH_DB)
rd = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


# ── 认证中间件 ──
async def verify_api_key(request: Request):
    """校验 X-API-Key，/health 除外"""
    if request.url.path == "/health":
        return None
    api_key = request.headers.get("X-API-Key", "")
    if not api_key or api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return api_key


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    try:
        await verify_api_key(request)
    except HTTPException as e:
        return JSONResponse({"error": e.detail}, status_code=e.status_code)
    return await call_next(request)


# ── 额度统计 ──
def record_quota(api_key: str, rows: int):
    """记录当日查询额度（按返回行数）"""
    today = date.today().isoformat()
    key = f"quota:{api_key}:{today}"
    rd.incrby(key, rows)
    rd.expire(key, 86400 * 2)  # 保留2天


def get_quota(api_key: str) -> dict:
    """获取当前额度使用情况"""
    today = date.today().isoformat()
    key = f"quota:{api_key}:{today}"
    used = int(rd.get(key) or 0)
    # 额度上限从环境变量读，默认 10,000,000（和 JQData 正式版一致）
    total = int(os.getenv("DAILY_QUOTA", "10000000"))
    return {"total": total, "used": used, "spare": max(0, total - used)}


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
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    table = f"stock_daily_{fq}"
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end},
    )
    record_quota(x_api_key, len(rows))
    return {"code": code, "count": len(rows), "data": rows}


# ── 批量股票日线 ──
class BatchDailyRequest(BaseModel):
    codes: List[str]
    start: str
    end: str
    fq: str = "pre"
    fields: Optional[str] = None


@app.post("/v1/daily/batch")
def get_daily_batch(req: BatchDailyRequest, x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """批量查询多只股票日线，返回合并 DataFrame 格式"""
    table = f"stock_daily_{req.fq}"
    cols = req.fields or "code,trade_date,open,high,low,close,volume,amount"
    # ClickHouse IN 查询
    rows = ch.execute(
        f"SELECT {cols} FROM {table} WHERE code IN %(codes)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY code, trade_date",
        {"codes": req.codes, "start": req.start, "end": req.end},
    )
    record_quota(x_api_key, len(rows))
    return {"codes": req.codes, "count": len(rows), "data": rows}


# ── 指数日线 ──
@app.get("/v1/index/{code}")
def get_index(
    code: str,
    start: str,
    end: str,
    fields: str = Query(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    cols = fields or "trade_date,open,high,low,close,volume,amount"
    rows = ch.execute(
        f"SELECT {cols} FROM index_daily WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end},
    )
    record_quota(x_api_key, len(rows))
    return {"code": code, "count": len(rows), "data": rows}


# ── 标的信息 ──
@app.get("/v1/securities")
def get_securities(
    types: str = Query(None, description="过滤类型：stock,etf,index，逗号分隔"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """获取全市场标的信息（对应 jqdatasdk.get_all_securities）"""
    query = "SELECT code, display_name, name, type, exchange, start_date, end_date FROM security_info"
    params = {}
    if types:
        type_list = [t.strip() for t in types.split(",")]
        query += " WHERE type IN %(types)s"
        params["types"] = type_list
    query += " ORDER BY code"
    rows = ch.execute(query, params)
    record_quota(x_api_key, len(rows))
    return {"count": len(rows), "data": rows}


# ── 交易日历 ──
@app.get("/v1/trade_days")
def get_trade_days(
    start: str,
    end: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """获取指定日期范围内的交易日（从 stock_daily_pre 去重推断）"""
    rows = ch.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_pre WHERE trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"start": start, "end": end},
    )
    dates = [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in rows]
    record_quota(x_api_key, len(dates))
    return {"start": start, "end": end, "count": len(dates), "trade_days": dates}


# ── 指数成分股 ──
@app.get("/v1/index/{code}/stocks")
def get_index_stocks(
    code: str,
    trade_date: str = Query(None, description="权重日期，默认最新"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    获取指数成分股列表。
    当前数据层暂未同步 index_weights 表，返回占位数据 + 提示。
    """
    # TODO: 等 index_weights 表同步后，改为实际查询
    record_quota(x_api_key, 0)
    return {
        "code": code,
        "trade_date": trade_date,
        "count": 0,
        "data": [],
        "note": "index_weights 表尚未同步，请先执行正式版数据同步",
    }


# ── 额度查询 ──
@app.get("/v1/query_count")
def query_count(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """查询当日额度使用情况（对应 jqdatasdk.get_query_count）"""
    return get_quota(x_api_key)

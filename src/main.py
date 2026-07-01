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
    version="2.2.0"
)

import math

# 价格字段集合（需要进行复权调整的字段）
PRICE_COLS = {"open", "high", "low", "close", "pre_close", "avg", "high_limit", "low_limit"}


def _sanitize_for_json(rows):
    """将 rows 中的 NaN/Inf 替换为 None，避免 JSON 序列化失败"""
    if not rows:
        return rows
    cleaned = []
    for row in rows:
        new_row = []
        for val in row:
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                new_row.append(None)
            else:
                new_row.append(val)
        cleaned.append(tuple(new_row))
    return cleaned


def _apply_fq_adjustment(rows, col_names, fq):
    """对 pre/post 表的价格字段进行复权调整。
    
    ClickHouse 中存储的是不复权价格，返回时乘以 factor 得到前复权/后复权价格。
    """
    if fq not in ("pre", "post") or "fq_factor" not in col_names:
        return rows
    
    factor_idx = col_names.index("fq_factor")
    adjusted = []
    for row in rows:
        factor = row[factor_idx]
        # 处理 None/NaN factor
        if factor is None or (isinstance(factor, float) and math.isnan(factor)):
            factor = 1.0
        new_row = []
        for i, val in enumerate(row):
            if col_names[i] in PRICE_COLS and val is not None and factor:
                # 跳过 NaN 值
                if isinstance(val, float) and math.isnan(val):
                    new_row.append(None)
                else:
                    new_row.append(val * factor)
            else:
                new_row.append(val)
        adjusted.append(tuple(new_row))
    return adjusted


def _remove_factor_column(rows, col_names):
    """如果客户端没请求 fq_factor，从结果中移除该列"""
    if "fq_factor" not in col_names:
        return rows, col_names
    idx = col_names.index("fq_factor")
    new_cols = col_names[:idx] + col_names[idx+1:]
    new_rows = [row[:idx] + row[idx+1:] for row in rows]
    return new_rows, new_cols

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
    
    # 确保查询包含 fq_factor（用于复权计算）
    query_cols = cols
    if "fq_factor" not in query_cols:
        query_cols += ",fq_factor"
    
    rows = ch.execute(
        f"SELECT {query_cols} FROM {table} WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
        {"code": code, "start": start, "end": end},
    )
    col_names = [c.strip() for c in query_cols.split(",")]
    
    # 复权调整：不复权价格 * factor = 前复权/后复权价格
    rows = _apply_fq_adjustment(rows, col_names, fq)
    
    # 如果客户端没请求 fq_factor，从结果中移除
    if "fq_factor" not in (fields or ""):
        rows, col_names = _remove_factor_column(rows, col_names)
    
    rows = _sanitize_for_json(rows)
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
    
    # 确保查询包含 fq_factor（用于复权计算）
    query_cols = cols
    if "fq_factor" not in query_cols:
        query_cols += ",fq_factor"
    
    rows = ch.execute(
        f"SELECT {query_cols} FROM {table} WHERE code IN %(codes)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY code, trade_date",
        {"codes": req.codes, "start": req.start, "end": req.end},
    )
    col_names = [c.strip() for c in query_cols.split(",")]
    
    # 复权调整：不复权价格 * factor = 前复权/后复权价格
    rows = _apply_fq_adjustment(rows, col_names, req.fq)
    
    # 如果客户端没请求 fq_factor，从结果中移除
    if "fq_factor" not in (req.fields or ""):
        rows, col_names = _remove_factor_column(rows, col_names)
    
    rows = _sanitize_for_json(rows)
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
    rows = _sanitize_for_json(rows)
    return {"code": code, "count": len(rows), "data": rows}


# ── 标的信息 ──
@app.get("/v1/securities")
def get_securities(
    types: str = Query(None, description="过滤类型：stock,etf,index，逗号分隔"),
):
    """获取全市场标的信息"""
    query = "SELECT code, display_name, name, sec_type, exchange, start_date, end_date FROM security_info"
    params = {}
    if types:
        type_list = [t.strip() for t in types.split(",")]
        query += " WHERE sec_type IN %(types)s"
        params["types"] = type_list
    query += " ORDER BY code"
    rows = ch.execute(query, params)
    rows = _sanitize_for_json(rows)
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
    """获取指数成分股列表"""
    table = "index_weights"
    query_sql = f"SELECT code, display_name, weight FROM {table} WHERE index_code=%(code)s"
    params = {"code": code}
    if trade_date:
        query_sql += " AND date=%(trade_date)s"
        params["trade_date"] = trade_date
    query_sql += " ORDER BY weight DESC"
    try:
        rows = ch.execute(query_sql, params)
        return {"code": code, "trade_date": trade_date, "count": len(rows), "data": rows}
    except Exception:
        return {"code": code, "trade_date": trade_date, "count": 0, "data": [],
                "note": "index_weights 表无数据，请先执行 sync_index_weights.py"}


# ── 指数成分权重 ──
@app.get("/v1/index/{code}/weights")
def get_index_weights(
    code: str,
    date: str = Query(None, description="权重日期 YYYY-MM-DD"),
):
    """获取指数成分股权重"""
    table = "index_weights"
    query_sql = f"SELECT code, display_name, weight FROM {table} WHERE index_code=%(code)s"
    params = {"code": code}
    if date:
        query_sql += " AND date=%(date)s"
        params["date"] = date
    query_sql += " ORDER BY weight DESC"
    try:
        rows = ch.execute(query_sql, params)
        return {"code": code, "date": date, "count": len(rows), "data": rows}
    except Exception:
        return {"code": code, "date": date, "count": 0, "data": [],
                "note": "index_weights 表无数据，请先执行 sync_index_weights.py"}


# ── 行业分类 ──
@app.get("/v1/industry")
def get_industry(
    codes: str = Query(None, description="股票代码，逗号分隔，如 000001.XSHE,000002.XSHE"),
    date: str = Query(None, description="查询日期 YYYY-MM-DD"),
    type: str = Query("sw_l1", description="行业分类标准：sw_l1/sw_l2/sw_l3"),
):
    """获取个股申万行业分类"""
    table = "industry_component"
    code_list = [c.strip() for c in codes.split(",")] if codes else None
    query_sql = f"SELECT stock_code, industry_code, industry_name FROM {table} WHERE level=%(type)s"
    params = {"type": type}
    if code_list:
        query_sql += " AND stock_code IN %(codes)s"
        params["codes"] = code_list
    if date:
        query_sql += " AND trade_date=%(date)s"
        params["date"] = date
    rows = ch.execute(query_sql, params)
    # 转为 {code: {industry_name, industry_code}} 格式
    result = {}
    for stock_code, ind_code, ind_name in rows:
        result[stock_code] = {"industry_name": ind_name, "industry_code": ind_code}
    return {"count": len(result), "data": result}


# ── 市值表查询 ──
COLUMN_MAP_VALUATION = {
    "trade_date": "day",
    "code": "code",
    "pe_ratio": "pe_ratio",
    "pb_ratio": "pb_ratio",
    "ps_ratio": "ps_ratio",
    "pcf_ratio": "pcf_ratio",
    "turnover_ratio": "turnover_ratio",
    "market_cap": "market_cap",
    "circulating_market_cap": "circulating_market_cap",
    "total_shares": "capitalization",
    "circulating_shares": "circulating_cap",
    "pe_ratio_lyr": "pe_ratio_lyr",
}


@app.get("/v1/valuation")
def get_valuation(
    codes: str = Query(None, description="股票代码，逗号分隔"),
    start_date: str = Query(None, description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    fields: str = Query(None, description="字段，逗号分隔，如 pe_ratio,pb_ratio,market_cap"),
):
    """获取个股市值表数据（模仿 jqdatasdk.get_valuation）"""
    table = "stock_valuation"
    code_list = [c.strip() for c in codes.split(",")] if codes else None
    
    # 默认返回字段（模仿 jqdatasdk）
    if fields:
        field_list = [f.strip() for f in fields.split(",")]
    else:
        field_list = ["pe_ratio", "pb_ratio", "ps_ratio", "pcf_ratio",
                      "turnover_ratio", "market_cap", "circulating_market_cap"]
    
    # 构建 SELECT 列（ClickHouse → jqdatasdk 字段名）
    select_cols = []
    output_cols = []
    for f in field_list:
        # 找到 ClickHouse 列名（反向映射）
        ch_col = None
        for ch_name, jq_name in COLUMN_MAP_VALUATION.items():
            if jq_name == f:
                ch_col = ch_name
                break
        if ch_col:
            select_cols.append(ch_col)
            output_cols.append(f)
    
    if not select_cols:
        return {"count": 0, "data": [], "fields": [], "error": "no valid fields"}
    
    # 始终包含 code 和 day
    select_str = "code, trade_date AS day, " + ", ".join(select_cols)
    
    query_sql = f"SELECT {select_str} FROM {table} WHERE 1=1"
    params = {}
    
    if code_list:
        query_sql += " AND code IN %(codes)s"
        params["codes"] = code_list
    if start_date:
        query_sql += " AND trade_date >= %(start_date)s"
        params["start_date"] = start_date
    if end_date:
        query_sql += " AND trade_date <= %(end_date)s"
        params["end_date"] = end_date
    
    query_sql += " ORDER BY code, trade_date"
    rows = ch.execute(query_sql, params)
    
    return {
        "count": len(rows),
        "fields": ["code", "day"] + output_cols,
        "data": rows
    }


# ── 除权除息 ──
@app.get("/v1/xr_xd")
def get_xr_xd(
    codes: str = Query(None, description="股票代码，逗号分隔"),
    start_date: str = Query(None, description="除权日起始 YYYY-MM-DD"),
    end_date: str = Query(None, description="除权日结束 YYYY-MM-DD"),
):
    """获取除权除息事件（分红送转）"""
    table = "stk_xr_xd"
    columns = "code,company_name,a_xr_date,bonus_type,dividend_ratio,transfer_ratio," \
              "bonus_ratio_rmb,bonus_amount_rmb,a_registration_date,a_bonus_date," \
              "plan_progress,implementation_pub_date,report_date"
    query_sql = f"SELECT {columns} FROM {table} FINAL WHERE 1=1"
    params = {}
    if codes:
        code_list = [c.strip() for c in codes.split(",")]
        query_sql += " AND code IN %(codes)s"
        params["codes"] = code_list
    if start_date:
        query_sql += " AND a_xr_date >= %(start_date)s"
        params["start_date"] = start_date
    if end_date:
        query_sql += " AND a_xr_date <= %(end_date)s"
        params["end_date"] = end_date
    query_sql += " ORDER BY a_xr_date DESC, code"
    rows = ch.execute(query_sql, params)
    return {"count": len(rows), "data": rows}


# ── 额度查询 ──
@app.get("/v1/query_count")
def query_count():
    """查询当日调用统计（内部使用）"""
    return {"note": "内部系统，暂无额度限制"}

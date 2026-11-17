"""
JQData Platform API — sqlite 后端版（M6 去 ClickHouse/docker 化）

与 main.py（ClickHouse 版）端点/参数/响应结构逐一对齐，仅替换数据层：
  ClickHouse → sqlite3（/data/jqdata-platform/data/jqdata.db，只读）
  Redis（仅健康检查 ping 与同步水位）→ 移除，健康检查改为 sqlite 可读性

差异说明：
  - ClickHouse %(name)s 参数风格 → sqlite ? 占位符（007 规范）
  - trade_date 在 sqlite 中为 TEXT（YYYY-MM-DD），比较语义与 Date 一致
  - 复权调整（fq_factor 乘算）逻辑原样保留在 Python 层
"""
import hashlib
import math
import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sql_ident import ident, ident_list, KNOWN_TABLES, KNOWN_COLUMNS

app = FastAPI(
    title="JQData Platform API",
    description="公司内部金融数据查询服务（sqlite 后端）",
    version="3.0.0",
)

# 价格字段集合（需要进行复权调整的字段）
PRICE_COLS = {"open", "high", "low", "close", "pre_close", "avg", "high_limit", "low_limit"}

DB_PATH = os.getenv("JQDATA_DB", "/data/jqdata-platform/data/jqdata.db")
SIGNATURE_SALT = os.getenv("SIGNATURE_SALT", "default-salt")


def _conn() -> sqlite3.Connection:
    # 只读模式打开，日更写入（WAL）不阻塞读
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)


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

    库中存储的是不复权价格，返回时乘以 factor 得到前复权/后复权价格。
    """
    if fq not in ("pre", "post") or "fq_factor" not in col_names:
        return rows

    factor_idx = col_names.index("fq_factor")
    adjusted = []
    for row in rows:
        factor = row[factor_idx]
        if factor is None or (isinstance(factor, float) and math.isnan(factor)):
            factor = 1.0
        new_row = []
        for i, val in enumerate(row):
            if col_names[i] in PRICE_COLS and val is not None and factor:
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
    new_cols = col_names[:idx] + col_names[idx + 1:]
    new_rows = [row[:idx] + row[idx + 1:] for row in rows]
    return new_rows, new_cols


def _in_clause(values: list) -> str:
    return ",".join("?" * len(values))


def _query(sql: str, params: list):
    conn = _conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


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
    db_ok = False
    try:
        _query("SELECT 1", [])
        db_ok = True
    except Exception:
        pass
    return {"status": "ok" if db_ok else "degraded", "sqlite": db_ok}


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

    query_cols = cols
    if "fq_factor" not in query_cols:
        query_cols += ",fq_factor"

    rows = _query(
        "SELECT {query_cols} FROM {table} "
        "WHERE code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date".format(
            query_cols=ident_list(query_cols, KNOWN_COLUMNS),
            table=ident(table, KNOWN_TABLES),
        ),
        [code, start, end],
    )
    col_names = [c.strip() for c in query_cols.split(",")]

    rows = _apply_fq_adjustment(rows, col_names, fq)

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

    query_cols = cols
    if "fq_factor" not in query_cols:
        query_cols += ",fq_factor"

    rows = _query(
        "SELECT {query_cols} FROM {table} WHERE code IN ({in_clause}) "
        "AND trade_date BETWEEN ? AND ? ORDER BY code, trade_date".format(
            query_cols=ident_list(query_cols, KNOWN_COLUMNS),
            table=ident(table, KNOWN_TABLES),
            in_clause=_in_clause(req.codes),
        ),
        [*req.codes, req.start, req.end],
    )
    col_names = [c.strip() for c in query_cols.split(",")]

    rows = _apply_fq_adjustment(rows, col_names, req.fq)

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
    rows = _query(
        "SELECT {cols} FROM index_daily "
        "WHERE code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date".format(
            cols=ident_list(cols, KNOWN_COLUMNS)
        ),
        [code, start, end],
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
    params: list = []
    if types:
        type_list = [t.strip() for t in types.split(",")]
        query += f" WHERE sec_type IN ({_in_clause(type_list)})"
        params = type_list
    query += " ORDER BY code"
    rows = _query(query, params)
    rows = _sanitize_for_json(rows)
    return {"count": len(rows), "data": rows}


# ── 交易日历 ──
@app.get("/v1/trade_days")
def get_trade_days(start: str, end: str):
    """获取交易日历"""
    rows = _query(
        "SELECT DISTINCT trade_date FROM stock_daily_pre WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        [start, end],
    )
    dates = [str(r[0]) for r in rows]
    return {"start": start, "end": end, "count": len(dates), "trade_days": dates}


# ── 指数成分股 ──
@app.get("/v1/index/{code}/stocks")
def get_index_stocks(
    code: str,
    trade_date: str = Query(None, description="权重日期，默认最新"),
):
    """获取指数成分股列表"""
    query_sql = "SELECT code, display_name, weight FROM index_weights WHERE index_code=?"
    params: list = [code]
    if trade_date:
        query_sql += " AND date=?"
        params.append(trade_date)
    query_sql += " ORDER BY weight DESC"
    try:
        rows = _query(query_sql, params)
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
    query_sql = "SELECT code, display_name, weight FROM index_weights WHERE index_code=?"
    params: list = [code]
    if date:
        query_sql += " AND date=?"
        params.append(date)
    query_sql += " ORDER BY weight DESC"
    try:
        rows = _query(query_sql, params)
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
    code_list = [c.strip() for c in codes.split(",")] if codes else None
    query_sql = "SELECT stock_code, industry_code, industry_name FROM industry_component WHERE level=?"
    params: list = [type]
    if code_list:
        query_sql += f" AND stock_code IN ({_in_clause(code_list)})"
        params += code_list
    if date:
        query_sql += " AND trade_date=?"
        params.append(date)
    rows = _query(query_sql, params)
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
    code_list = [c.strip() for c in codes.split(",")] if codes else None

    if fields:
        field_list = [f.strip() for f in fields.split(",")]
    else:
        field_list = ["pe_ratio", "pb_ratio", "ps_ratio", "pcf_ratio",
                      "turnover_ratio", "market_cap", "circulating_market_cap"]

    select_cols = []
    output_cols = []
    for f in field_list:
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

    select_str = "code, trade_date AS day, " + ident_list(select_cols, KNOWN_COLUMNS)

    query_sql = "SELECT {sel} FROM stock_valuation WHERE 1=1".format(sel=select_str)
    params: list = []

    if code_list:
        query_sql += f" AND code IN ({_in_clause(code_list)})"
        params += code_list
    if start_date:
        query_sql += " AND trade_date >= ?"
        params.append(start_date)
    if end_date:
        query_sql += " AND trade_date <= ?"
        params.append(end_date)

    query_sql += " ORDER BY code, trade_date"
    rows = _query(query_sql, params)

    return {
        "count": len(rows),
        "fields": ["code", "day"] + output_cols,
        "data": rows,
    }


# ── 除权除息 ──
@app.get("/v1/xr_xd")
def get_xr_xd(
    codes: str = Query(None, description="股票代码，逗号分隔"),
    start_date: str = Query(None, description="除权日起始 YYYY-MM-DD"),
    end_date: str = Query(None, description="除权日结束 YYYY-MM-DD"),
):
    """获取除权除息事件（分红送转）"""
    columns = "code,company_name,a_xr_date,bonus_type,dividend_ratio,transfer_ratio," \
              "bonus_ratio_rmb,bonus_amount_rmb,a_registration_date,a_bonus_date," \
              "plan_progress,implementation_pub_date,report_date"
    # ClickHouse 版用 FINAL 去重（ReplacingMergeTree）；sqlite 导出时已按 FINAL 口径落库
    query_sql = "SELECT {columns} FROM stk_xr_xd WHERE 1=1".format(
        columns=ident_list(columns, KNOWN_COLUMNS)
    )
    params: list = []
    if codes:
        code_list = [c.strip() for c in codes.split(",")]
        query_sql += f" AND code IN ({_in_clause(code_list)})"
        params += code_list
    if start_date:
        query_sql += " AND a_xr_date >= ?"
        params.append(start_date)
    if end_date:
        query_sql += " AND a_xr_date <= ?"
        params.append(end_date)
    query_sql += " ORDER BY a_xr_date DESC, code"
    rows = _query(query_sql, params)
    return {"count": len(rows), "data": rows}


# ── 额度查询 ──
@app.get("/v1/query_count")
def query_count():
    """查询当日调用统计（内部使用）"""
    return {"note": "内部系统，暂无额度限制"}

"""业务接口 —— 模仿 jqdatasdk 的 Python API"""
from typing import Union, List, Optional
import pandas as pd
from .client import HTTPClient
from .utils import normalize_fields, rows_to_dataframe, to_jqdata_format
from .exceptions import JQDataError


# 全局客户端实例
_client: Optional[HTTPClient] = None


def _get_client() -> HTTPClient:
    global _client
    if _client is None:
        _client = HTTPClient()
    return _client


def get_price(
    code: Union[str, List[str]],
    start_date: str,
    end_date: str,
    frequency: str = "daily",
    fields: Optional[Union[str, List[str]]] = None,
    skip_paused: bool = False,
    fq: str = "pre",
    panel: bool = True,
) -> pd.DataFrame:
    """
    获取股票/指数行情数据（模仿 jqdatasdk.get_price）

    参数：
        code: 单只代码(str)或多只(list)
        start_date/end_date: 'YYYY-MM-DD'
        frequency: 'daily'（目前仅支持日线）
        fields: 字段列表，如 ['open','close','volume'] 或逗号分隔字符串
        fq: 'pre'/'post'/'none'，复权方式
        panel: True 返回单股票 Index 格式，False 保留 code 列

    返回：pandas DataFrame
    """
    client = _get_client()
    fields_str = normalize_fields(fields)
    codes = [code] if isinstance(code, str) else list(code)

    if len(codes) == 1:
        params = {"start": start_date, "end": end_date, "fq": fq}
        if fields_str:
            params["fields"] = fields_str
        result = client.get(f"/v1/daily/{codes[0]}", params=params)
        rows = result.get("data", [])
        cols = (fields or "trade_date,open,high,low,close,volume,amount").split(",")
        cols = [c.strip() for c in cols]
        df = rows_to_dataframe(rows, cols)
        if panel and "trade_date" in df.columns:
            df = df.set_index("trade_date")
        return df
    else:
        payload = {
            "codes": codes,
            "start": start_date,
            "end": end_date,
            "fq": fq,
        }
        if fields_str:
            payload["fields"] = f"code,{fields_str}"
        else:
            payload["fields"] = "code,trade_date,open,high,low,close,volume,amount"
        result = client.post("/v1/daily/batch", json=payload)
        rows = result.get("data", [])
        cols = payload["fields"].split(",")
        df = rows_to_dataframe(rows, cols)
        return to_jqdata_format(df, panel=panel)


def get_all_securities(
    types: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> pd.DataFrame:
    """获取标的信息（模仿 jqdatasdk.get_all_securities）"""
    client = _get_client()
    params = {}
    if types:
        params["types"] = ",".join(types)
    result = client.get("/v1/securities", params=params)
    rows = result.get("data", [])
    cols = ["code", "display_name", "name", "type", "exchange", "start_date", "end_date"]
    df = rows_to_dataframe(rows, cols, index_col=None)
    for col in ["start_date", "end_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def get_trade_days(start_date: str, end_date: str) -> pd.DatetimeIndex:
    """获取交易日列表（模仿 jqdatasdk.get_trade_days）"""
    client = _get_client()
    result = client.get("/v1/trade_days", params={"start": start_date, "end": end_date})
    dates = result.get("trade_days", [])
    return pd.to_datetime(dates)


def get_index_stocks(code: str, date: Optional[str] = None) -> List[str]:
    """获取指数成分股（模仿 jqdatasdk.get_index_stocks）"""
    client = _get_client()
    params = {}
    if date:
        params["trade_date"] = date
    result = client.get(f"/v1/index/{code}/stocks", params=params)
    stocks = result.get("data", [])
    if not stocks and result.get("note"):
        print(f"⚠️  {result['note']}")
    return [s[0] if isinstance(s, (list, tuple)) else s for s in stocks]


def get_query_count() -> dict:
    """查询当日调用统计"""
    client = _get_client()
    return client.get("/v1/query_count")

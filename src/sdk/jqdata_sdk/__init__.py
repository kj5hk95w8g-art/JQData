"""
JQData SDK —— 模仿 jqdatasdk 接口的内部数据查询工具

用法：
    import jqdata_sdk as jq
    jq.auth(api_key="your-api-key")
    df = jq.get_price("000001.XSHE", start_date="2020-01-01", end_date="2026-05-08")
"""

from .api import (
    auth,
    logout,
    is_auth,
    get_price,
    get_all_securities,
    get_trade_days,
    get_index_stocks,
    get_query_count,
)
from .exceptions import JQDataError, AuthError, APIError, QuotaExceededError

__version__ = "2.0.0"

__all__ = [
    "auth",
    "logout",
    "is_auth",
    "get_price",
    "get_all_securities",
    "get_trade_days",
    "get_index_stocks",
    "get_query_count",
    "JQDataError",
    "AuthError",
    "APIError",
    "QuotaExceededError",
]

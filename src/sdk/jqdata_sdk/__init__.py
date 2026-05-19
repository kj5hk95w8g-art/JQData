"""
JQData SDK —— 模仿 jqdatasdk 接口的内部数据查询工具

用法：
    import jqdata_sdk as jq
    df = jq.get_price("000001.XSHE", start_date="2020-01-01", end_date="2026-05-08")
"""

from .api import (
    get_price,
    get_all_securities,
    get_trade_days,
    get_index_stocks,
    get_index_weights,
    get_industry,
    get_xr_xd,
    get_query_count,
)
from .utils import normalize_code
from .exceptions import JQDataError, AuthError, APIError, QuotaExceededError
from . import macro

__version__ = "2.2.0"

__all__ = [
    "get_price",
    "get_all_securities",
    "get_trade_days",
    "get_index_stocks",
    "get_index_weights",
    "get_industry",
    "get_xr_xd",
    "normalize_code",
    "get_query_count",
    "macro",
    "JQDataError",
    "AuthError",
    "APIError",
    "QuotaExceededError",
]

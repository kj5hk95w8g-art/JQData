"""
JQData SDK —— 模仿 jqdatasdk 接口的内部数据查询工具
服务器: http://101.132.161.52:18080 | 认证: 自动签名

用法:
    import jqdata_sdk as jq
    jq.info()        # 查看接口清单
    help(jq)         # 查看本文档

─────────────────────────── 接口清单 v2.2.0 ───────────────────────────

【行情】
    get_price(code, start_date, end_date, ...) → DataFrame    个股/指数日线OHLCV
    get_all_securities(types=['stock'])         → DataFrame    全市场标的信息

【指数】
    get_index_stocks(code, date)                → List[str]    指数成分股代码
    get_index_weights(index_code, date)         → DataFrame    指数成分股+权重

【行业】
    get_industry(stock_codes, date, type='sw_l1') → dict      申万行业分类

【除权除息】
    get_xr_xd(codes, start_date, end_date)      → DataFrame   分红送转事件

【宏观】
    macro.MAC_BOND_YIELD_10Y                    → str         表标识常量
    macro.run_query(table, start_date, end_date) → DataFrame  宏观数据查询

【工具】
    normalize_code(code)                        → str         代码标准化(000001→000001.XSHE)
    get_trade_days(start_date, end_date)        → DatetimeIndex 交易日历
    get_query_count()                           → dict        调用统计
"""

from .api import (
    get_price,
    get_all_securities,
    get_trade_days,
    get_index_stocks,
    get_index_weights,
    get_industry,
    get_valuation,
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
    "get_valuation",
    "get_xr_xd",
    "normalize_code",
    "get_query_count",
    "macro",
    "info",
    "JQDataError",
    "AuthError",
    "APIError",
    "QuotaExceededError",
]


def info() -> None:
    """运行时自检：打印当前 SDK 版本和全部可用接口清单"""
    catalog = [
        ("行情", "get_price", "个股/指数日线 OHLCV"),
        ("行情", "get_all_securities", "全市场标的信息"),
        ("指数", "get_index_stocks", "指数成分股代码"),
        ("指数", "get_index_weights", "指数成分股+权重"),
        ("行业", "get_industry", "申万行业分类 (sw_l1/l2/l3)"),
        ("行情", "get_valuation", "个股市值表 (PE/PB/市值等)"),
        ("除权除息", "get_xr_xd", "分红送转事件"),
        ("宏观", "macro.run_query", "宏观数据查询 (MAC_BOND_YIELD_10Y等)"),
        ("工具", "normalize_code", "代码标准化 (000001→000001.XSHE)"),
        ("工具", "get_trade_days", "交易日历"),
        ("工具", "get_query_count", "调用统计"),
    ]
    print(f"jqdata_sdk v{__version__}")
    print(f"{'分类':<10} {'接口':<22} 说明")
    print("-" * 56)
    for cat, name, desc in catalog:
        print(f"{cat:<10} {name:<22} {desc}")
    print("-" * 56)
    print(f"服务端: http://101.132.161.52:18080 | 认证: 自动签名")
    print("help(jq) 查看更多用法 →")

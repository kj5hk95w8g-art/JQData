"""JQData SDK macro 模块 —— 已下线

聚宽 License 3 云端账号无宏观数据权限，macro 接口自 v2.2.0+ 起下线。
保留此文件仅为避免历史代码 ``from jqdata_sdk import macro`` 直接 ImportError，
任何实际调用都会抛出 NotImplementedError 并提示迁移方案。
"""
from typing import Optional
import pandas as pd


MAC_BOND_YIELD_10Y = "macro_bond_yield_10y"
MAC_CN_GDP = "macro_cn_gdp"
MAC_CN_CPI = "macro_cn_cpi"
MAC_CN_M2 = "macro_cn_m2"
MAC_CN_PMI = "macro_cn_pmi"


class _MacroOfflineError(NotImplementedError):
    def __init__(self) -> None:
        super().__init__(
            "jqdata_sdk.macro 接口已下线：聚宽 License 3 云端无宏观数据权限。"
            "请使用 akshare / tushare / 国债指数 000012.XSHG 日线等替代数据源。"
        )


def run_query(
    table: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Optional[str] = None,
) -> pd.DataFrame:
    """已下线，调用即抛 NotImplementedError。"""
    raise _MacroOfflineError()

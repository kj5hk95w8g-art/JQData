"""JQData SDK macro 模块 —— 宏观数据查询（模仿 jqdatasdk.macro）

用法:
    import jqdata_sdk as jq
    df = jq.macro.run_query(jq.macro.MAC_BOND_YIELD_10Y, start_date='2020-01-01')
"""
from typing import Optional
import pandas as pd
from ..client import HTTPClient

# ── 宏观表标识常量 ──
MAC_BOND_YIELD_10Y = "macro_bond_yield_10y"
MAC_CN_GDP = "macro_cn_gdp"
MAC_CN_CPI = "macro_cn_cpi"
MAC_CN_M2 = "macro_cn_m2"
MAC_CN_PMI = "macro_cn_pmi"

# 全局客户端（复用模式）
_client: Optional[HTTPClient] = None


def _get_client() -> HTTPClient:
    global _client
    if _client is None:
        _client = HTTPClient()
    return _client


def run_query(
    table: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Optional[str] = None,
) -> pd.DataFrame:
    """
    查询宏观数据表（模仿 jqdatasdk.macro.run_query）

    Args:
        table: 表标识常量，如 MAC_BOND_YIELD_10Y
        start_date: 起始日期 'YYYY-MM-DD'
        end_date: 结束日期 'YYYY-MM-DD'
        columns: 返回字段，逗号分隔，默认全部

    Returns:
        DataFrame

    Example:
        df = jq.macro.run_query(jq.macro.MAC_BOND_YIELD_10Y,
                                start_date='2020-01-01',
                                end_date='2026-05-19')
    """
    client = _get_client()
    payload = {"table": table}
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date
    if columns:
        payload["columns"] = columns
    result = client.post("/v1/macro/query", json=payload)
    rows = result.get("data", [])
    if not rows:
        return pd.DataFrame()
    if isinstance(rows[0], dict):
        return pd.DataFrame(rows)
    return pd.DataFrame(rows)

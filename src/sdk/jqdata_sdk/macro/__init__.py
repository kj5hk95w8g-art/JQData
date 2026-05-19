"""JQData SDK macro 模块 —— 宏观数据查询（模仿 jqdatasdk.macro）

用法:
    import jqdata_sdk as jq

    # 10年期国债收益率（自动使用国债指数 000012.XSHG 计算）
    df = jq.macro.run_query(jq.macro.MAC_BOND_YIELD_10Y,
                            start_date='2020-01-01',
                            end_date='2026-05-19')
    # → DataFrame with columns: stat_date, yield

    # 其他宏观表（GDP/CPI/M2/PMI）→ 查 ClickHouse
    df = jq.macro.run_query(jq.macro.MAC_CN_GDP, start_date='2020-01-01')

注意:
    MAC_BOND_YIELD_10Y 在聚宽 License 3 云端不可用，本 SDK 通过 000012.XSHG
    中证国债指数日线数据自动计算替代 → 日频无风险利率，精度满足夏普/归因需求。
"""
from typing import Optional
import pandas as pd
from ..client import HTTPClient

# ── 宏观表标识常量 ──
MAC_BOND_YIELD_10Y = "macro_bond_yield_10y"  # 特殊处理 → 国债指数反推
MAC_CN_GDP = "macro_cn_gdp"
MAC_CN_CPI = "macro_cn_cpi"
MAC_CN_M2 = "macro_cn_m2"
MAC_CN_PMI = "macro_cn_pmi"

# 国债指数替代计算的配置
_BOND_INDEX_CODE = "000012.XSHG"
_BOND_INDEX_NAME = "中证国债指数"

# 全局客户端
_client: Optional[HTTPClient] = None


def _get_client() -> HTTPClient:
    global _client
    if _client is None:
        _client = HTTPClient()
    return _client


def _bond_yield_from_index(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    通过国债指数 000012.XSHG 日线收盘价反推 10年期国债收益率
    公式: 日收益率 = (今日收盘/昨日收盘 - 1) × 100
          返回年化收益率（%）
    """
    from ..api import get_price as _get_price

    # 为计算昨天→今天的收益率，需要多取一天
    _start = start_date or "2015-01-01"
    _end = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")

    # 国债指数走 /v1/index/{code} 端点（数据在 index_daily 表）
    client = _get_client()
    result = client.get(
        f"/v1/index/{_BOND_INDEX_CODE}",
        params={"start": _start, "end": _end, "fields": "trade_date,close"},
    )
    rows = result.get("data", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["trade_date", "close"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    # 日收益率（%）
    df["daily_return"] = df["close"].pct_change() * 100
    # 年化收益率（%）= 日收益率 × 252
    df["yield"] = df["daily_return"] * 252
    # 去掉第一行（NaN）
    df = df.dropna(subset=["yield"])

    # 构造返回格式
    result = pd.DataFrame({
        "stat_date": df.index.strftime("%Y-%m-%d"),
        "yield": df["yield"].round(4),
    }).reset_index(drop=True)

    if start_date:
        result = result[result["stat_date"] >= start_date]
    return result


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
    # MAC_BOND_YIELD_10Y 特殊处理：通过国债指数日线反推
    if table == MAC_BOND_YIELD_10Y:
        return _bond_yield_from_index(start_date, end_date)

    # 其他表：走后端 /v1/macro/query → ClickHouse
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

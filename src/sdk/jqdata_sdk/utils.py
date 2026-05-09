"""工具函数 —— DataFrame 转换、参数校验"""
from typing import Union, List, Optional
import pandas as pd


# JQData 字段 → ClickHouse 字段映射（对外隐藏）
FIELD_MAP = {
    "money": "amount",
    "factor": "fq_factor",
    "avg": "avg_price",
}


def normalize_fields(fields: Optional[Union[str, List[str]]]) -> Optional[str]:
    """将字段列表或逗号分隔字符串转为 API 参数字符串"""
    if fields is None:
        return None
    if isinstance(fields, str):
        # 将 JQData 字段名映射为内部字段名
        parts = [f.strip() for f in fields.split(",")]
    else:
        parts = list(fields)
    # 映射字段名
    parts = [FIELD_MAP.get(p, p) for p in parts]
    return ",".join(parts)


def rows_to_dataframe(rows: list, columns: list, index_col: str = "trade_date") -> pd.DataFrame:
    """将 API 返回的 rows + columns 转为 pandas DataFrame"""
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows, columns=columns)
    # 日期列转为 datetime
    if index_col in df.columns:
        df[index_col] = pd.to_datetime(df[index_col])
    return df


def to_jqdata_format(df: pd.DataFrame, panel: bool = True, code_col: str = "code") -> pd.DataFrame:
    """
    将 DataFrame 转为 jqdatasdk 风格：
    - panel=True: 单股票时以 trade_date 为 Index
    - panel=False: 多股票时保留 code 列
    """
    if panel and "trade_date" in df.columns:
        if code_col in df.columns and df[code_col].nunique() == 1:
            # 单股票，去掉 code 列，以 trade_date 为 index
            df = df.drop(columns=[code_col])
        if "trade_date" in df.columns:
            df = df.set_index("trade_date")
    return df

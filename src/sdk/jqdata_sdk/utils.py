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
    # 日期列转为 datetime（兼容 str、int Unix 时间戳、datetime 对象）
    if index_col in df.columns:
        col = df[index_col]
        if col.dtype == 'object':
            df[index_col] = pd.to_datetime(col)
        elif pd.api.types.is_numeric_dtype(col):
            # Unix 时间戳（秒）
            df[index_col] = pd.to_datetime(col, unit='s')
        else:
            df[index_col] = pd.to_datetime(col)
    return df


def normalize_code(code: str) -> str:
    """
    代码标准化：纯数字 → JQData 格式（模仿 jqdatasdk.normalize_code）

    Args:
        code: 纯数字代码如 '000001' 或已含后缀的 '000001.XSHE'

    Returns:
        JQData 格式代码，或原样返回（无法识别/港股等）

    Examples:
        normalize_code('000001')  → '000001.XSHE'
        normalize_code('600000')  → '600000.XSHG'
        normalize_code('000001.XSHE') → '000001.XSHE'  # 已含后缀则原样
    """
    code = str(code).strip()
    if '.' in code:
        return code  # 已是完整格式
    # 港股（5位纯数字）— 暂不支持
    if len(code) == 5 and code.isdigit():
        return code  # 无后缀，原样返回
    if code.startswith(('6', '68', '5', '9')):
        return f'{code}.XSHG'
    if code.startswith(('0', '1', '3', '4', '8')):
        return f'{code}.XSHE'
    return code


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

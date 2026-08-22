#!/usr/bin/env python3
"""SQL 标识符白名单校验（M6 sqlite 化合规改造，配合 007 规范红线）

红线：SQL 只用 ? 占位符，禁止 f-string/拼接值。允许插值的只有"标识符"
（表名 / 列名），且必须先经 ident() 校验后才能拼进 SQL 模板：

    1. 必须匹配 ^[A-Za-z_][A-Za-z0-9_]*$ —— 拦截引号 / 分号 / 空格等注入向量；
    2. 若给定 allowed 白名单（set），name 还须在其中。

校验失败抛 ValueError，阻止非法标识符进入 SQL。值一律仍用 ? 占位符绑定，
绝不经此函数拼接。

用法：把原 f-string 拼 SQL（改前形如 `f"...SELECT max({date_col}) FROM {table}"`，
此类写法会被 scripts/guards.sh 检查 2 拦截）改成"模板普通字符串 + ident() 校验 +
.format()"：

    "SELECT max({date_col}) FROM {table}".format(
        date_col=ident(date_col, KNOWN_COLUMNS),
        table=ident(table, KNOWN_TABLES))

列清单（','.join(...) 动态拼接）用 ident_list() 逐项校验后原样重组，
保持原有拼接逻辑不变，仅换拼接方式。
"""
import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ── 已知业务表白名单（覆盖本库全部业务表；sync_meta 为内部表） ──
KNOWN_TABLES = {
    "security_info",
    "stock_daily_pre", "stock_daily_post", "stock_daily_none",
    "index_daily",
    "stock_valuation",
    "index_weights",
    "stk_xr_xd",
    "margin_trading", "billboard", "locked_shares", "margin_stocks",
    "industry_component", "concept_component", "index_component",
    "etf_daily",
    "balance", "income", "cash_flow", "indicator",
    "trade_calendar",
    "sync_meta",
}

# ── 已知列白名单（覆盖本库各业务表列名） ──
KNOWN_COLUMNS = {
    # 通用
    "id", "code", "trade_date", "day", "date", "name", "value",
    # stock_daily_* / index_daily / security_info
    "open", "high", "low", "close", "volume", "amount",
    "fq_factor", "high_limit", "low_limit", "avg_price", "pre_close", "paused",
    "display_name", "sec_type", "exchange", "start_date", "end_date", "list_status",
    # stock_valuation
    "pe_ratio", "pb_ratio", "ps_ratio", "pcf_ratio", "turnover_ratio",
    "total_shares", "market_cap", "circulating_shares", "circulating_market_cap",
    "pe_ratio_lyr", "pcf_ratio2", "dividend_ratio", "free_shares", "free_market_cap",
    "a_shares", "a_market_cap",
    # index_weights
    "weight", "index_code", "index_name",
    # stk_xr_xd
    "company_name", "a_xr_date", "bonus_type", "dividend_ratio", "transfer_ratio",
    "bonus_ratio_rmb", "bonus_amount_rmb", "a_registration_date", "a_bonus_date",
    "plan_progress", "implementation_pub_date", "report_date",
    # 财务三表 + 指标（statDate 大写，notify.py 查询用）
    "statDate", "pubDate",
    # 同步元信息 / 通用日期列
    "sync_date", "key", "note",
}


def ident(name, allowed=None):
    """校验 SQL 标识符（表名 / 列名），合法则原样返回，否则抛 ValueError。

    - 必须为合法标识符 ^[A-Za-z_][A-Za-z0-9_]*$（拦截注入向量）；
    - 若给定 allowed 白名单（set），还须在其中（已知表/列做强校验；
      泛型工具方法可传 None，仅做标识符形状校验，便于复用任意合法标识符）。
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"非法 SQL 标识符: {name!r}")
    if allowed is not None and name not in allowed:
        raise ValueError(f"SQL 标识符不在白名单: {name!r}")
    return name


def ident_list(seq, allowed=None, sep=", "):
    """校验逗号分隔列清单（字符串或序列），逐项 ident() 后原样重组为逗号串。

    仅换拼接方式，不改变原有列清单的内容与顺序；值仍由调用方用 ? 占位符绑定。
    """
    if isinstance(seq, str):
        parts = [p.strip() for p in seq.split(",")]
    else:
        parts = list(seq)
    return sep.join(ident(p, allowed) for p in parts)

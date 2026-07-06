#!/usr/bin/env python3
"""JQData 同步完成飞书卡片通知

用法:
    # 仅查询表状态
    FEISHU_WEBHOOK_URL=xxx python3 src/notify.py [status]

    # 附带阶段执行结果
    python3 src/notify.py [status] [log_file] [phases_json]

环境变量:
    FEISHU_WEBHOOK_URL  飞书机器人 webhook（优先）
    WEBHOOK_URL         通用 webhook（备选）
"""
import os
import sys
import json
import subprocess
from datetime import datetime, date, timedelta
from typing import Optional
from clickhouse_driver import Client

try:
    import jqdatasdk as jq
except ImportError:
    jq = None

# ── 配置 ──
WEBHOOK = os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("WEBHOOK_URL", "")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_DB = os.getenv("CH_DB", "jqdata")

# ── 表配置: (表名, 中文名, 日期列, 频率, 同步时机) ──
# 同步时机: "实时"=15:30+23:00双次 / "日"=仅23:00一次 / "—"=手动全量
TABLES = [
    ("stock_daily_pre",  "股票日线(前复权)", "trade_date",  "日", "实时"),
    ("stock_daily_post", "股票日线(后复权)", "trade_date",  "日", "实时"),
    ("index_daily",      "指数日线",        "trade_date",  "日", "实时"),
    ("etf_daily",        "ETF日线",         "trade_date",  "日", "实时"),
    ("stock_valuation",  "每日估值",        "trade_date",  "日", "日"),
    ("margin_stocks",    "融资融券标的",     "trade_date",  "日", "日"),
    ("margin_trading",   "融资融券明细",     "trade_date",  "T+1", "日"),
    ("billboard",        "龙虎榜",          "trade_date",  "T+1", "日"),
    ("industry_component", "申万行业成分",   "trade_date",  "月", "日"),
    ("concept_component", "概念板块成分",    "trade_date",  "月", "日"),
    ("index_weights",    "指数成分权重",     "date",        "月", "日"),
    ("stk_xr_xd",        "除权除息",        "a_xr_date",   "月", "日"),
    ("balance",          "资产负债表",       "statDate",    "季", "—"),
    ("income",           "利润表",          "statDate",    "季", "—"),
    ("cash_flow",        "现金流量表",       "statDate",    "季", "—"),
    ("indicator",        "财务指标",        "statDate",    "季", "—"),
]


def get_quota() -> str:
    """获取今日额度使用情况"""
    limit = os.getenv("DAILY_QUOTA_LIMIT", "200000000")
    total = "?"

    # 方法1：redis-cli（宿主机直连）
    try:
        used = subprocess.check_output(
            ["redis-cli", "GET", "jqdata_sync:quota_used_today"],
            text=True, timeout=5,
        ).strip()
        if used and used != "(nil)":
            if jq:
                try:
                    q = jq.get_query_count()
                    total = str(q.get("total", "?"))
                except Exception:
                    pass
            return f"今日已用: {int(used):,}（上限 {int(limit)//10000}万，JQ总额 {_fmt_num(total)}）"
    except Exception:
        pass

    # 方法2：Python redis 客户端
    try:
        import redis as _rd
        r = _rd.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                      port=int(os.getenv("REDIS_PORT", "6379")),
                      db=0, decode_responses=True, socket_connect_timeout=3)
        used = r.get("jqdata_sync:quota_used_today")
        if used:
            if jq:
                try:
                    q = jq.get_query_count()
                    total = str(q.get("total", "?"))
                except Exception:
                    pass
            return f"今日已用: {int(used):,}（上限 {int(limit)//10000}万，JQ总额 {_fmt_num(total)}）"
    except Exception:
        pass

    return "未知"


def _fmt_num(val) -> str:
    """大数字格式化: 200000000 → 2亿"""
    try:
        n = int(float(str(val)))
        if n >= 100_000_000:
            return f"{n // 100_000_000}亿"
        elif n >= 10_000:
            return f"{n // 10_000}万"
        else:
            return f"{n:,}"
    except Exception:
        return str(val)


def get_last_trade_day(ch: Client = None) -> Optional[date]:
    """获取最近交易日：优先用 JQData，失败则从 stock_daily_pre 取"""
    if jq:
        try:
            days = jq.get_trade_days(
                start_date=(date.today() - timedelta(days=10)).isoformat(),
                end_date=date.today().isoformat(),
            )
            if len(days) > 0:
                d = days[-1]
                return d.date() if hasattr(d, 'date') else d
        except Exception:
            pass
    if ch:
        try:
            r = ch.execute("SELECT max(trade_date) FROM stock_daily_pre")
            if r and r[0][0]:
                d = r[0][0]
                return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        except Exception:
            pass
    return None


def table_status(max_date_val, last_trade: Optional[date], frequency: str, sync_time: str = "日") -> str:
    """根据最新日期判断表状态，返回可操作的状态描述
    
    sync_time: 该表的同步频率 — "日"(每日23:00), "实时"(15:30+23:00)
    """
    if max_date_val is None:
        return "❌ 无数据"

    if isinstance(max_date_val, str):
        try:
            d = date.fromisoformat(max_date_val[:10])
        except ValueError:
            return f"⚠️ {max_date_val}"
    elif isinstance(max_date_val, date):
        d = max_date_val
    else:
        return f"⚠️ {max_date_val}"

    today = date.today()
    delay = (today - d).days
    # 优先用交易日，否则用自然日；T+1 表允许滞后一个交易日
    if frequency == "T+1" and last_trade:
        ref = last_trade - timedelta(days=1)
    else:
        ref = last_trade if last_trade else today
    trade_delay = (ref - d).days

    if frequency in ("日", "T+1"):
        # 日频表按交易日判断
        td = trade_delay
        if td <= 0:
            return "✅ 正常"
        elif td == 1 and sync_time == "日":
            return f"⏳ 待今晚同步"
        elif td == 1:
            return "⚠️ 延1天"
        elif td <= 3:
            return f"⏳ 待今晚同步" if sync_time == "日" else f"⚠️ 延{td}天"
        else:
            return f"❌ 缺{td}天"

    elif frequency == "月":
        if delay <= 35:
            return "✅ 正常"
        elif delay <= 62:
            return "⏳ 待月度更新"
        else:
            return f"❌ 缺{delay}天"

    elif frequency == "季":
        if delay <= 95:
            return "✅ 正常"
        elif delay <= 130:
            return "⏳ 待季报发布"
        else:
            return f"⚠️ 需全量同步"

    # 无频率标记的表
    if delay <= 3:
        return "✅ 正常"
    elif delay <= 7:
        return f"⏳ 延{delay}天"
    else:
        return f"❌ 缺{delay}天"


def query_tables(ch: Client, last_trade: Optional[date]):
    """查询所有表状态"""
    rows = []
    for table, name_cn, date_col, freq, sync_time in TABLES:
        try:
            # 检查表是否存在
            exists = ch.execute(
                f"SELECT count() FROM system.tables "
                f"WHERE database = '{CH_DB}' AND name = '{table}'"
            )
            if not exists or exists[0][0] == 0:
                rows.append((name_cn, "—", "未创建", "—", "—", freq))
                continue

            # 最新日期
            r = ch.execute(f"SELECT max({date_col}) FROM {table}")
            max_d = r[0][0] if r and r[0][0] else None

            # 总数据量
            r2 = ch.execute(f"SELECT count() FROM {table}")
            total = r2[0][0] if r2 else 0

            # 今日同步量
            try:
                r3 = ch.execute(
                    f"SELECT count() FROM {table} WHERE sync_date >= today()"
                )
                today_new = r3[0][0] if r3 else 0
            except Exception:
                today_new = 0

            status = table_status(max_d, last_trade, freq, sync_time)

            # 格式化日期
            if max_d and isinstance(max_d, date):
                max_str = max_d.strftime("%m-%d")
            elif max_d:
                max_str = str(max_d)[:10]
            else:
                max_str = "N/A"

            today_str = f"{today_new:,}" if today_new > 0 else "—"

            rows.append((name_cn, max_str, status, today_str, f"{total:,}", freq))
        except Exception as e:
            rows.append((name_cn, "ERR", f"❌ {str(e)[:20]}", "—", "—", freq))

    return rows


def parse_phases(phases_path: Optional[str]) -> list:
    """解析阶段执行结果"""
    if not phases_path or not os.path.exists(phases_path):
        return []
    try:
        with open(phases_path) as f:
            data = json.load(f)
        return data.get("phases", [])
    except Exception:
        return []


def read_log_tail(log_path: Optional[str], n: int = 8) -> Optional[str]:
    """读取日志最后 N 行（仅失败时使用）"""
    if not log_path or not os.path.exists(log_path):
        return None
    try:
        out = subprocess.check_output(
            ["tail", f"-{n}", log_path], text=True, timeout=5
        )
        return out.strip()
    except Exception:
        return None


def build_card(
    status: str,
    table_rows: list,
    quota: str,
    phases: list,
    log_tail: Optional[str],
):
    """构建飞书 interactive card"""
    has_failure = "失败" in status or any(p.get("status") == "failed" for p in phases)
    color = "red" if has_failure else "green"

    # 成功/失败的阶段统计
    ok_count = sum(1 for p in phases if p.get("status") == "success")
    fail_count = sum(1 for p in phases if p.get("status") == "failed")
    total_phases = len(phases)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 标题行 ──
    if has_failure:
        if total_phases > 0:
            title = f"JQData 同步 — ⚠️ {ok_count}/{total_phases} 阶段成功"
        else:
            title = f"JQData 同步 — ❌ {status}"
    else:
        title = f"JQData 同步 — ✅ 全部成功"

    # ── 摘要内容 ──
    summary_lines = [
        f"**服务器**: D (101.132.161.52)",
        f"**时间**: {now}",
        f"**额度**: {quota}",
    ]
    summary_md = "\n".join(summary_lines)

    # ── 阶段状态 ──
    phase_md = ""
    if phases:
        phase_items = []
        for p in phases:
            label = p.get("label", "?")
            st = p.get("status", "?")
            if st == "success":
                icon = "✅"
            elif st == "failed":
                icon = "❌"
            elif st == "skipped":
                icon = "⏭️"
            else:
                icon = "❓"
            phase_items.append(f"{icon} {label}")
        phase_md = "  ".join(phase_items)

    # ── 数据表格 ──
    table_header = "| 数据表 | 最新 | 状态 | 今日 | 总量 |"
    table_sep = "|--------|------|------|------|------|"
    table_lines = [table_header, table_sep]
    for row in table_rows:
        name, max_d, st, today, total, _freq = row
        table_lines.append(f"| {name} | {max_d} | {st} | {today} | {total} |")
    table_md = "\n".join(table_lines)

    # ── 失败详情 ──
    failure_md = ""
    if has_failure:
        failed_phases = [p for p in phases if p.get("status") == "failed"]
        if failed_phases:
            failure_lines = ["**失败阶段**:"]
            for p in failed_phases:
                code = p.get("exit_code", "?")
                failure_lines.append(f"  - {p.get('label', '?')} (exit={code})")
            failure_md = "\n".join(failure_lines)

        if log_tail:
            failure_md += f"\n\n**最近日志**:\n```\n{log_tail}\n```"

    # ── 组装卡片 ──
    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary_md},
        },
    ]

    if phase_md:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": phase_md},
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": table_md},
    })

    if failure_md:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": failure_md},
        })

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }
    return card


def send_card(card: dict):
    if not WEBHOOK:
        print("[notify] webhook 未配置，跳过通知")
        return
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", WEBHOOK,
                "-H", "Content-Type: application/json",
                "-d", json.dumps(card, ensure_ascii=False),
            ],
            timeout=15, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[notify] curl 失败: {result.stderr}")
        else:
            resp = result.stdout.strip()
            print(f"[notify] 飞书卡片已发送, resp={resp[:200]}")
    except Exception as e:
        print(f"[notify] 发送异常: {e}")


def main():
    status = sys.argv[1] if len(sys.argv) > 1 else "完成"
    log_file = sys.argv[2] if len(sys.argv) > 2 else None
    phases_file = sys.argv[3] if len(sys.argv) > 3 else None

    # ── 连接 ClickHouse ──
    try:
        ch = Client(host=CH_HOST, database=CH_DB)
    except Exception as e:
        print(f"[notify] ClickHouse 连接失败: {e}")
        # 即使连不上也发个简单告警
        send_card({
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "JQData 同步 — ❌ 通知失败"},
                    "template": "red",
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"无法连接 ClickHouse: {e}"},
                }],
            },
        })
        sys.exit(1)

    # ── JQData 认证（获取交易日需要）──
    if jq:
        jq_user = os.getenv("JQ_USER", "")
        jq_pass = os.getenv("JQ_PASS", "")
        if jq_user and jq_pass:
            try:
                jq.auth(jq_user, jq_pass)
            except Exception as e:
                print(f"[notify] JQData 认证失败: {e}")

    # ── 获取交易日 ──
    last_trade = get_last_trade_day(ch)

    # ── 查询表状态 ──
    table_rows = query_tables(ch, last_trade)

    # ── 解析阶段结果 ──
    phases = parse_phases(phases_file)

    # ── 失败时读日志尾巴 ──
    has_failure = "失败" in status or any(
        p.get("status") == "failed" for p in phases
    )
    log_tail = read_log_tail(log_file) if has_failure else None

    # ── 额度 ──
    quota = get_quota()

    # ── 构建并发送 ──
    card = build_card(status, table_rows, quota, phases, log_tail)
    send_card(card)


if __name__ == "__main__":
    main()

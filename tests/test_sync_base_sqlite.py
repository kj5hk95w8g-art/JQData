#!/usr/bin/env python3
"""sync_base_sqlite 单元测试（M6 sqlite 化）

覆盖：
  - sync_meta 额度计数 / checkpoint 水位读写
  - _insert_batch 去重 + 覆盖幂等（重复插入同 key 不产生重复行，且不影响其他行）
  - _get_db_max_date

外部依赖（jqdatasdk auth / get_query_count / get_trade_days）全部 mock（006 规范）。
数据库使用 tmp_path 临时 sqlite 文件，不触碰生产库。
"""
import sys
from pathlib import Path

import pytest

# 使 src 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import jqdatasdk as jq
from sync_base_sqlite import SyncBaseSqlite


@pytest.fixture
def base(tmp_path, monkeypatch):
    """构造 SyncBaseSqlite：mock JQ 认证/额度查询，使用临时库文件"""
    monkeypatch.setenv("JQ_USER", "test-user")
    monkeypatch.setenv("JQ_PASS", "test-pass")
    monkeypatch.setattr(jq, "auth", lambda *a, **k: None)
    monkeypatch.setattr(jq, "get_query_count", lambda *a, **k: {"spare": 999_999, "total": 1_000_000})
    monkeypatch.setattr(jq, "get_trade_days", lambda *a, **k: [])
    db = tmp_path / "test.db"
    obj = SyncBaseSqlite(db_path=str(db))
    return obj


def test_quota_add_and_persist(base):
    base._add_quota(1234)
    assert base._quota_used_today == 1234
    assert base._quota_ok()

    # 已写入 sync_meta
    row = base.conn.execute(
        "SELECT value FROM sync_meta WHERE key = 'quota_used_today'"
    ).fetchone()
    assert row is not None and row[0] == "1234"


def test_quota_over_limit(base):
    # 逼近上限
    base._quota_used_today = base.quota_limit - 1
    ok = base._add_quota(100)
    assert ok is False
    assert base._quota_ok() is False


def test_checkpoint_read_write(base):
    assert base._get_checkpoint("stock_daily_pre") is None
    base._set_checkpoint("stock_daily_pre", "2026-08-19")
    assert base._get_checkpoint("stock_daily_pre") == "2026-08-19"

    # 不同表互不干扰
    assert base._get_checkpoint("index_daily") is None
    base._set_checkpoint("index_daily", "2026-08-18")
    assert base._get_checkpoint("stock_daily_pre") == "2026-08-19"


def test_quota_date_rollover(base):
    # 写入当天 watermark 与用量
    base._add_quota(500)
    today = base._quota_date
    assert base._meta_get("quota_date") == today

    # 模拟跨天：把 sync_meta 里的日期改成昨天，重新加载应重置
    yesterday = "2020-01-01"
    base._meta_set("quota_date", yesterday)
    base._load_quota_state()
    assert base._quota_used_today == 0
    assert base._meta_get("quota_date") == today


def test_insert_batch_insert_and_dedup_in_memory(base):
    base.conn.execute("CREATE TABLE t (code TEXT, trade_date TEXT, close REAL)")
    base.conn.commit()

    records = [
        ("000001.XSHE", "2026-08-19", 10.0),
        ("000001.XSHE", "2026-08-19", 99.0),  # 同 key 重复，内存去重保留最后一条
        ("000002.XSHE", "2026-08-19", 20.0),
    ]
    base._insert_batch("t", records, "code, trade_date, close")

    rows = base.conn.execute("SELECT code, trade_date, close FROM t ORDER BY code").fetchall()
    assert rows == [
        ("000001.XSHE", "2026-08-19", 99.0),
        ("000002.XSHE", "2026-08-19", 20.0),
    ]


def test_insert_batch_idempotent_overwrite(base):
    base.conn.execute("CREATE TABLE t (code TEXT, trade_date TEXT, close REAL)")
    base.conn.commit()

    # 第一轮
    base._insert_batch(
        "t",
        [("000001.XSHE", "2026-08-19", 10.0), ("000002.XSHE", "2026-08-19", 20.0)],
        "code, trade_date, close",
    )

    # 第二轮：覆盖同 key 值 + 新增新 key，不影响其他已存在 key
    base._insert_batch(
        "t",
        [("000001.XSHE", "2026-08-19", 11.0), ("000003.XSHE", "2026-08-19", 30.0)],
        "code, trade_date, close",
    )

    rows = base.conn.execute("SELECT code, trade_date, close FROM t ORDER BY code").fetchall()
    assert rows == [
        ("000001.XSHE", "2026-08-19", 11.0),
        ("000002.XSHE", "2026-08-19", 20.0),
        ("000003.XSHE", "2026-08-19", 30.0),
    ]

    # 无重复行（覆盖幂等）
    n = base.conn.execute("SELECT count(*) FROM t").fetchone()[0]
    assert n == 3


def test_insert_batch_single_key(base):
    # 单 dedup key（如 stk_xr_xd 按 id 覆盖）
    base.conn.execute("CREATE TABLE x (id INTEGER, note TEXT)")
    base.conn.commit()
    base._insert_batch("x", [(1, "a"), (2, "b")], "id, note", dedup_keys=("id",))
    base._insert_batch("x", [(1, "a2")], "id, note", dedup_keys=("id",))
    rows = base.conn.execute("SELECT id, note FROM x ORDER BY id").fetchall()
    assert rows == [(1, "a2"), (2, "b")]


def test_get_db_max_date(base):
    base.conn.execute("CREATE TABLE t (code TEXT, trade_date TEXT)")
    base.conn.executemany(
        "INSERT INTO t VALUES (?, ?)",
        [("a", "2020-01-05"), ("b", "2026-03-01"), ("c", "2024-12-31")],
    )
    base.conn.commit()
    assert base._get_db_max_date("t") == "2026-03-01"

    # 空表
    base.conn.execute("DELETE FROM t")
    base.conn.commit()
    assert base._get_db_max_date("t") is None

    # 表不存在：返回 None（不抛异常）
    assert base._get_db_max_date("no_such_table") is None

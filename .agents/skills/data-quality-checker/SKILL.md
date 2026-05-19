---
name: data-quality-checker
description: |
  Data quality validation for JQData ClickHouse/Redis platform.
  Use when asked to "检查数据质量" / "有没有缺数据" / "数据对不对" /
  after sync jobs or before reporting data readiness.
  Covers completeness, consistency, boundary checks, and quota monitoring.
---

# Data Quality Checker — JQData

## Check Categories

### 1. Completeness Checks

| Check | Query Example | Threshold |
|-------|--------------|-----------|
| Daily data completeness | `SELECT count(DISTINCT code) FROM security_daily_prices WHERE trade_date = today()` | Should match expected universe (~5200 for A-shares) |
| Minute data completeness | `SELECT count(*) FROM security_minute_prices WHERE trade_date = today()` | Should be ~120万 for full market |
| Missing trading days | Compare `get_trade_days` vs actual data dates | No gaps in trading calendar |
| Index component sync | `SELECT count(*) FROM index_component WHERE date = today()` | Should match index universe |

### 2. Consistency Checks

| Check | Method |
|-------|--------|
| Price continuity | Check for suspicious gaps (e.g., price = 0, volume = 0 on non-halt days) |
| OHLC relationship | `high >= max(open, close)`, `low <= min(open, close)` |
| Forward fill gaps | Check for stale prices (> 1 day without update for active stocks) |
| Cross-table consistency | `security_daily_prices` vs `security_minute_prices` daily aggregate |

### 3. Boundary Checks

| Field | Boundary |
|-------|----------|
| Price | > 0 (suspicious if = 0) |
| Volume | >= 0 |
| Turnover | >= 0 |
| pct_change | Between -20% and +20% for normal stocks (ST: -5%~+5%) |

### 4. Quota Monitoring

- Daily quota: ~10 million records
- Daytime self-limit: 5.5 million
- Check: `SELECT count(*) FROM import_log WHERE date = today()`
- Alert if > 80% quota consumed before 15:00

## Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Missing today's data | Sync script not run | Run sync script on server D container |
| Partial universe | Quota exhausted | Check quota, wait for next day or use backup source |
| Price = 0 | Suspended/delisted stock | Filter with `WHERE close > 0` |
| Duplicate records | Missing ReplacingMergeTree | Use `FINAL` or dedup in query |
| Missing historical fill | Sync backlog | Run batch sync with date range |

## Output Format

```
📊 Data Quality Report — YYYY-MM-DD

✅ Completeness: 5200/5200 daily prices
⚠️  Consistency: 3 stocks with OHLC violation (codes: xxx, yyy, zzz)
❌  Boundary: 1 stock with price=0 (code: xxx, reason: suspended)
📈 Quota: 3.2M / 10M (32%)

Recommendation: ...
```

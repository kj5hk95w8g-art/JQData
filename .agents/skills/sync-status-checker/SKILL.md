---
name: sync-status-checker
description: |
  Data sync status monitoring for JQData platform.
  Use when asked to "检查同步状态" / "今天数据齐了吗" / "sync check" /
  in the morning or after scheduled sync jobs.
  Covers sync job status, data freshness, and quota consumption.
---

# Sync Status Checker — JQData

## Daily Sync Schedule

| Job | Source | Target Table | Frequency | Expected Completion |
|-----|--------|-------------|-----------|---------------------|
| Daily prices | JQData Cloud | `security_daily_prices` | Daily ~19:00 | 20:00 |
| Minute prices | JQData Cloud | `security_minute_prices` | Daily ~19:00 | 21:00 |
| Index components | JQData Cloud | `index_component` | Weekly (Fri) | 22:00 |
| Macro data | JQData Cloud | `macro_bond_yield_10y` | Daily | 20:00 |
| XR/XD events | JQData Cloud | `stk_xr_xd` | Daily | 20:00 |

## Status Check Commands

```bash
# On server D container
docker exec jqdata-api bash -c "python src/check_sync_status.py"

# Or manual checks
clickhouse-client -q "SELECT max(trade_date) FROM security_daily_prices"
clickhouse-client -q "SELECT count(DISTINCT trade_date) FROM security_daily_prices WHERE trade_date >= today() - 7"
```

## Quota Dashboard

```
今日额度: X,XXX,XXX / 10,000,000 (XX%)
白天自限: X,XXX,XXX / 5,500,000 (XX%)
```

## Alert Conditions

| Condition | Severity | Action |
|-----------|----------|--------|
| Missing today's data at 21:00 | 🔴 High | Check sync logs, rerun sync script |
| Partial data (< 90% universe) | 🔴 High | Check quota, check error logs |
| Quota > 80% before 15:00 | ⚠️ Medium | Pause non-essential sync, alert admin |
| Sync job error > 3 retries | ⚠️ Medium | Check network, check JQData cloud status |
| Historical gap detected | ℹ️ Low | Schedule backfill batch |

## Output Format

```
📅 Sync Status — YYYY-MM-DD HH:MM

✅ security_daily_prices: 5200/5200 records, latest 2026-05-12
✅ security_minute_prices: 120万 records, latest 2026-05-12
⏳ index_component: last update 2026-05-09 (expected Fri)
✅ macro_bond_yield_10y: latest 2026-05-12
✅ stk_xr_xd: 15 events today

📊 Quota: 2.1M / 10M (21%)
🎯 Status: All clear / ⚠️ Needs attention / 🔴 Critical
```

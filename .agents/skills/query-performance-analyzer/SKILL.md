---
name: query-performance-analyzer
description: |
  Query performance analysis for JQData ClickHouse.
  Use when asked to "查询好慢" / "优化一下" / "slow query" /
  when ClickHouse queries take too long or consume too much memory.
  Covers query pattern analysis, index usage, and ClickHouse-specific optimizations.
---

# Query Performance Analyzer — JQData

## Common Anti-Patterns

| Anti-Pattern | Impact | Fix |
|-------------|--------|-----|
| `SELECT *` | High I/O, network overhead | Specify only needed columns |
| Missing `WHERE` | Full table scan | Always filter by `trade_date` and/or `code` |
| Large JOINs | Memory explosion | Push join to application layer, use dictionary |
| `ORDER BY` without limit | Full sort | Add `LIMIT` or use `ORDER BY` in outer query |
| String comparison on code | Slow | Use `CODE` type or pre-filter with `IN` |
| Unbounded date ranges | Scan too many partitions | Limit to `trade_date >= subtractMonths(today(), 3)` |

## Optimization Checklist

### Before Execution
- [ ] `WHERE` includes `trade_date` or `code`
- [ ] Column list is explicit (no `*`)
- [ ] `LIMIT` added for exploration queries
- [ ] Date range is bounded
- [ ] Single query标的数 <= 1000

### ClickHouse-Specific
- [ ] `PREWHERE` used for high-selectivity filters
- [ ] `FINAL` avoided if possible (use dedup logic instead)
- [ ] `materialize()` used for computed columns in WHERE
- [ ] `max_execution_time` set for ad-hoc queries

## Diagnostic Queries

```sql
-- Check query log for slow queries
SELECT 
    query,
    duration_ms,
    read_rows,
    read_bytes,
    memory_usage
FROM system.query_log
WHERE event_time > now() - INTERVAL 1 HOUR
ORDER BY duration_ms DESC
LIMIT 10;

-- Check table size and part count
SELECT 
    table,
    formatReadableSize(sum(bytes)) as size,
    count() as parts
FROM system.parts
WHERE active
GROUP BY table;
```

## Performance Targets

| Metric | Target | Action if Exceeded |
|--------|--------|-------------------|
| Daily aggregate query | < 1s | Add materialized view |
| Single-stock history (1y) | < 500ms | Verify ORDER BY |
| Multi-stock scan (1000) | < 3s | Use PREWHERE, limit columns |
| Memory per query | < 4GB | Reduce LIMIT, add filters |

## Output Format

```
🔍 Query Analysis

SQL: (truncated)
❌ Anti-patterns:
  - SELECT * (15 columns needed, table has 80)
  - No trade_date filter

✅ Recommendations:
  - SELECT code, trade_date, close, volume
  - WHERE trade_date >= '2026-01-01' AND code IN (...)
  - Add LIMIT 100 for testing

Expected improvement: 10x faster
```

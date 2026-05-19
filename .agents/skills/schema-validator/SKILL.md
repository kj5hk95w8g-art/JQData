---
name: schema-validator
description: |
  Database schema validation for JQData ClickHouse.
  Use when asked to "检查表结构" / "schema对不对" / "表结构变更" /
  after migration scripts or schema modifications.
  Covers naming conventions, migration compliance, and ClickHouse-specific constraints.
---

# Schema Validator — JQData

## Naming Conventions

| Entity | Convention | Example |
|--------|-----------|---------|
| Table name | snake_case | `security_daily_prices` |
| Column name | snake_case | `trade_date`, `product_code` |
| Migration script | `migrations/VXXX__description.sql` | `V003__add_index_weights.sql` |
| View name | `v_` prefix | `v_daily_summary` |

## Migration Rules

| Rule | Severity |
|------|----------|
| All DDL must be in migration scripts | 🔴 Error |
| No manual `ALTER TABLE` on production | 🔴 Error |
| Destructive changes (DROP COLUMN/TABLE) must have rollback script | ⚠️ Warn |
| New tables must have `ORDER BY` and `PARTITION BY` specified | ⚠️ Warn |

## ClickHouse-Specific Checks

### Engine Selection
- Time-series data: `MergeTree` / `ReplacingMergeTree`
- Dedup required: `ReplacingMergeTree(version)`
- Log data: `StripeLog` (rarely used)

### Required Clauses
```sql
CREATE TABLE example (
    -- columns
) ENGINE = MergeTree()
ORDER BY (code, trade_date)      -- ✅ Required
PARTITION BY toYYYYMM(trade_date) -- ✅ Required for time-series
```

### Prohibited Patterns
| Pattern | Why | Fix |
|---------|-----|-----|
| `DELETE FROM` | ClickHouse slow | Use `ALTER TABLE ... DROP PARTITION` |
| `UPDATE` | Limited support | Use `ReplacingMergeTree` + re-insert |
| No primary key | Bad performance | Always specify `ORDER BY` |
| `UUID` default | Unnecessary | Use auto-increment or natural key |

## Validation Checklist

```
✅ Table names: snake_case
✅ Columns: snake_case, no SQL keywords
✅ Engine: appropriate for use case
✅ ORDER BY: specified
✅ PARTITION BY: specified for large tables
✅ Migration script: VXXX__description.sql format
✅ Rollback script: present for destructive changes
```

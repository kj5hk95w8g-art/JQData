---
name: factor-calculator
description: |
  Technical factor calculation helper for JQData platform.
  Use when asked to "计算MA" / "算一下RSI" / "factor" / "indicator" /
  when computing technical indicators on stock/etf data.
  Covers indicator formulas, parameter conventions, and ClickHouse implementation hints.
---

# Factor Calculator — JQData

## Available Indicators

### Moving Averages

| Indicator | Formula | Parameters |
|-----------|---------|-----------|
| MA (SMA) | `SUM(close, N) / N` | N: 5, 10, 20, 60, 120, 250 |
| EMA | `EMA(today) = close * k + EMA(yesterday) * (1-k)` | k = 2/(N+1) |
| VWAP | `SUM(close * volume, N) / SUM(volume, N)` | Intraday or rolling |

### Momentum

| Indicator | Formula | Parameters |
|-----------|---------|-----------|
| RSI | `100 - 100/(1 + RS)` | N=14, overbought=70, oversold=30 |
| MACD | `EMA(12) - EMA(26)`, Signal=EMA(9) | Standard |
| ROC | `(close - close[N]) / close[N] * 100` | N=12 |

### Volatility

| Indicator | Formula | Parameters |
|-----------|---------|-----------|
| Bollinger Bands | `MA ± 2 * STD(close, N)` | N=20 |
| ATR | `MA(TR, N)`, TR = max(high-low, |high-close_prev|, |low-close_prev|)` | N=14 |

## JQData Implementation

### Using jqdata_sdk
```python
import jqdata_sdk as jq

# Get price data
df = jq.get_price('000001.XSHE', start_date='2026-01-01', end_date='2026-05-12',
                  frequency='daily', fields=['open', 'close', 'high', 'low', 'volume'])

# Calculate MA20
df['ma20'] = df['close'].rolling(20).mean()
```

### Using ClickHouse (Aggregate)
```sql
-- MA20 for single stock
SELECT 
    trade_date,
    close,
    avg(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20
FROM security_daily_prices
WHERE code = '000001.XSHE'
ORDER BY trade_date;
```

## Parameter Conventions

| Parameter | Default | Common Values |
|-----------|---------|---------------|
| MA short | 5 | 5, 10 |
| MA medium | 20 | 20, 60 |
| MA long | 250 | 120, 250 |
| RSI period | 14 | 14 |
| MACD fast/slow/signal | 12/26/9 | Standard |
| Bollinger period | 20 | 20 |
| Bollinger std | 2 | 2 |

## Constraints

- Factor calculation must use **前复权** (forward-adjusted) prices
- `get_price(fq='pre')` for jqdata_sdk
- For backtests, ensure dividend/split adjustment is applied
- Single factor query should limit to <= 1000 stocks
- Large-scale factor computation should use ClickHouse, not pandas loop

## Output Format

```
📈 Factor Calculation — MA20

Input: 000001.XSHE, 2026-01-01 to 2026-05-12
Method: jq.get_price(..., fq='pre') + rolling(20).mean()

Result (last 5 days):
| Date | Close | MA20 |
|------|-------|------|
| ... | ... | ... |
```

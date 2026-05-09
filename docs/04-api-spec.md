# JQData Platform API 文档

## 认证

所有接口（除 `/health` 外）需在 Header 中携带 API Key：

```
X-API-Key: your-api-key
```

---

## 端点列表

### GET /health
健康检查，无需认证。

**响应：**
```json
{
  "status": "ok",
  "clickhouse": true,
  "redis": true
}
```

---

### GET /v1/daily/{code}
查询单股票日线行情。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| code | path | ✅ | 股票代码，如 `000001.XSHE` |
| start | query | ✅ | 起始日期 `YYYY-MM-DD` |
| end | query | ✅ | 结束日期 `YYYY-MM-DD` |
| fq | query | ❌ | 复权方式：`pre`/`post`/`none`，默认 `pre` |
| fields | query | ❌ | 字段筛选，逗号分隔 |

**响应：**
```json
{
  "code": "000001.XSHE",
  "count": 4,
  "data": [
    ["2025-02-05", 10.92, 10.94, 10.76, 10.80, 88828580.0, 961193600.0]
  ]
}
```

---

### POST /v1/daily/batch
批量查询多只股票日线。

**请求体：**
```json
{
  "codes": ["000001.XSHE", "000002.XSHE"],
  "start": "2025-02-01",
  "end": "2025-02-05",
  "fq": "pre",
  "fields": "code,trade_date,open,close,volume"
}
```

**响应：** 同单股票，但每行第一列为 `code`。

---

### GET /v1/index/{code}
查询指数日线行情。参数同 `/v1/daily/{code}`，但无 `fq` 参数。

---

### GET /v1/securities
获取全市场标的信息。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| types | query | ❌ | 过滤类型：`stock`,`etf`,`index`，逗号分隔 |

---

### GET /v1/trade_days
获取交易日历。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start | query | ✅ | 起始日期 |
| end | query | ✅ | 结束日期 |

**响应：**
```json
{
  "start": "2025-02-01",
  "end": "2025-02-10",
  "count": 4,
  "trade_days": ["2025-02-05", "2025-02-06", "2025-02-07", "2025-02-10"]
}
```

---

### GET /v1/query_count
查询当日额度使用情况。

**响应：**
```json
{
  "total": 10000000,
  "used": 12345,
  "spare": 9987655
}
```

---

## SDK 用法

```python
import jqdata_sdk as jq

jq.auth(api_key="your-key")

# 单股票
df = jq.get_price("000001.XSHE", start_date="2020-01-01", end_date="2026-05-08")

# 多股票
df = jq.get_price(["000001.XSHE", "000002.XSHE"], start_date="2020-01-01", end_date="2026-05-08")

# 标的信息
securities = jq.get_all_securities(types=["stock"])

# 交易日历
days = jq.get_trade_days("2025-01-01", "2025-12-31")

# 额度
print(jq.get_query_count())
```

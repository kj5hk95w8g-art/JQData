# JQData Platform API 文档

## 认证

所有接口（除 `/health` 和 `/nginx-health` 外）需在 Header 中携带请求签名：

```
X-Timestamp: 1715241600
X-Signature: a1b2c3d4e5f6...
```

**签名计算：** `signature = md5(SALT + timestamp)`，其中 SALT 为服务器与 SDK 约定的盐值。

> 不需要手动计算签名，使用 Python SDK 会自动附加。

---

## 端点列表

### GET /health
健康检查，无需认证。返回 ClickHouse 和 Redis 连接状态。

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

### GET /v1/index/{code}/weights
获取指数成分股权重。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| code | path | ✅ | 指数代码，如 `000300.XSHG` |
| date | query | ❌ | 权重日期 `YYYY-MM-DD`，默认最新 |

**响应：**
```json
{
  "code": "000300.XSHG",
  "date": "2026-05-19",
  "count": 300,
  "data": [
    ["600519.XSHG", "贵州茅台", 5.23],
    ["000858.XSHE", "五粮液", 2.15]
  ]
}
```

---

### GET /v1/industry
获取个股申万行业分类。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| codes | query | ✅ | 股票代码，逗号分隔 |
| type | query | ❌ | 行业标准：`sw_l1`/`sw_l2`/`sw_l3`，默认 `sw_l1` |
| date | query | ❌ | 查询日期 `YYYY-MM-DD` |

**响应：**
```json
{
  "count": 2,
  "data": {
    "000001.XSHE": {"industry_name": "银行", "industry_code": "801780.SI"},
    "000002.XSHE": {"industry_name": "房地产", "industry_code": "801180.SI"}
  }
}
```

---

### GET /v1/xr_xd
获取除权除息事件（分红送转）。

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| codes | query | ❌ | 股票代码，逗号分隔，空=不限制 |
| start_date | query | ❌ | 除权日起始 `YYYY-MM-DD` |
| end_date | query | ❌ | 除权日结束 `YYYY-MM-DD` |

**响应：**
```json
{
  "count": 1,
  "data": [
    ["000001.XSHE", "平安银行", "2026-06-15", "分红", 0.15, 0, 1.5, 1500000000, ...]
  ]
}
```

---

### POST /v1/macro/query
通用宏观数据查询。

**请求体：**
```json
{
  "table": "macro_bond_yield_10y",
  "start_date": "2020-01-01",
  "end_date": "2026-05-19",
  "columns": "stat_date, yield"
}
```

**响应：**
```json
{
  "count": 1560,
  "data": [
    ["2020-01-02", 3.15],
    ["2020-01-03", 3.14]
  ]
}
```

---

### GET /v1/query_count
查询当日调用统计（内部使用，当前无额度限制）。

**响应：**
```json
{
  "note": "内部系统，暂无额度限制"
}
```

---

## SDK 用法

**安装：**
```bash
pip install git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

**使用（完全无感知，无需 auth）：**
```python
import jqdata_sdk as jq

# 个股/指数日线
df = jq.get_price("000001.XSHE", start_date="2020-01-01", end_date="2026-05-08")
df = jq.get_price(["000001.XSHE", "000002.XSHE"], start_date="2020-01-01", end_date="2026-05-08")

# 标的信息
securities = jq.get_all_securities(types=["stock"])

# 交易日历
days = jq.get_trade_days("2025-01-01", "2025-12-31")

# ✨ 指数成分权重（v2.2.0 新增）
weights = jq.get_index_weights("000300.XSHG", date="2026-05-19")

# ✨ 申万行业分类（v2.2.0 新增）
industry = jq.get_industry(["000001.XSHE", "000002.XSHE"], date="2026-05-19")

# ✨ 除权除息事件（v2.2.0 新增）
xr_xd = jq.get_xr_xd(codes="000001.XSHE", start_date="2025-01-01")

# ✨ 代码标准化（v2.2.0 新增）
jq.normalize_code("000001")  # → '000001.XSHE'

# ✨ 宏观数据（v2.2.0 新增）
df = jq.macro.run_query(jq.macro.MAC_BOND_YIELD_10Y, start_date="2020-01-01")
```

**升级：**
```bash
pip install --upgrade git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

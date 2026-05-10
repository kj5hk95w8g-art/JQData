# JQData 外部项目接入指南

> 适用对象：资产沃土、资产管家等内部项目  
> 接入方式：Python SDK（推荐）或 REST API

---

## 一、服务地址

| 环境 | 地址 | 说明 |
|------|------|------|
| **公网** | `http://101.132.161.52:18080` | 外网/出差可用 |
| **内网** | `http://172.24.52.237:18080` | VPC 内网，延迟更低（需安全组放行）|

---

## 二、方式一：Python SDK（推荐）

### 安装

```bash
pip install git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

> 需要服务器有 SSH key 且已添加到 GitHub Deploy Keys

### 使用示例

```python
import jqdata_sdk as jq

# 无需 auth()，内置 base_url 和自动签名
df = jq.get_price("000001.XSHE", start_date="2024-01-01", end_date="2024-12-31")

# 批量查询多股票
df = jq.get_price(
    ["000001.XSHE", "000002.XSHE"],
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# 标的信息
securities = jq.get_all_securities(types=["stock"])

# 交易日历
days = jq.get_trade_days("2024-01-01", "2024-12-31")

# 额度查询（当前内部系统无限制，返回提示信息）
print(jq.get_query_count())
```

### 依赖

- Python >= 3.8
- requests
- pandas（返回 DataFrame）

---

## 三、方式二：REST API 直接调用

### 认证

所有接口（除 `/health`）需在 Header 中携带签名：

```
X-Timestamp: 1715241600
X-Signature: md5(SALT + timestamp)
```

> 不需要手动计算，SDK 已封装。如直接调用 API，需参考 `src/sdk/jqdata_sdk/client.py` 的 `_sign_headers()` 实现。

### 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查，无需认证 |
| GET | `/v1/daily/{code}` | 单股票日线 |
| POST | `/v1/daily/batch` | 批量股票日线 |
| GET | `/v1/index/{code}` | 指数日线 |
| GET | `/v1/securities` | 全市场标的信息 |
| GET | `/v1/trade_days` | 交易日历 |

### 请求示例

```bash
curl -H "X-Timestamp: $(date +%s)" \
     -H "X-Signature: $(echo -n "yuntu-jqdata-2026-internal-only$(date +%s)" | md5sum | cut -d' ' -f1)" \
     "http://101.132.161.52:18080/v1/daily/000001.XSHE?start=2024-01-01&end=2024-12-31"
```

---

## 四、限流与额度

| 项目 | 值 | 说明 |
|------|-----|------|
| Nginx 单 IP 限流 | 500 r/s | 超过返回 503 |
| JQData 日额度 | 1000 万条 | 由 JQData 官方账号控制 |
| 内部同步占用 | 550 万/天（白天）| 晚上 23:00 定时任务可能用完剩余额度 |

**建议：** 沃土/管家项目白天使用，避免晚上 23:00 与全量同步任务抢额度。

---

## 五、高频场景备选：直连 ClickHouse

如果 SDK/API 性能不够（如每次查 500+ 只股票），可直连 ClickHouse：

```python
from clickhouse_driver import Client
ch = Client(host='172.24.52.237', port=9000, database='jqdata')

rows = ch.execute(
    "SELECT trade_date, open, close, volume FROM stock_daily_pre WHERE code=%(code)s AND trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
    {"code": "000001.XSHE", "start": "2024-01-01", "end": "2024-12-31"}
)
```

**约束：**
- 仅限 VPC 内网（`172.24.52.0/24`）
- 禁止全表扫描，必须带 `WHERE code = ?` 或 `WHERE trade_date >= ?`
- 需提前与 JQData 项目确认，避免影响数据同步任务

---

## 六、联系人

JQData 项目维护：当前 AI 会话  
技术文档：`docs/README.md` / `docs/api-reference.md`

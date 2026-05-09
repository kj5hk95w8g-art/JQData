# REQ-004 技术架构设计

> 目标：请求签名认证 + 无感知SDK + 单IP限流

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      团队成员电脑                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Python SDK (jqdata_sdk)                              │  │
│  │  ├── 内置 base_url (当前内网IP / 后期公网IP)           │  │
│  │  ├── 内置 SECRET_SALT (硬编码)                        │  │
│  │  ├── HTTPClient (发请求)                              │  │
│  │  │   └── 每次请求自动附加签名头                        │  │
│  │  │       X-Timestamp: 当前时间戳                       │  │
│  │  │       X-Signature: md5(SALT + timestamp)            │  │
│  │  └── api.py (业务接口: get_price等)                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ HTTPS/HTTP
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    D 服务器 (Nginx 可选)                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  FastAPI (jqdata-api 容器)                            │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  中间件栈 (处理顺序从上到下)                      │  │  │
│  │  │  1. CORS 中间件 (如果需要)                        │  │  │
│  │  │  2. 签名验证中间件 ← 新增                        │  │  │
│  │  │     ├── 跳过 /health (Docker healthcheck)         │  │  │
│  │  │     ├── 读取 X-Timestamp + X-Signature            │  │  │
│  │  │     ├── 检查时间窗口 (±5分钟)                     │  │  │
│  │  │     ├── 计算 expected = md5(SALT + timestamp)     │  │  │
│  │  │     ├── 比对 signature == expected                │  │  │
│  │  │     └── 失败返回 401                              │  │  │
│  │  │  3. IP限流中间件 ← 新增                          │  │  │
│  │  │     ├── 基于 Redis 计数器 (sliding window)        │  │  │
│  │  │     ├── 单IP 100条/秒                             │  │  │
│  │  │     └── 超限返回 429                              │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  │                          │                            │  │
│  │                          ▼                            │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  路由层 (已有 + 新增)                            │  │  │
│  │  │  ├── GET  /health           (放行，无签名)        │  │  │
│  │  │  ├── GET  /v1/daily/{code}  (需签名)             │  │  │
│  │  │  ├── POST /v1/daily/batch   (需签名)             │  │  │
│  │  │  ├── GET  /v1/index/{code}  (需签名)             │  │  │
│  │  │  ├── GET  /v1/securities    (需签名)             │  │  │
│  │  │  ├── GET  /v1/trade_days    (需签名)             │  │  │
│  │  │  └── GET  /v1/query_count   (需签名)             │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Redis (jqdata-redis 容器)                             │  │
│  │  ├── IP限流计数器 (TTL 1秒)                           │  │
│  │  └── 调用统计 (可选)                                   │  │
│  └───────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  ClickHouse (jqdata-clickhouse 容器)                   │  │
│  │  └── 数据查询                                          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、关键模块设计

### 2.1 签名验证中间件 (FastAPI)

**位置：** `src/main.py` 或独立 `src/middleware/auth.py`

**职责：**
- 拦截所有请求（除 `/health`）
- 验证 X-Timestamp + X-Signature
- 时间窗口检查（±300秒）
- 失败返回 401 Unauthorized

**盐值来源：** 环境变量 `SIGNATURE_SALT`
```bash
# docker-compose.d.yml 环境变量
SIGNATURE_SALT=yuntu-jqdata-2026-internal-only
```

**支持多盐值并行（平滑升级）：**
```bash
# 新旧盐值同时支持（逗号分隔）
SIGNATURE_SALT=yuntu-jqdata-2026-internal-only,yuntu-jqdata-2027-v2-new
```

### 2.2 IP限流中间件 (FastAPI)

**位置：** `src/main.py` 或独立 `src/middleware/rate_limit.py`

**实现：** Redis + Sliding Window
```
Key: rate_limit:{client_ip}:{timestamp//1}
Value: 计数器
TTL: 2秒
```

**规则：**
- 单IP 1秒内不超过 100 次请求
- 超限返回 429 Too Many Requests
- 响应头附带 `X-RateLimit-Remaining` 和 `X-RateLimit-Reset`

### 2.3 SDK签名模块

**位置：** `src/sdk/jqdata_sdk/client.py` (改造)

**职责：**
- 每次 HTTP 请求前自动计算签名
- 附加到请求头中

```python
import hashlib
import time

SECRET_SALT = "yuntu-jqdata-2026-internal-only"
BASE_URL = "http://172.24.52.237:8000"  # 后期换公网IP

def _sign_headers():
    timestamp = str(int(time.time()))
    signature = hashlib.md5(f"{SECRET_SALT}{timestamp}".encode()).hexdigest()
    return {
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }
```

---

## 三、数据流

### 正常请求
```
用户调用 get_price()
    → SDK 计算签名 (timestamp + md5)
    → HTTP GET /v1/daily/000001.XSHE
       Headers: X-Timestamp=1234567890, X-Signature=abc123...
    → FastAPI 中间件
       → 读取 Headers
       → 检查时间窗口 (|now - timestamp| <= 300)
       → 计算 expected = md5(SALT + timestamp)
       → 比对 signature == expected ✅
       → 检查 IP 限流 (Redis 计数)
       → 限流通过 ✅
    → 路由处理
       → 查 ClickHouse
       → 返回 JSON
    → SDK 解析 JSON → DataFrame
```

### 异常请求
```
无签名请求
    → 中间件检测 X-Signature 缺失
    → 返回 401 {"detail": "Missing signature"}

过期请求 (timestamp > 5分钟前)
    → 中间件检测时间窗口
    → 返回 401 {"detail": "Request expired"}

错误签名
    → 中间件计算 expected != signature
    → 返回 401 {"detail": "Invalid signature"}

IP限流
    → 中间件检查 Redis 计数 > 100
    → 返回 429 {"detail": "Rate limit exceeded"}
```

---

## 四、配置管理

### 服务器端 (docker-compose.d.yml)

```yaml
services:
  api:
    environment:
      # 签名盐值（支持多盐值并行，逗号分隔）
      SIGNATURE_SALT: "yuntu-jqdata-2026-internal-only"
      
      # 时间窗口（秒）
      SIGNATURE_TIME_WINDOW: "300"
      
      # IP限流：每秒最大请求数
      RATE_LIMIT_PER_SECOND: "100"
```

### SDK端 (硬编码)

```python
# src/sdk/jqdata_sdk/client.py

# 当前版本盐值
SECRET_SALT = "yuntu-jqdata-2026-internal-only"

# 服务器地址（后期公网IP绑定后更新）
DEFAULT_BASE_URL = "http://172.24.52.237:8000"
```

---

## 五、盐值更换流程

```
Day 0: 发现盐值泄露（或到期）
    │
    ├── 1. 生成新盐值：yuntu-jqdata-2027-v2-new
    │
    ├── 2. 服务器端更新
    │      SIGNATURE_SALT="旧盐值,新盐值"（并行支持）
    │      docker compose restart api
    │
    ├── 3. 更新SDK代码
    │      SECRET_SALT = "yuntu-jqdata-2027-v2-new"
    │      git commit → git push
    │
    ├── 4. 通知团队成员升级
    │      pip install --upgrade git+ssh://...
    │
    ├── 5. 并行期（7天）
    │      旧版SDK → 旧盐值 → 服务器认 ✅
    │      新版SDK → 新盐值 → 服务器认 ✅
    │
    └── 6. Day 7: 禁用旧盐值
           SIGNATURE_SALT="新盐值"（只剩一个）
           docker compose restart api
           旧版SDK请求 → 401（提示升级）
```

---

## 六、代码变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/main.py` | 修改 | 添加签名验证中间件、IP限流中间件 |
| `src/middleware/auth.py` | 新增 | 签名验证逻辑（可选拆分） |
| `src/middleware/rate_limit.py` | 新增 | IP限流逻辑（可选拆分） |
| `src/sdk/jqdata_sdk/client.py` | 修改 | 每次请求自动附加签名头 |
| `docker-compose.d.yml` | 修改 | 添加 SIGNATURE_SALT 环境变量 |
| `docs/REQ-004-architecture.md` | 新增 | 本文档 |

---

## 七、性能考量

| 指标 | 目标 | 说明 |
|------|------|------|
| 签名计算 | < 1ms | md5计算极快，不影响响应 |
| 时间窗口检查 | < 1ms | 简单整数比较 |
| IP限流（Redis） | < 5ms | Redis incr + ttl |
| 总体中间件开销 | < 10ms | 可忽略 |
| P95 响应时间 | < 200ms | 含中间件 + ClickHouse查询 |

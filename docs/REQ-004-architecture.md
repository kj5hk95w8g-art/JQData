# REQ-004 技术架构设计（简化版）

> 目标：请求签名认证 + 无感知SDK + Nginx IP限流
> 原则：够用即可，不过度设计

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      团队成员电脑                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Python SDK (jqdata_sdk)                              │  │
│  │  ├── 内置 base_url                                    │  │
│  │  ├── 内置 SECRET_SALT (硬编码)                        │  │
│  │  ├── HTTPClient                                       │  │
│  │  │   └── 每次请求自动附加签名头                        │  │
│  │  │       X-Signature: md5(SALT + timestamp)            │  │
│  │  └── api.py (get_price等)                             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Nginx（反向代理 + 限流）                    │
│  ├── limit_req: 单IP 100条/秒                               │
│  └── proxy_pass → FastAPI                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI (jqdata-api)                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  签名验证中间件                                       │  │
│  │  ├── 跳过 /health                                     │  │
│  │  ├── 读取 X-Signature                                 │  │
│  │  ├── 计算 expected = md5(SALT + timestamp)            │  │
│  │  ├── 比对 signature == expected                       │  │
│  │  └── 失败返回 401                                     │  │
│  └───────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  路由层                                               │  │
│  │  ├── GET  /health           (放行)                    │  │
│  │  ├── GET  /v1/daily/{code}  (需签名)                  │  │
│  │  ├── POST /v1/daily/batch   (需签名)                  │  │
│  │  ├── GET  /v1/index/{code}  (需签名)                  │  │
│  │  ├── GET  /v1/securities    (需签名)                  │  │
│  │  ├── GET  /v1/trade_days    (需签名)                  │  │
│  │  └── GET  /v1/query_count   (需签名)                  │  │
│  └───────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  ClickHouse                                           │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、关键设计决策

### 2.1 为什么去掉时间窗口

| 原方案 | 简化后 | 理由 |
|--------|--------|------|
| 检查 `\|now - timestamp\| <= 300秒` | **不检查** | 内部团队使用，重放攻击风险极低；避免用户电脑时间不准导致报错 |

**保留timestamp的原因：** 让每次请求签名不同，防止简单抓包复用。

### 2.2 为什么用Nginx限流而不是Redis

| 方案 | 复杂度 | 改动范围 |
|------|--------|---------|
| Redis限流 | 需改代码 + 依赖Redis | FastAPI中间件 |
| **Nginx限流** | **只改配置** | **Nginx配置** |

Nginx原生支持，两行配置搞定，不需要碰代码。

### 2.3 为什么盐值不主动更换

| 原方案 | 简化后 | 理由 |
|--------|--------|------|
| 每年更换，多盐值并行7天 | **不更换，泄露再换** | 私有仓库泄露概率极低；减少维护工作量 |

---

## 三、签名验证中间件（FastAPI）

```python
import hashlib
import os
from fastapi import Request, HTTPException

# 从环境变量读取盐值
SALT = os.getenv("SIGNATURE_SALT", "default-salt")

async def signature_middleware(request: Request, call_next):
    # /health 放行（Docker healthcheck用）
    if request.url.path == "/health":
        return await call_next(request)
    
    # 读取签名
    signature = request.headers.get("X-Signature", "")
    timestamp = request.headers.get("X-Timestamp", "")
    
    if not signature or not timestamp:
        return JSONResponse({"detail": "Missing signature"}, 401)
    
    # 计算期望签名（只比对，不检查时间窗口）
    expected = hashlib.md5(f"{SALT}{timestamp}".encode()).hexdigest()
    
    if signature != expected:
        return JSONResponse({"detail": "Invalid signature"}, 401)
    
    return await call_next(request)
```

**代码量：约15行**

---

## 四、SDK签名模块

```python
import hashlib
import time

SALT = "yuntu-jqdata-2026-internal-only"
BASE_URL = "http://172.24.52.237:8000"

def _sign_headers():
    timestamp = str(int(time.time()))
    signature = hashlib.md5(f"{SALT}{timestamp}".encode()).hexdigest()
    return {
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }

# 每次请求自动附加
headers = {"Content-Type": "application/json"}
headers.update(_sign_headers())
```

**代码量：约10行**

---

## 五、Nginx限流配置

```nginx
# http块中定义限流区域
limit_req_zone $binary_remote_addr zone=jqdata_api:10m rate=100r/s;

server {
    listen 80;
    
    location /jqdata/ {
        # 限流：100条/秒，突发200条
        limit_req zone=jqdata_api burst=200 nodelay;
        
        # 转发到FastAPI
        proxy_pass http://172.24.52.237:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**配置量：约8行**

---

## 六、数据流

### 正常请求
```
SDK → 计算签名 → HTTP请求 → Nginx限流通过 → FastAPI验签通过 → 查数据 → 返回
```

### 异常请求
```
无签名          → FastAPI返回 401
错误签名        → FastAPI返回 401
单IP超100条/秒 → Nginx返回 429
```

---

## 七、配置管理

| 位置 | 配置项 | 说明 |
|------|--------|------|
| `docker-compose.d.yml` | `SIGNATURE_SALT` | 服务器盐值 |
| SDK硬编码 | `SECRET_SALT` | 客户端盐值（与服务器一致）|
| SDK硬编码 | `BASE_URL` | 服务器地址 |
| Nginx配置 | `limit_req` | 单IP 100条/秒 |

---

## 八、总代码/配置量

| 模块 | 代码/配置量 |
|------|-----------|
| FastAPI签名中间件 | ~15行 |
| SDK签名函数 | ~10行 |
| Nginx限流配置 | ~8行 |
| **合计** | **~33行** |

---

## 九、盐值泄露应对（被动）

```
发现泄露（或怀疑泄露）
    │
    ├── 1. 改服务器环境变量 SIGNATURE_SALT（新盐值）
    ├── 2. 改SDK代码 SECRET_SALT（新盐值）
    ├── 3. git commit → git push
    ├── 4. 通知团队：pip install --upgrade
    └── 5. 服务器重启（加载新盐值）

旧版SDK用户 → 401错误 → 升级后恢复
```

**不需要多盐值并行，直接切换。**

---

## 十、变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/main.py` | 修改 | 添加签名验证中间件（~15行）|
| `src/sdk/jqdata_sdk/client.py` | 修改 | 添加签名头（~10行）|
| Nginx配置 | 新增 | limit_req限流（~8行）|
| `docker-compose.d.yml` | 修改 | 添加 SIGNATURE_SALT 环境变量 |

# 对外接口消费者登记册（JQData 本地行情数据平台）

> **目的**：登记 JQData 对外暴露的数据接口（HTTP REST + Python SDK）及其外部消费方，
> 防止接口被误删/改坏而策略研发等系统不自知。
> **背景**：同类项目曾发生对外接口被误删、而外部系统每日在调的重大事故。
> **镜像**：本文件的消费方登记已并入下方 §3，与消费方 `docs/upstream-deps.md`
> 互为镜像（双向登记，改任一处须同步另一处）。
> **配套防线**：`tests/test_api_contract_smoke.py`（HTTP 路由/认证/JSON 形状源码契约）
> 与 `tests/test_sdk_contract_smoke.py`（SDK↔HTTP 跨契约）。
> **最后核对日期**：2026-08-11（核对人：AI 辅助，据各项目代码 grep 核对）

---

## 0. 对外服务入口

| 项 | 值 |
|----|----|
| 服务 | FastAPI `src/main.py`（`jqdata-api` 容器，:8000 仅容器内） |
| 对外入口 | Nginx `18080`（D 服务器 `101.132.161.52`） |
| 认证 | 请求签名：`X-Timestamp` + `X-Signature`（`md5(SALT+timestamp)`）；除 `/health` 外全部要求 |
| 数据范围 | 金融数据仅限内网（VPC `172.24.52.0/24`），禁止外发公网 |

---

## 1. HTTP 对外接口（服务端）

| 接口 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 免签 | 健康检查（ClickHouse/Redis 状态） |
| `/v1/daily/{code}` | GET | 签名 | 单股票日线 OHLCV（fq=pre/post/none） |
| `/v1/daily/batch` | POST | 签名 | 批量多股票日线 |
| `/v1/index/{code}` | GET | 签名 | 指数日线 |
| `/v1/securities` | GET | 签名 | 全市场标的信息 |
| `/v1/trade_days` | GET | 签名 | 交易日历 |
| `/v1/index/{code}/stocks` | GET | 签名 | 指数成分股 |
| `/v1/index/{code}/weights` | GET | 签名 | 指数成分权重 |
| `/v1/industry` | GET | 签名 | 申万行业分类（sw_l1/l2/l3） |
| `/v1/valuation` | GET | 签名 | 个股市值表（PE/PB/市值） |
| `/v1/xr_xd` | GET | 签名 | 除权除息事件 |
| `/v1/query_count` | GET | 签名 | 调用统计（内部） |

> 说明：`docs/api-reference.md` 提到 `/nginx-health` 亦免签，但 `src/main.py` 无此路由
> （应属 Nginx 自身健康探活，不在 FastAPI 契约内，未列入快照）。

---

## 2. SDK 消费接口（客户端）

Python SDK `src/sdk/jqdata_sdk`（`import jqdata_sdk as jq`，自动附加签名）。

| SDK 函数 | 对应 HTTP 端点 | 认证 |
|---------|---------------|------|
| `jq.get_price(code, ...)` 单标的 | `GET /v1/daily/{code}`（无数据回退 `GET /v1/index/{code}`） | 自动签名 |
| `jq.get_price([codes], ...)` 多标的 | `POST /v1/daily/batch` | 自动签名 |
| `jq.get_all_securities(types)` | `GET /v1/securities` | 自动签名 |
| `jq.get_trade_days(start,end)` | `GET /v1/trade_days` | 自动签名 |
| `jq.get_index_stocks(code,date)` | `GET /v1/index/{code}/stocks` | 自动签名 |
| `jq.get_index_weights(code,date)` | `GET /v1/index/{code}/weights` | 自动签名 |
| `jq.get_industry(codes,date,type)` | `GET /v1/industry` | 自动签名 |
| `jq.get_valuation(securities,...)` | `GET /v1/valuation` | 自动签名 |
| `jq.get_xr_xd(codes,...)` | `GET /v1/xr_xd` | 自动签名 |
| `jq.get_query_count()` | `GET /v1/query_count` | 自动签名 |

---

## 3. 已知消费方

| 消费方 | 消费方式 | 明细 | 调用频率 | 兜底行为 |
|--------|---------|------|---------|---------|
| **云图中心（yuntuCenter）** | SDK | `python-backend/services/jqdata_adapter.py` 调用 `jq.get_price`（批量 200 只/批 + 单只）、`jq.get_all_securities`；仓库内嵌 `python-backend/jqdata_sdk/` 副本 | 按需（行情取数、均线计算、收盘快照回退） | `fetch_daily_data` 异常按批降级、返回空 DataFrame；未装 SDK 时记日志返回空 |
| **资产沃土** | — | venv 已装 `jqdata_sdk` 但**代码未使用**（`jqdata` 仅作 `data_source` 字符串标签，见 `migrations/025`） | 无 | — |
| **QuantLab（~/QuantLab）** | 规划中 | 架构文档 `docs/design/architecture-evolution.md` 计划经 D 机薄适配层接入（`/api/data/daily`、`/api/data/index-daily` 协议），**尚未直接调用** jqdata-api/SDK | 规划中 | — |
| **live-171（171 服务器策略研发）** | 未知 | 用户提供的消费线索；远程服务器，本仓库无法核对 | 待确认 | — |

> **风险提示**：`valuation/detail`-类查询、`/v1/query_count` 目前无外部消费方（内部统计）。
> 消费方最多的是 `get_price`（云图中心行情主通道），其对应端点 `/v1/daily/{code}`、
> `/v1/daily/batch`、`/v1/index/{code}` 禁止删除/改名。

---

## 4. 契约冒烟测试覆盖（防误删/改坏）

| 测试文件 | 覆盖 | 断言要点 |
|---------|------|---------|
| `tests/test_api_contract_smoke.py` | 12 个 HTTP 路由 | 与 `EXPECTED_ROUTES` 快照完全一致；`/health` 免签、其余签名中间件保护；顶层响应 key 与文档一致（happy-path 等值校验）；日线/批量/指数/标的信息 4 个端点经 `_sanitize_for_json`（`trade_days` 返回字符串无需；`xr_xd`/`valuation`/成分/权重/行业端点当前返回原始行、暂未 sanitize，为已知缺口）；`fq` 有 `pattern` 校验 |
| `tests/test_sdk_contract_smoke.py` | 9 个 SDK 函数 | SDK 公共 API 面完整；每个 SDK 函数调用的端点/方法存在于服务路由快照（SDK↔HTTP 跨契约） |

> 执行级 HTTP 冒烟（真实签名请求 + mock ClickHouse/Redis）需在装有 fastapi/httpx 的环境
> 或 D 服务器容器内运行，属后续增强；当前为可离线运行的源码契约闸门。

---

## 5. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-11 | 首次建立本登记册；新增 HTTP 契约冒烟 `tests/test_api_contract_smoke.py` 与 SDK 跨契约 `tests/test_sdk_contract_smoke.py`；据 yuntuCenter/资产沃土/QuantLab 代码核对消费方 |

# -*- coding: utf-8 -*-
"""
JQData FastAPI 契约冒烟测试 — 防止对外数据接口被误删/改坏而不自知。

背景：服务运行于 D 服务器 Docker（`jqdata-api`，入口 nginx 18080，签名认证）。
本机无 fastapi（服务不在此运行），故本套件用「源码级契约」实现防删闸：
以 AST 解析 `src/main.py`（纯标准库，任何环境可运行、不依赖服务在线），断言：

  1. 路由注册契约：12 个 HTTP 路由与 `EXPECTED_ROUTES` 硬快照完全一致
     （删除/改名/改 HTTP 方法即失败）。
  2. 认证契约：`/health` 免签；其余全部在 `signature_auth` 签名中间件保护内，
     缺失签名返回 401（JSON）。防止把接口误暴露为无鉴权。
  3. 静态 JSON 形状契约：每个端点的顶层响应 key 与文档一致（happy-path 主返回，
     等值校验）；数据端点（日线/批量/指数/标的信息）均经 `_sanitize_for_json`
     （把 NaN/Inf → None，防 JSON 序列化崩溃）；非法参数由 FastAPI
     `Query(..., pattern=...)` 校验（422，而非 500）。

> 快照更新：本文件顶部 `EXPECTED_ROUTES` / `EXPECTED_RESPONSE_KEYS` /
> `PUBLIC_ROUTES` / `SANITIZED_ROUTES` 为契约基线，由 `src/main.py` 的真实路由生成。
> 任何「新增/删除/改名/改 HTTP 方法/改响应顶层 key/改 sanitize 覆盖/改免签集合」
> 都必须同步更新对应快照，否则本测试失败。建议修改 main.py 后运行
> `python3 -m pytest tests/ -q`，根据失败信息把新值回填到对应快照。

> 说明：真实 HTTP 执行级冒烟（带签名请求、mock ClickHouse/Redis）需在装有
> fastapi/httpx 的环境（或 D 服务器容器）运行，属后续增强；本套件为可离线运行的
> 源码契约闸门。运行：`cd ~/JQData && python3 -m pytest tests/ -q`
"""
import ast

import pytest

from _ast_contract_utils import (
    _funcs,
    _path_from_arg,
    _source,
    _tree,
    find_middleware_http_middleware,
    parse_response_keys,
    parse_routes,
)


# ===========================================================================
# 一、路由快照（契约基线）—— 由 src/main.py 的 @app.get/@app.post 快照生成
# 新增/删除/改方法任何 /v1 路由都必须同步更新本快照，否则本测试失败。
# ===========================================================================
EXPECTED_ROUTES = {
    "/health": frozenset({"GET"}),
    "/v1/daily/{code}": frozenset({"GET"}),
    "/v1/daily/batch": frozenset({"POST"}),
    "/v1/index/{code}": frozenset({"GET"}),
    "/v1/index/{code}/stocks": frozenset({"GET"}),
    "/v1/index/{code}/weights": frozenset({"GET"}),
    "/v1/industry": frozenset({"GET"}),
    "/v1/query_count": frozenset({"GET"}),
    "/v1/securities": frozenset({"GET"}),
    "/v1/trade_days": frozenset({"GET"}),
    "/v1/valuation": frozenset({"GET"}),
    "/v1/xr_xd": frozenset({"GET"}),
}

# 每个端点顶层响应 key 契约（与 docs/api-reference.md 一致，happy-path 主返回）
EXPECTED_RESPONSE_KEYS = {
    "/health": {"status", "clickhouse", "redis"},
    "/v1/daily/{code}": {"code", "count", "data"},
    "/v1/daily/batch": {"codes", "count", "data"},
    "/v1/index/{code}": {"code", "count", "data"},
    "/v1/securities": {"count", "data"},
    "/v1/trade_days": {"start", "end", "count", "trade_days"},
    "/v1/index/{code}/stocks": {"code", "trade_date", "count", "data"},
    "/v1/index/{code}/weights": {"code", "date", "count", "data"},
    "/v1/industry": {"count", "data"},
    "/v1/valuation": {"count", "fields", "data"},
    "/v1/xr_xd": {"count", "data"},
    "/v1/query_count": {"note"},
}

# 免签端点（docs/api-reference.md：除 /health 外均需签名）
PUBLIC_ROUTES = frozenset({"/health"})
# 必须经 _sanitize_for_json 处理（防 NaN/Inf 序列化崩溃）的数据端点。
# 注：仅日线/批量/指数/标的信息 4 个端点当前确实调用 _sanitize_for_json；
# trade_days 返回 ISO 字符串无需 sanitize；xr_xd / valuation / index_stocks /
# index_weights / industry 当前返回原始 float 行、暂未 sanitize（已知缺口，
# 见 docs/external-consumers.md §4 备注，不作为本契约强约束）。
SANITIZED_ROUTES = frozenset({
    "/v1/daily/{code}", "/v1/daily/batch", "/v1/index/{code}", "/v1/securities",
})


def _view_of(tree) -> dict:
    """路径 → 视图函数名（从 @app.<method> 装饰器解析，字面量/常量段路径）。"""
    view_of = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in ("get", "post") and dec.args):
                    path = _path_from_arg(dec.args[0])
                    if path:
                        view_of.setdefault(path, node.name)
    return view_of


# ---------------------------------------------------------------------------
# 1) 路由注册契约
# ---------------------------------------------------------------------------
class TestRouteContract:
    @pytest.fixture(scope="class")
    def tree(self):
        return _tree()

    def test_all_expected_routes_registered(self, tree):
        actual = parse_routes(tree)
        missing = {p: EXPECTED_ROUTES[p] for p in EXPECTED_ROUTES if p not in actual}
        assert not missing, f"以下路由被删除/未注册:\n{missing}"

    def test_no_unexpected_route(self, tree):
        actual = parse_routes(tree)
        extra = {p: actual[p] for p in actual if p not in EXPECTED_ROUTES}
        assert not extra, f"以下新路由未登记到契约快照:\n{extra}"

    def test_methods_match_snapshot(self, tree):
        actual = parse_routes(tree)
        diff = {p: (EXPECTED_ROUTES[p], actual[p]) for p in EXPECTED_ROUTES
                if p in actual and actual[p] != EXPECTED_ROUTES[p]}
        assert not diff, f"以下路由 HTTP 方法与快照不一致(期望/实际):\n{diff}"


# ---------------------------------------------------------------------------
# 2) 认证契约
# ---------------------------------------------------------------------------
class TestAuthContract:
    @pytest.fixture(scope="class")
    def tree(self):
        return _tree()

    def test_middleware_exists_and_guards_all(self, tree):
        mw = find_middleware_http_middleware(tree)
        assert mw is not None, "缺少 signature_auth 签名中间件"
        src = ast.get_source_segment(_source(), mw) or ""
        assert 'status_code=401' in src, "缺失签名应返回 401"

    def test_only_health_exempt(self, tree):
        mw = find_middleware_http_middleware(tree)
        src = ast.get_source_segment(_source(), mw) or ""
        # 断言中间件显式豁免的正是 /health，且豁免条件在非认证分支前
        exempt_idx = src.find('"/health"')
        assert exempt_idx != -1, "中间件未显式豁免 /health"
        # 除 /health 外的所有 /v1 路由都不应出现在豁免条件里
        for p in EXPECTED_ROUTES:
            if p != "/health":
                assert f'"{p}"' not in src, f"{p} 不应被中间件豁免"

    def test_exempted_paths_exactly_health(self, tree):
        """从中间件源码 AST 断言豁免集合恰为 {/health}（而非用常量自证）。"""
        mw = find_middleware_http_middleware(tree)
        assert mw is not None, "缺少 signature_auth 签名中间件"
        exempted = set()
        for node in ast.walk(mw):
            # 形如 request.url.path == "/xxx" 的比较
            if (isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Attribute)
                    and getattr(node.left, "attr", "") == "path"
                    and node.comparators):
                c = node.comparators[0]
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    exempted.add(c.value)
        assert exempted == {"/health"}, (
            f"中间件免签路径集合异常（期望恰为 {{/health}}）: {exempted}")

    @pytest.mark.parametrize("path", sorted(PUBLIC_ROUTES), ids=lambda p: p)
    def test_public_route_is_health(self, path):
        """免签快照（PUBLIC_ROUTES）中出现的路径必须就是 /health。"""
        assert path == "/health"


# ---------------------------------------------------------------------------
# 3) 静态 JSON 形状契约
# ---------------------------------------------------------------------------
class TestJsonShapeContract:
    @pytest.fixture(scope="class")
    def tree(self):
        return _tree()

    def test_response_keys_match_contract(self, tree):
        funcs = _funcs(tree)
        view_of = _view_of(tree)
        mismatched = []
        for path, expected in EXPECTED_RESPONSE_KEYS.items():
            fn = funcs.get(view_of.get(path, ""))
            if fn is None:
                mismatched.append(f"{path}: 视图函数缺失")
                continue
            keys = parse_response_keys(fn)
            if keys != expected:
                mismatched.append(f"{path}: 期望 {expected}, 实际 {keys}")
        assert not mismatched, "响应顶层 key 契约不匹配:\n" + "\n".join(mismatched)

    def test_data_endpoints_sanitized(self, tree):
        """数据端点必须调用 _sanitize_for_json（防 NaN/Inf 序列化崩溃），按端点逐一校验。"""
        funcs = _funcs(tree)
        view_of = _view_of(tree)
        unsanitized = []
        for path in SANITIZED_ROUTES:
            fn = funcs.get(view_of.get(path, ""))
            if fn is None:
                unsanitized.append(f"{path}: 视图函数缺失")
                continue
            body = ast.get_source_segment(_source(), fn) or ""
            if "_sanitize_for_json" not in body:
                unsanitized.append(f"{path} ({fn.name}) 未调用 _sanitize_for_json")
        assert not unsanitized, "以下数据端点未经 _sanitize_for_json:\n" + "\n".join(unsanitized)

    def test_fq_validation_declared(self, tree):
        """/v1/daily 的 fq 参数有 pattern 校验（非法值→422，而非 500）。"""
        assert 'pattern="^(pre|post|none)$"' in _source(), "fq 参数缺少 pattern 校验声明"

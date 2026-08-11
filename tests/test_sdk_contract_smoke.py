# -*- coding: utf-8 -*-
"""
JQData SDK 契约冒烟测试 — 防止 SDK 与 HTTP 服务契约脱节（调用的端点被删/改名而不自知）。

纯源码级契约（AST 解析，不 import，避免依赖 pandas/服务在线）：
  1. SDK 公共 API 面（__all__）与预期一致。
  2. SDK 各函数调用的 HTTP 端点必须存在于服务路由快照（cross-contract），
     HTTP 方法一致（get_price 多标的走 POST /v1/daily/batch，其余 GET）。

> 快照更新：`EXPECTED_PUBLIC_API` / `SDK_ENDPOINTS` 为契约基线。SDK 新增/删除公共
> 接口、或某函数改调端点/方法时，须同步更新对应快照，否则本测试失败。建议改后运行
> `python3 -m pytest tests/ -q` 按失败信息回填。

运行：`cd ~/JQData && python3 -m pytest tests/ -q`
"""
import ast

from _ast_contract_utils import (
    _normalize_path,
    _path_from_arg,
    _tree,
    API_PY,
    INIT_PY,
    parse_routes,
)

# SDK 公共 API（与 __init__.__all__ 对应）
EXPECTED_PUBLIC_API = {
    "get_price", "get_all_securities", "get_trade_days", "get_index_stocks",
    "get_index_weights", "get_industry", "get_valuation", "get_xr_xd",
    "normalize_code", "get_query_count",
}

# 每个 SDK 函数期望访问的 HTTP 端点（方法；get_price 支持单标的 GET / 多标的 POST）
SDK_ENDPOINTS = {
    "get_price": {("/v1/daily/{code}", "GET"), ("/v1/index/{code}", "GET"),
                  ("/v1/daily/batch", "POST")},
    "get_all_securities": {("/v1/securities", "GET")},
    "get_trade_days": {("/v1/trade_days", "GET")},
    "get_index_stocks": {("/v1/index/{code}/stocks", "GET")},
    "get_index_weights": {("/v1/index/{code}/weights", "GET")},
    "get_industry": {("/v1/industry", "GET")},
    "get_valuation": {("/v1/valuation", "GET")},
    "get_xr_xd": {("/v1/xr_xd", "GET")},
    "get_query_count": {("/v1/query_count", "GET")},
}


def _parse_sdk_endpoints(tree) -> dict:
    """解析 api.py 每个函数内调用 client.get/post 的路径（模板）与方法。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr in ("get", "post") and sub.args:
                    path = _path_from_arg(sub.args[0])
                    if path and path.startswith("/v1/"):
                        found.add((_normalize_path(path), sub.func.attr.upper()))
            if found:
                out[node.name] = found
    return out


def _init_all(tree) -> set:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, ast.List):
                return {e.value for e in node.value.elts
                        if isinstance(e, ast.Constant)}
    return set()


def test_public_api_surface():
    tree = ast.parse(open(INIT_PY, encoding="utf-8").read())
    all_ = _init_all(tree)
    assert all_ >= EXPECTED_PUBLIC_API, f"SDK 缺失公共接口: {EXPECTED_PUBLIC_API - all_}"


def test_sdk_endpoints_exist_on_server():
    """SDK 每个函数调用的端点必须存在于服务路由快照（防删/防改）。"""
    sdk_tree = ast.parse(open(API_PY, encoding="utf-8").read())
    sdk_map = _parse_sdk_endpoints(sdk_tree)
    server_routes = parse_routes(_tree())
    # 服务端路由也归一化（/v1/daily/{code} → /v1/daily/{}）
    server_routes_norm = {
        _normalize_path(p): {m.upper() for m in ms} for p, ms in server_routes.items()
    }
    for fn, endpoints in SDK_ENDPOINTS.items():
        expected = {(_normalize_path(p), m) for p, m in endpoints}
        actual = sdk_map.get(fn, set())
        assert expected == actual, (
            f"SDK {fn} 端点与预期不符:\n  期望 {expected}\n  实际 {actual}")
        for path, method in expected:
            assert path in server_routes_norm, f"SDK {fn} 调用 {path} 但服务端无此路由"
            assert method in server_routes_norm[path], \
                f"SDK {fn} 调用 {method} {path} 但服务端方法是 {server_routes_norm[path]}"

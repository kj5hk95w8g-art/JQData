# -*- coding: utf-8 -*-
"""契约冒烟测试共享的纯标准库 AST 解析工具。

不 import fastapi/pandas/服务，任何环境（含无 fastapi 的本机/CI）均可离线运行。
所有路径均由 __file__ 推导，无硬编码绝对路径。

注：文件名以 `_` 开头，避免被 pytest 当作测试模块收集。
"""
import ast
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PY = os.path.join(REPO_ROOT, "src", "main.py")
SDK_DIR = os.path.join(REPO_ROOT, "src", "sdk", "jqdata_sdk")
API_PY = os.path.join(SDK_DIR, "api.py")
INIT_PY = os.path.join(SDK_DIR, "__init__.py")

# FastAPI 路由方法白名单（@app.get/@app.post/@app.put/@app.delete）
HTTP_METHOD_ATTRS = ("get", "post", "put", "delete")


def _tree() -> ast.Module:
    with open(MAIN_PY, encoding="utf-8") as f:
        return ast.parse(f.read())


def _source() -> str:
    """main.py 源码文本（供 ast.get_source_segment 使用）。"""
    with open(MAIN_PY, encoding="utf-8") as f:
        return f.read()


def _funcs(tree):
    """顶层函数名 → FunctionDef 节点。"""
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _path_from_arg(arg):
    """把装饰器/调用参数还原为路径模板。

    Constant 取原值；f-string 的非占位常量段取原文、占位段归一化为 {}。
    返回 None 表示无法解析（非常量段、非 f-string）。
    """
    if isinstance(arg, ast.Constant):
        return str(arg.value)
    if isinstance(arg, ast.JoinedStr):
        out = []
        for v in arg.values:
            if isinstance(v, ast.Constant):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                out.append("{}")
        return "".join(out)
    return None


def _normalize_path(p: str) -> str:
    """把 {code} 等路径占位符归一化为 {}，便于 SDK f-string 模板与服务路由模板比对。"""
    return re.sub(r"\{[^}]*\}", "{}", p)


def parse_routes(tree) -> dict:
    """返回 {path: set(methods)}，由 @app.<method>('...') 装饰器解析。

    支持字面量与 f-string 常量段路径。注意：f-string 占位路由会归一化为 {}
    （如 f"/v1/x/{code}" → "/v1/x/{}"），快照中对应键需写成 {}；字面量路由保持
    原样（如 /v1/x/{code}）。用变量/其它表达式定义路由的会被跳过（返回 None）。
    """
    routes = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in HTTP_METHOD_ATTRS and dec.args):
                path = _path_from_arg(dec.args[0])
                if path:
                    routes.setdefault(path, set()).add(dec.func.attr.upper())
    return routes


def parse_response_keys(func: ast.FunctionDef) -> set:
    """取函数"主返回"（happy-path）的顶层 key：跳过含 error key 的错误分支。

    优先返回第一个不含 error key 的 inline dict return（即正常返回分支）；
    若全部含 error key 或没有 dict return，退回第一个候选/空集。
    """
    candidates = []
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = set()
            for k in node.value.keys:
                if isinstance(k, ast.Constant):
                    keys.add(k.value)
            if keys:
                candidates.append(keys)
    for ks in candidates:
        if "error" not in ks:
            return ks
    return candidates[0] if candidates else set()


def find_middleware_http_middleware(tree):
    """定位 signature_auth 中间件函数节点（顶层 def / async def）。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "signature_auth":
            return node
    return None

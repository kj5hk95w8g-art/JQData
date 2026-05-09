"""HTTP 客户端 —— 处理请求签名、请求、重试"""
import hashlib
import time
from typing import Optional
import requests
from .exceptions import APIError


# ── 内置配置（硬编码）──
DEFAULT_BASE_URL = "http://172.24.52.237:8000"
SECRET_SALT = "yuntu-jqdata-2026-internal-only"


def _sign_headers():
    """计算请求签名，每次调用生成不同签名"""
    timestamp = str(int(time.time()))
    signature = hashlib.md5(f"{SECRET_SALT}{timestamp}".encode()).hexdigest()
    return {
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


class HTTPClient:
    """底层 HTTP 客户端"""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        self.timeout = 60
        self.max_retries = 3

    def request(self, method: str, path: str, **kwargs) -> dict:
        """发送 HTTP 请求，自动附加签名，自动重试"""
        url = f"{self.base_url}{path}"

        # 自动附加签名头
        headers = kwargs.pop("headers", {})
        headers.update(_sign_headers())

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method, url, headers=headers, timeout=self.timeout, **kwargs
                )
                if resp.status_code == 401:
                    raise APIError(f"认证失败: {resp.text}", status_code=401)
                if resp.status_code == 429:
                    raise APIError("请求频率过高，请稍后再试", status_code=429)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                continue

        raise APIError(f"请求失败（重试{self.max_retries}次）: {last_error}")

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Optional[dict] = None) -> dict:
        return self.request("POST", path, json=json)

"""HTTP 客户端 —— 处理认证、请求、重试"""
import time
from typing import Optional
import requests
from .exceptions import APIError, AuthError


class HTTPClient:
    """底层 HTTP 客户端"""

    def __init__(self, base_url: str = "http://101.132.161.52:8000", api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        })
        self.timeout = 60
        self.max_retries = 3

    def request(self, method: str, path: str, **kwargs) -> dict:
        """发送 HTTP 请求，自动重试"""
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
                if resp.status_code == 401:
                    raise AuthError(f"认证失败: {resp.text}")
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

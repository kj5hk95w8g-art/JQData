"""JQData SDK 单元测试"""
import unittest
from unittest.mock import Mock, patch
import pandas as pd
from jqdata_sdk import auth, get_price, get_all_securities, get_trade_days
from jqdata_sdk.exceptions import JQDataError, AuthError


class TestAuth(unittest.TestCase):
    def test_auth_without_calling_auth_raises(self):
        """未认证时调用接口应抛异常"""
        # 重置内部状态（模拟未认证）
        import jqdata_sdk.api as api_module
        api_module._client = None
        with self.assertRaises(JQDataError):
            get_price("000001.XSHE", "2020-01-01", "2020-01-10")


class TestGetPrice(unittest.TestCase):
    @patch("jqdata_sdk.client.HTTPClient.get")
    @patch("jqdata_sdk.client.HTTPClient.request")
    def test_single_stock(self, mock_request, mock_get):
        """单股票查询返回 DataFrame"""
        mock_get.return_value = {
            "code": "000001.XSHE",
            "count": 2,
            "data": [
                ["2020-01-02", 10.0, 11.0, 9.5, 10.5, 1000000, 10500000],
                ["2020-01-03", 10.5, 11.5, 10.0, 11.0, 1200000, 13200000],
            ],
        }
        auth("test-key", base_url="http://test")
        df = get_price("000001.XSHE", "2020-01-01", "2020-01-10")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 2)
        self.assertIn("open", df.columns)


class TestGetAllSecurities(unittest.TestCase):
    @patch("jqdata_sdk.client.HTTPClient.get")
    def test_filter_by_type(self, mock_get):
        mock_get.return_value = {
            "count": 1,
            "data": [["000001.XSHE", "平安银行", "PAYH", "stock", "XSHE", "1991-04-03", "2200-01-01"]],
        }
        auth("test-key", base_url="http://test")
        df = get_all_securities(types=["stock"])
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["code"], "000001.XSHE")


class TestGetTradeDays(unittest.TestCase):
    @patch("jqdata_sdk.client.HTTPClient.get")
    def test_returns_datetime_index(self, mock_get):
        mock_get.return_value = {
            "start": "2020-01-01",
            "end": "2020-01-10",
            "count": 5,
            "trade_days": ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"],
        }
        auth("test-key", base_url="http://test")
        idx = get_trade_days("2020-01-01", "2020-01-10")
        self.assertIsInstance(idx, pd.DatetimeIndex)
        self.assertEqual(len(idx), 5)


if __name__ == "__main__":
    unittest.main()

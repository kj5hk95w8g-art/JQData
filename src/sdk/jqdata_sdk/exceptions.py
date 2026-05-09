"""JQData SDK 自定义异常"""


class JQDataError(Exception):
    """基础异常"""
    pass


class AuthError(JQDataError):
    """认证失败"""
    pass


class APIError(JQDataError):
    """API 调用失败"""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class QuotaExceededError(JQDataError):
    """额度超限"""
    pass

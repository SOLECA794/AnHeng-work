"""业务异常。

承载 SDD-03 §3 的错误码；服务层抛出，由 API 层转换为统一响应结构（`src/core/response.py`）。
"""

from src.core.errors import ErrorCode


class ServiceError(Exception):
    """业务异常。

    :param code: SDD-03 §3 定义的错误码
    :param message: 面向调用方的可读说明（不得包含内部实现细节或敏感数据）
    """

    def __init__(self, code: ErrorCode | int, message: str) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

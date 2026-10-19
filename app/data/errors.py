"""数据层：扶摇错误码 → 领域异常映射。

依据：docs/DATASOURCES.md §1.2 与 03_BUILD_PLAN §3 M0。
关键口径（D5）：扶摇业务错误 **HTTP 状态码恒为 200**，必须靠响应体里的 `code` 判错。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "FuyaoError",
    "RateLimited",
    "NotReady",
    "SourceTimeout",
    "SourceDown",
    "NoPermission",
    "MockDataMissing",
    "NotEnabled",
    "InvalidThscode",
    "error_from_code",
]


class FuyaoError(Exception):
    """扶摇业务错误基类；`code` 为扶摇错误码，`request_id` 便于对账。"""

    code: int = -1

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.request_id = request_id
        self.payload = payload or {}

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"[{self.code}] {self.message}"


class RateLimited(FuyaoError):
    """`4001` 触发限流（等价 HTTP 429）：应降并发、退避重试。"""

    code = 4001


class NotReady(FuyaoError):
    """`3002` 数据未就绪。"""

    code = 3002


class SourceTimeout(FuyaoError):
    """`5002` 数据源超时。"""

    code = 5002


class SourceDown(FuyaoError):
    """`5003` 数据源不可用。"""

    code = 5003


class NoPermission(FuyaoError):
    """`2003` API Key 无权访问该 capability。"""

    code = 2003


class MockDataMissing(FuyaoError):
    """离线 Mock 数据集缺少该接口的样本（Mock 模式的 `3002` 等价物）。"""

    code = 3002


class NotEnabled(FuyaoError):
    """备源（iFinD MCP）未启用 / 未配置工具映射（03_BUILD_PLAN §0 表 L28、§3 M2）。

    注意：本异常**不对应扶摇错误码**（扶摇错误码见 DATASOURCES §1.2），`code = -2` 是本地约定，
    故**不登记进 `CODE_TO_ERROR`**。语义：备源不可用时由降级阶梯第 ⑥ 步转交第 ⑦ 步，
    最终以 `degradation_notice` 在 UI 显式标注（架构 §10.3 / §11.2）。
    """

    code = -2


class InvalidThscode(ValueError):
    """标的不符合完整 `thscode` 口径（D1：`600519.SH`，不可传纯 ticker）。"""


#: 扶摇错误码 → 领域异常（仅覆盖 M0 契约要求的 5 个；其余走通用 `FuyaoError`）。
CODE_TO_ERROR: dict[int, type[FuyaoError]] = {
    cls.code: cls for cls in (RateLimited, NoPermission, NotReady, SourceTimeout, SourceDown)
}


def error_from_code(
    code: int,
    message: str,
    *,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> FuyaoError:
    """按 `code` 构造对应领域异常（未登记的错误码返回基类）。"""
    cls = CODE_TO_ERROR.get(int(code), FuyaoError)
    return cls(message, code=int(code), request_id=request_id, payload=payload)

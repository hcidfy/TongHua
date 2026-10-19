"""数据层：扶摇（同花顺金融数据 API）真实客户端。

依据：docs/DATASOURCES.md §1.2/§1.3 与 03_BUILD_PLAN §3 M0 接口契约：
    FuyaoClient.get(path, params) -> ApiData      # 已剥信封；业务错误抛领域异常
    FuyaoClient.raw(path, params) -> RawResponse  # 保留原始响应，供 Evidence.raw_ref

口径约束（HANDOFF §8.2）：
- D1：标的一律完整 `thscode`（`600519.SH`），不接受纯 ticker；单标的接口不接受逗号。
- D2：时间戳毫秒级；D5：业务错误 HTTP 恒 200，靠 `code` 判错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings
from app.domain.models import now_ms

from .errors import FuyaoError, InvalidThscode, error_from_code
from .quality import SOURCE_FUYAO

__all__ = ["RawResponse", "ApiData", "FuyaoClient", "validate_thscode", "ensure_single_thscode"]

#: 完整 thscode：6 位代码 + `.` + 2~4 位市场后缀（SH/SZ/BJ/TI/...）
_THSCODE_RE = re.compile(r"^[0-9A-Za-z]{5,7}\.[A-Za-z]{2,4}$")


def validate_thscode(value: str) -> str:
    """校验并返回完整 `thscode`；不合法抛 `InvalidThscode`（D1）。"""
    candidate = (value or "").strip()
    if "." not in candidate or not _THSCODE_RE.match(candidate):
        raise InvalidThscode(
            f"非法 thscode: {value!r}；须为完整代码（如 '600519.SH'），不接受纯 ticker（D1）"
        )
    return candidate


def ensure_single_thscode(value: str) -> str:
    """单标的接口入参校验：不接受逗号分隔（D1）。"""
    if "," in value:
        raise InvalidThscode(f"单标的接口不接受逗号：{value!r}")
    return validate_thscode(value)


@dataclass(slots=True)
class RawResponse:
    """原始响应留档：供 `Evidence.raw_ref` 反查（不落密钥）。"""

    url: str
    status_code: int
    payload: dict[str, Any]
    fetched_at: int

    @property
    def request_id(self) -> str | None:
        value = self.payload.get("request_id")
        return str(value) if value is not None else None

    @property
    def code(self) -> int | None:
        value = self.payload.get("code")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def ref(self) -> str:
        """稳定的证据引用串（不含 Key）。"""
        return f"{self.url}#status={self.status_code}&rid={self.request_id}&at={self.fetched_at}"


@dataclass(slots=True)
class ApiData:
    """已剥信封的响应体。"""

    code: int
    message: str
    request_id: str | None
    data: Any
    timestamp: int | None
    raw_ref: str
    fetched_at: int
    raw: RawResponse = field(repr=False)

    @property
    def item(self) -> list[Any]:
        """`data.item`（扶摇列表类接口的统一字段名）；缺失时返回空列表。"""
        if isinstance(self.data, dict):
            items = self.data.get("item")
            if isinstance(items, list):
                return items
        return []

    @property
    def total(self) -> int:
        if isinstance(self.data, dict):
            total = self.data.get("total")
            if isinstance(total, (int, float)):
                return int(total)
        return len(self.item)


class FuyaoClient:
    """扶摇 HTTP 客户端（`X-api-key` 注入 + 信封解析 + 错误码映射）。"""

    #: 证据来源标识（M2：`tools/impl` 据此填 `Evidence.source`）；`MockClient` 覆盖为 `mock`。
    source: str = SOURCE_FUYAO

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        timeout_s = self.settings.fuyao_timeout_ms / 1000
        self._client = http_client or httpx.Client(
            base_url=self.settings.fuyao_base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=True,
        )
        self._owns_client = http_client is None

    # --- 生命周期 ---
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FuyaoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 请求 ---
    def _headers(self) -> dict[str, str]:
        key = self.settings.fuyao_api_key
        if not key:
            raise FuyaoError("缺少 FUYAO_API_KEY：请改用 MockClient 或配置密钥（S1）")
        return {"X-api-key": key, "Accept": "application/json"}

    def raw(self, path: str, params: dict[str, Any] | None = None) -> RawResponse:
        """发起请求并返回原始响应（不做 code 判错，供留档）。"""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = path if path.startswith("http") else f"/{path.lstrip('/')}"
        response = self._client.get(url, params=query, headers=self._headers())
        try:
            payload = response.json()
        except ValueError:  # 非 JSON 响应
            payload = {"code": -1, "message": response.text[:500], "data": None}
        if not isinstance(payload, dict):
            payload = {"code": -1, "message": "非预期响应结构", "data": payload}
        fetched_at = now_ms()
        shown_url = f"{self.settings.fuyao_base_url.rstrip('/')}{url}"
        if query:
            shown_url = f"{shown_url}?{urlencode(query)}"
        return RawResponse(
            url=shown_url, status_code=response.status_code, payload=payload, fetched_at=fetched_at
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> ApiData:
        """发起请求并剥信封；`code != 0` 时按错误码抛领域异常（D5）。"""
        raw = self.raw(path, params)
        code = raw.code
        message = str(raw.payload.get("message") or "")
        if code is None:
            raise FuyaoError(
                f"响应缺少 code 字段（HTTP {raw.status_code}）：{message}",
                request_id=raw.request_id,
                payload=raw.payload,
            )
        if code != 0:
            # 业务错误 HTTP 恒为 200 —— 只能靠 code 判错
            raise error_from_code(code, message, request_id=raw.request_id, payload=raw.payload)
        data = raw.payload.get("data")
        timestamp = None
        if isinstance(data, dict) and isinstance(data.get("timestamp"), (int, float)):
            timestamp = int(data["timestamp"])
        return ApiData(
            code=code,
            message=message or "success",
            request_id=raw.request_id,
            data=data,
            timestamp=timestamp,
            raw_ref=raw.ref,
            fetched_at=raw.fetched_at,
            raw=raw,
        )

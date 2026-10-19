"""数据层：扶摇主源客户端、iFinD 备源客户端、离线 Mock、错误映射、质量元数据与三件协作件。

模块与 03_BUILD_PLAN §3 M2 的对应：

| 模块 | M2 交付 |
|---|---|
| `errors.py` | 扶摇错误码 → 领域异常（+ 备源不可用的 `NotEnabled`） |
| `fuyao_client.py` | 主源客户端（M0） |
| `mock_client.py` | 离线 Mock（M0，断网可演示） |
| `quality.py` | 质量元数据（`as_of` / `staleness_ms` / `caliber` / `confidence`）+ `degradation_notice` |
| `ratelimit.py` | 令牌桶（阶梯第 ③ 步） |
| `cache.py` | TTL 缓存（第 ④ 步；降级可返回「缓存 + 过期标注」） |
| `breaker.py` | 熔断器（第 ⑤ 步） |
| `ifind_client.py` | iFinD MCP 客户端骨架（第 ⑥ 步备源；不可用 → `NotEnabled`） |
"""

from .breaker import STATE_CLOSED, STATE_OPEN, Breaker
from .cache import TTLCache
from .errors import (
    FuyaoError,
    InvalidThscode,
    MockDataMissing,
    NoPermission,
    NotEnabled,
    NotReady,
    RateLimited,
    SourceDown,
    SourceTimeout,
    error_from_code,
)
from .fuyao_client import ApiData, FuyaoClient, RawResponse, ensure_single_thscode, validate_thscode
from .ifind_client import MCP_PROTOCOL_VERSION, IfindMCPClient, build_ifind_client
from .mock_client import MOCK_DIR, MOCK_PATH_MAP, MockClient, build_client
from .quality import (
    DEFAULT_CONFIDENCE,
    IFIND_CONFIDENCE,
    MOCK_CONFIDENCE,
    SOURCE_FUYAO,
    SOURCE_IFIND,
    SOURCE_INTERNAL,
    SOURCE_MOCK,
    compute_staleness_ms,
    degradation_notice,
    quality_fields,
)
from .ratelimit import RateLimiter

__all__ = [
    # 错误
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
    # 客户端
    "RawResponse",
    "ApiData",
    "FuyaoClient",
    "validate_thscode",
    "ensure_single_thscode",
    "MockClient",
    "build_client",
    "MOCK_DIR",
    "MOCK_PATH_MAP",
    "IfindMCPClient",
    "build_ifind_client",
    "MCP_PROTOCOL_VERSION",
    # 质量元数据
    "SOURCE_FUYAO",
    "SOURCE_MOCK",
    "SOURCE_IFIND",
    "SOURCE_INTERNAL",
    "DEFAULT_CONFIDENCE",
    "MOCK_CONFIDENCE",
    "IFIND_CONFIDENCE",
    "compute_staleness_ms",
    "quality_fields",
    "degradation_notice",
    # 协作件（阶梯第 ③④⑤ 步）
    "RateLimiter",
    "TTLCache",
    "Breaker",
    "STATE_CLOSED",
    "STATE_OPEN",
]

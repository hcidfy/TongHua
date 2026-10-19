"""工具层：声明式工具蓝图（03_BUILD_PLAN §3.1 M2 模板的结构化落地）。

`ToolSpec` 契约（HANDOFF §6.1）**没有 `backing` 字段**——「工具怎么执行」不写在 spec 里。
本模块用一个 `ToolBlueprint` 同时承载 §3.1 模板的每一项：

    tool / fuyao / args_mapping / keep_fields / evidence{unit,caliber,source} / fallback / timeout_ms / retry

- `tools/specs/` 由蓝图派生 `ToolSpec`（**给 LLM 看的对外契约**）；
- `tools/impl/` 由蓝图派生执行函数（**真正取数 + 裁剪 + 质量元数据**）。

两者同源，避免「spec 声明与 impl 行为漂移」。

**关于 `retry`**：8 个工具的模板值一致（`times: 2, backoff: exponential`，见 §3.1），
因此不逐工具配置，而由执行器统一实现（`ToolExecutor(max_attempts=3)` = 首次 + 2 次重试，
退避 `base_backoff_s * 2**(n-1)`）；`RETRY_POLICY` 常量供测试与文档引用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.data.errors import FuyaoError
from app.data.fuyao_client import ensure_single_thscode, validate_thscode
from app.data.quality import DEFAULT_CONFIDENCE
from app.domain import PermissionLevel, Phase, ToolSpec

__all__ = [
    "ToolBlueprint",
    "ArgsMapper",
    "RETRY_POLICY",
    "join_thscodes",
    "single_thscode",
    "optional_int",
]

#: §3.1 模板的 retry 值（由 `harness/tool_executor.py` 统一实现，见模块 docstring）。
RETRY_POLICY: dict[str, Any] = {"times": 2, "backoff": "exponential"}

#: 业务参数 → 扶摇查询参数的映射函数。
ArgsMapper = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolBlueprint:
    """一个受控数据工具的**唯一声明**（spec 与 impl 的共同来源）。"""

    name: str
    description: str
    domain: str
    #: 扶摇接口路径；可传函数以按入参派生（如行情快照 → 个股 / 指数两个端点）。
    path: str | Callable[[dict[str, Any]], str]
    #: 业务参数 → 扶摇查询参数（**业务口径与扶摇口径的解耦点**）。
    args_mapping: ArgsMapper
    #: 给 LLM 的 JSON Schema。
    schema_json: dict[str, Any]
    #: 响应裁剪白名单（`None` = 不过滤）；裁剪只保留记录里**实际存在**的字段。
    keep_fields: tuple[str, ...] | None = None
    #: 从 `data` 中抽取记录列表的字段名（扶摇多数接口为 `item`，资讯类为 `items`）。
    list_key: str = "item"
    #: 证据口径（C3）：`unit` / `caliber` 显式声明；上游缺失时透传 None（D4）。
    unit: str | None = None
    caliber: str = ""
    confidence: float = DEFAULT_CONFIDENCE
    phase: Phase = Phase.EXPLORE
    timeout_ms: int = 8000
    idempotent: bool = True
    #: 是否挂 iFinD 备源（§3.1 `fallback: ifind`；内部工具为 False）。
    use_fallback: bool = True
    #: iFinD 侧工具名映射；未实测确认前保持 `None` → 备源按 §3.1「无 schema 时直接降级」抛 `NotEnabled`。
    ifind_tool: str | None = None

    def path_for(self, args: dict[str, Any] | None = None) -> str:
        """该次调用实际命中的扶摇路径（写进 `Evidence.interface`）。"""
        if callable(self.path):
            return self.path(dict(args or {}))
        return self.path

    def to_spec(self) -> ToolSpec:
        """派生 `ToolSpec`（**不改契约字段**，仅填充取值）。"""
        return ToolSpec(
            name=self.name,
            description=self.description,
            schema_json=dict(self.schema_json),
            domain=self.domain,
            phase=self.phase,
            idempotent=self.idempotent,
            timeout_ms=self.timeout_ms,
            permission_level=PermissionLevel.READ,
            enabled=True,
        )


# --------------------------------------------------------------------------- #
# 参数映射助手（业务口径 → 扶摇口径；D1 口径在此统一把关）
# --------------------------------------------------------------------------- #
def join_thscodes(value: Any) -> str:
    """业务参数 → 扶摇 `thscodes`（**逗号分隔**，多标的接口）。

    接受数组或已逗号分隔的字符串；逐个用 `validate_thscode` 把关（D1：完整 `thscode`，
    如 `600519.SH`，不接受纯 ticker）。
    """
    if isinstance(value, str):
        codes = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
    elif isinstance(value, (list, tuple, set)):
        codes = [str(chunk).strip() for chunk in value if str(chunk).strip()]
    else:
        raise FuyaoError(f"thscodes 需为字符串或数组，实际为 {type(value).__name__}")
    if not codes:
        raise FuyaoError("thscodes 不能为空")
    for code in codes:
        validate_thscode(code)
    return ",".join(codes)


def single_thscode(value: Any, *, field: str = "thscode") -> str:
    """业务参数 → 扶摇单标的 `thscode`（**不接受逗号**，D1）。"""
    if not isinstance(value, str) or not value.strip():
        raise FuyaoError(f"{field} 必填且须为字符串")
    return ensure_single_thscode(value.strip().upper())


def optional_int(
    value: Any,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """可选整数参数：缺省返回 None；超界自动夹取到 `[minimum, maximum]`。"""
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise FuyaoError(f"{field} 需为整数，实际为 {value!r}") from exc
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number

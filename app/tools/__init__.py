"""工具层（03_BUILD_PLAN §3 M2）：10 个工具 = 9 个受控数据工具 + 1 个内部 COMMIT 工具。

装配入口 `build_tools(registry)`：

1. 注册 10 个 `ToolSpec`（由 `ToolBlueprint.to_spec()` 派生，**不改契约字段**）；
2. 绑定实现：9 个数据工具绑「扶摇 / Mock 取数」+「iFinD 备源」，`emit_artifact` 绑内部实现。

分层：`specs/` 只声明（给 LLM 看的对外契约），`impl/` 只执行（真取数 + 裁剪 + 质量元数据），
两者同源于 `blueprint.py`，避免「spec 声明与 impl 行为漂移」。
"""

from __future__ import annotations

from typing import Any

from app.data import build_client, build_ifind_client
from app.harness.tool_registry import ToolRegistry

from .blueprint import RETRY_POLICY, ToolBlueprint, join_thscodes, optional_int, single_thscode
from .impl import make_artifact_impl, make_data_impl, make_ifind_fallback
from .specs import DATA_BLUEPRINTS, DATA_TOOL_NAMES, INTERNAL_BLUEPRINTS, INTERNAL_TOOL_NAME

__all__ = [
    # 蓝图与助手
    "ToolBlueprint",
    "RETRY_POLICY",
    "join_thscodes",
    "single_thscode",
    "optional_int",
    # 声明表
    "DATA_BLUEPRINTS",
    "INTERNAL_BLUEPRINTS",
    "DATA_TOOL_NAMES",
    "INTERNAL_TOOL_NAME",
    # 装配
    "build_tools",
]


def build_tools(
    registry: ToolRegistry,
    *,
    client: Any = None,
    ifind: Any = None,
    register: bool = True,
) -> ToolRegistry:
    """把 9 个工具装配到注册表：注册 `ToolSpec` + 绑定 `impl` / 备源。

    - `client`：主源客户端（缺省按 `MOCK_DATA_MODE` 取 `MockClient` 或 `FuyaoClient`）；
    - `ifind`：备源客户端（缺省构造；未配置时其调用抛 `NotEnabled`，由阶梯第 ⑦ 步降级）；
    - `register=False`：只绑定实现，不动已有声明（供恢复场景从 DB 回填 spec）。
    """
    fuyao = client if client is not None else build_client()
    ifind_client = ifind if ifind is not None else build_ifind_client()

    if register:
        registry.register_many(bp.to_spec() for bp in DATA_BLUEPRINTS + INTERNAL_BLUEPRINTS)

    for bp in DATA_BLUEPRINTS:
        registry.bind(
            bp.name,
            make_data_impl(bp, fuyao),
            fallback=make_ifind_fallback(bp, ifind_client) if bp.use_fallback else None,
        )
    for bp in INTERNAL_BLUEPRINTS:
        registry.bind(bp.name, make_artifact_impl(bp))

    return registry

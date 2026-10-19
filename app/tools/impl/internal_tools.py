"""工具层：1 个内部 COMMIT 工具（`emit_artifact`）的执行体。

§10 契约数字：**内部工具 = 1**，且是唯一 `phase = COMMIT` 的注册项（架构 §9.2）。它**不触外网**，
只把「事实 / 推断 / 不确定」三层分离的结构化结论原样交回（C1：不含操作建议；C2：三层分离）；
产物落库归 M5 的 `Artifact`。
"""

from __future__ import annotations

from typing import Any

from app.data import SOURCE_INTERNAL, quality_fields
from app.domain import new_id, now_ms
from app.harness.tool_executor import ToolResult

from ..blueprint import ToolBlueprint

__all__ = ["make_artifact_impl"]


def make_artifact_impl(bp: ToolBlueprint) -> Any:
    """把蓝图编译成一个内部产出实现（供 `ToolRegistry.bind`）。"""

    def impl(args: dict[str, Any]) -> ToolResult:
        payload = bp.args_mapping(dict(args or {}))
        return ToolResult(
            tool_name=bp.name,
            data=payload,
            **quality_fields(
                source=SOURCE_INTERNAL,
                interface=bp.path_for(payload),
                as_of=now_ms(),
                unit=bp.unit,
                caliber=bp.caliber,
                raw_ref=f"artifact://{new_id()}",
                confidence=bp.confidence,
            ),
        )

    return impl

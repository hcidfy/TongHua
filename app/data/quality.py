"""数据层：质量元数据（架构 C3；03_BUILD_PLAN §3 M2）。

职责（与 `harness/tool_executor.py` 第 ⑧ 步**同一口径**，工具侧提前注入、执行器兜底补齐）：

- `as_of`：**取响应 `data.timestamp`**（毫秒级 Unix，D2）；缺失时由执行器回落抓取时刻；
- `staleness_ms`：`now - as_of`（不小于 0）；
- `caliber`：口径（如「快照价」「日线收盘价（前复权）」）——C3 强制字段；
- `unit`：单位（如 `CNY`、`倍`、`只`）；上游缺失时**透传 None，不补零**（D4）；
- `confidence`：置信度（真实源 / 离线样本 / 备源文本三级）。

另提供 `degradation_notice()`：把降级结果渲染为架构 §11.2 白名单里的 UI 组件
`degradation_notice`（必填 props `{scope, reason, impact}`），对应 SSE `degrade` 事件
（架构 §13.1）。M5 的 UIRenderer 可直接把它包成 `UIBlock`。
"""

from __future__ import annotations

from typing import Any

from app.domain.models import now_ms

__all__ = [
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
]

#: 主源标识（§3.1 模板 `evidence.source`）。
SOURCE_FUYAO = "fuyao"
#: 离线 Mock 样本 —— **不与主源混同**（C4：不得静默；来源时点单位口径必须可辨）。
SOURCE_MOCK = "mock"
#: 降级备源（iFinD MCP，返回自然语言文本、无字段级 schema）。
SOURCE_IFIND = "ifind"
#: 内部工具（`emit_artifact`），非外部数据源。
SOURCE_INTERNAL = "internal"

#: 真实通道置信度。
DEFAULT_CONFIDENCE = 0.9
#: 离线样本置信度：结构与单位同构，但非实时。
MOCK_CONFIDENCE = 0.6
#: 备源自然语言文本置信度：无字段级 schema，只能作参考。
IFIND_CONFIDENCE = 0.5

#: 降级影响等级 → 供 `degradation_notice` 的 `impact` 取值（架构 §11.2）。
IMPACT_LOW = "low"
IMPACT_MEDIUM = "medium"
IMPACT_HIGH = "high"


def compute_staleness_ms(as_of: int | None, *, now: int | None = None) -> int | None:
    """`now - as_of`（毫秒）；`as_of` 缺失时返回 None（D4：不臆造）。"""
    if as_of is None:
        return None
    return max(0, int(now if now is not None else now_ms()) - int(as_of))


def quality_fields(
    *,
    source: str,
    interface: str,
    as_of: int | None,
    unit: str | None,
    caliber: str | None,
    raw_ref: str,
    confidence: float | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """组装一组质量元数据。

    返回的键**全部是 `ToolResult` 的字段名**，可直接 `ToolResult(tool_name=..., data=..., **fields)`
    展开进构造器（`ToolResult` 无 `fetched_at`，该字段由 `AgentLoop._observe` 在产 Evidence 时补）。

    `as_of` 缺失时**保持 None**，由执行器第 ⑧ 步回落抓取时刻并计算 `staleness_ms`
    ——避免工具侧与执行器侧对「无 timestamp 的响应」给出两个不同的时点。
    """
    return {
        "source": source,
        "interface": interface,
        "as_of": as_of,
        "unit": unit,
        "caliber": caliber,
        "raw_ref": raw_ref,
        "confidence": confidence,
        "staleness_ms": compute_staleness_ms(as_of, now=now),
    }


def degradation_notice(
    reason: str,
    *,
    scope: str,
    impact: str = IMPACT_MEDIUM,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 UI 白名单组件 `degradation_notice`（架构 §11.2，必填 props `{scope, reason, impact}`）。

    - `scope`：降级影响范围（如工具名 / `run` / 数据域）；
    - `reason`：显式原因（来自 `ToolResult.degrade_reason` 或 `DegradedResult.reason`）；
    - `impact`：`low` / `medium` / `high`（供前端决定醒目程度）。

    返回的是**未落库的块描述**（`component` + `props_json`）；M5 负责补 `id/run_id/seq`
    并写入 `ui_blocks`，SSE 侧对应 `event: degrade`（架构 §13.1）。
    """
    props: dict[str, Any] = {"scope": scope, "reason": reason, "impact": impact}
    if extra:
        props.update(extra)
    return {"component": "degradation_notice", "props_json": props}

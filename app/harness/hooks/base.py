"""Harness 内核：Hook 机制基座（03_BUILD_PLAN §3 M1）。

管线三段：`PreHook → AgentLoop → PostHook`（架构 §3.1）。本文件定义：

- `PipelineContext`：一次 Run 内贯穿所有 Hook 与主循环的上下文对象；
- `HookResult`：Hook 的返回（是否放行、是否短路、原因）；
- `Hook`：Hook 抽象基类（`name` / `kind` / `priority` / `run`）。

**PostHook 无条件执行**：Pipeline 用 `try/finally` 保障，因此每个 PostHook 必须
**幂等**（重复执行不产生重复副作用）——见各 PostHook 的说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from ..agent_loop import AgentLoop, LoopContext
from ..model_router import ModelRouter
from ..planner import PlannedTask, Planner, TaskDAG
from ..tool_executor import ToolExecutor
from ..tool_registry import ToolRegistry

__all__ = ["HookKind", "HookResult", "PipelineContext", "Hook"]


class HookKind(StrEnum):
    """Hook 所属阶段。"""

    PRE = "PRE"
    POST = "POST"


@dataclass(slots=True)
class HookResult:
    """Hook 执行结果。"""

    ok: bool = True
    short_circuit: bool = False
    reason: str | None = None

    @classmethod
    def passed(cls) -> "HookResult":
        return cls(ok=True)

    @classmethod
    def reject(cls, reason: str) -> "HookResult":
        """拒绝继续（PreHook 短路：整条 Run 不再往下走）。"""
        return cls(ok=False, short_circuit=True, reason=reason)

    @classmethod
    def skip(cls, reason: str) -> "HookResult":
        """放行但记录原因（如「模型不可用，已降级」）。"""
        return cls(ok=True, reason=reason)


@dataclass
class PipelineContext:
    """一次 Run 的执行上下文（Hook 与主循环共享）。"""

    run_id: str
    goal: str
    thread_id: str | None = None
    session: Session | None = None
    settings: Any = None

    # --- 内核组件 ---
    registry: ToolRegistry | None = None
    model_router: ModelRouter | None = None
    tool_executor: ToolExecutor | None = None
    planner: Planner | None = None
    agent_loop: AgentLoop | None = None
    tracer: Any = None
    loop_ctx: LoopContext | None = None

    # --- 计划与执行 ---
    plan: TaskDAG | None = None
    tasks: list[PlannedTask] = field(default_factory=list)
    task_rows: list[Any] = field(default_factory=list)
    task_statuses: dict[str, str] = field(default_factory=dict)
    evidences: list[Any] = field(default_factory=list)
    persisted_evidence_ids: set[str] = field(default_factory=set)

    # --- 结果与终态 ---
    stop_reason: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    cost_total: float = 0.0
    tokens_total: int = 0
    trace_spans: int = 0
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # --- 便捷 ---
    @property
    def tool_names(self) -> list[str]:
        if self.registry is None:
            return []
        return self.registry.names()

    def add_evidence(self, evidence: Any) -> None:
        """登记一条证据（主循环 `AgentLoop._observe` 是唯一产出者）。

        `Pipeline.run` 会把本字段与 `loop_ctx.evidences` 指向**同一个列表**，
        因此 Hook 侧与主循环侧看到的是同一份证据（单一事实来源）。
        """
        self.evidences.append(evidence)


class Hook:
    """Hook 抽象基类：`run(ctx) -> HookResult`；数值小的 `priority` 先执行。"""

    name: str = "hook"
    kind: HookKind = HookKind.PRE
    priority: int = 100

    def run(self, ctx: PipelineContext) -> HookResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    def __call__(self, ctx: PipelineContext) -> HookResult:
        return self.run(ctx)

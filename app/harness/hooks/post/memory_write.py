"""PostHook ③：会话记忆写入（架构 §3.1 / C8 的写入端）。

把本次 Run 的「目标 + 计划规模 + 证据规模」写成一条 `SESSION` 记忆，
供下一次同 Thread 的研究复用（召回打分归 M4 的 `harness/memory.py`）。

**幂等**：用 `ctx.extra["memory_written"]` 标记，重复执行只写一次（因此它在
「每个 Task 后」与「finally」两处被调用也不会产生重复记忆）。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext
from ....store.repo import MemoryRepository

__all__ = ["MemoryWriteHook"]


class MemoryWriteHook(Hook):
    """写一条 SESSION 级记忆（kind=`run_summary`）。"""

    name = "memory_write"
    kind = HookKind.POST
    priority = 30

    def run(self, ctx: PipelineContext) -> HookResult:
        if ctx.extra.get("memory_written"):
            return HookResult.passed()
        if ctx.session is None:
            return HookResult.skip("无 session，跳过记忆写入")
        content = (
            f"目标：{ctx.goal}；任务数：{len(ctx.tasks)}；"
            f"证据数：{len(ctx.evidences)}；计划来源：{ctx.plan.source if ctx.plan else 'none'}"
        )
        MemoryRepository(ctx.session).create(
            scope="SESSION",
            kind="run_summary",
            content=content,
            thread_id=ctx.thread_id,
            run_id=ctx.run_id,
        )
        ctx.extra["memory_written"] = True
        return HookResult.skip("已写入 SESSION 记忆")

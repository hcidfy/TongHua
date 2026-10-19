"""PreHook ④：生成计划（架构 §3.1「无计划则调用 Planner 生成 Task DAG」）。

幂等：`ctx.plan` 已存在时直接放行（重新执行同一条链不会重复规划）。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["PlanHook"]


class PlanHook(Hook):
    """无计划 → 调 Planner 生成 Task DAG；工具名白名单由 Planner 保证。"""

    name = "plan"
    kind = HookKind.PRE
    priority = 40

    def run(self, ctx: PipelineContext) -> HookResult:
        if ctx.plan is not None:
            return HookResult.passed()
        if ctx.planner is None or ctx.registry is None:
            return HookResult.skip("未装配 Planner/Registry，跳过计划生成")
        plan = ctx.planner.plan(ctx.goal, tools=ctx.registry.names(), run_id=ctx.run_id)
        ctx.plan = plan
        ctx.tasks = plan.tasks
        for task in plan.tasks:
            ctx.extra.setdefault("task_tools", {})[f"#{task.seq}"] = list(task.tools)
        if plan.degraded:
            return HookResult.skip(plan.note or "计划为降级兜底规划")
        return HookResult.passed()

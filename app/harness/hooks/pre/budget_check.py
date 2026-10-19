"""PreHook ②：预算前置检查（架构 §3.1）。

「检查剩余 token / 成本 / 步数预算，不足则拒绝启动」。

M1 阶段说明：**预算的配置项（`BUDGET_*`）归属 M3**（03_BUILD_PLAN §3 M1 的配置项只有
`LLM_*`，不含预算），因此本 Hook 不引入任何新配置，而是读取 `ctx.extra["budget"]`
（由调用方或 M3 的 `BudgetManager` 注入）。未注入时**一律放行**——不阻断主链路。
"""

from __future__ import annotations

from typing import Any

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["BudgetCheckHook"]

#: 预算键名（与 M3 的 `BUDGET_*` 一一对应，便于届时直接接管）。
BUDGET_KEYS = ("max_steps", "max_tokens", "max_cost", "max_wall_time_s")


class BudgetCheckHook(Hook):
    """预算不足 → 短路拒绝启动（M1 无预算配置时不阻断）。"""

    name = "budget_check"
    kind = HookKind.PRE
    priority = 20

    def run(self, ctx: PipelineContext) -> HookResult:
        budget: dict[str, Any] = ctx.extra.get("budget") or {}
        if not budget:
            return HookResult.passed()

        used_steps = int(ctx.extra.get("steps_used", 0))
        max_steps = budget.get("max_steps")
        if max_steps is not None and used_steps >= int(max_steps):
            return HookResult.reject(f"预算耗尽：步数 {used_steps} ≥ {max_steps}")

        max_tokens = budget.get("max_tokens")
        if max_tokens is not None and ctx.tokens_total >= int(max_tokens):
            return HookResult.reject(f"预算耗尽：token {ctx.tokens_total} ≥ {max_tokens}")

        max_cost = budget.get("max_cost")
        if max_cost is not None and ctx.cost_total >= float(max_cost):
            return HookResult.reject(f"预算耗尽：成本 {ctx.cost_total} ≥ {max_cost}")

        return HookResult.passed()

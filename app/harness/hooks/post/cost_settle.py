"""PostHook ④：成本结算（架构 §3.1）。

口径硬约束（HANDOFF §6.2 / 架构 §4.1）：`runs.cost_total` / `runs.tokens_total` 的
**唯一数据来源是 `llm_calls` 表**，不靠内存累计——因此本 Hook 用
`LlmCallRepository.totals_for_run()` 聚合后**覆盖写**回 `runs`。

**幂等**：写的是「聚合绝对值」而非增量，重复执行结果一致。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext
from ....store.repo import LlmCallRepository, RunRepository

__all__ = ["CostSettleHook"]


class CostSettleHook(Hook):
    """按 `llm_calls` 聚合结算 Run 的成本与 token。"""

    name = "cost_settle"
    kind = HookKind.POST
    priority = 40

    def run(self, ctx: PipelineContext) -> HookResult:
        if ctx.session is None:
            return HookResult.skip("无 session，跳过成本结算")
        cost, tokens = LlmCallRepository(ctx.session).totals_for_run(ctx.run_id)
        ctx.cost_total, ctx.tokens_total = cost, tokens
        run = RunRepository(ctx.session).get(ctx.run_id)
        if run is not None:
            run.cost_total = cost
            run.tokens_total = tokens
            ctx.session.flush()
        return HookResult.skip(f"成本结算：cost={cost}，tokens={tokens}")

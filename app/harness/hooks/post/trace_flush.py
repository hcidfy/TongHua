"""PostHook ②：Trace 落库确认（架构 §3.1 / C15）。

`Tracer` 的 Span 是**退出 `with` 即同步落库**的（`app/observ/tracer.py`），因此本 Hook
不"搬运"数据，只做**结算与确认**：把本次 Run 已结算的 Span 数写进上下文供成本/可观测面读取，
并显式 `flush()` 一次，确保异常路径下已生成的 Span 不会因未提交而丢失。

**幂等**：只读计数 + flush，重复执行无副作用。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["TraceFlushHook"]


class TraceFlushHook(Hook):
    """确认 Span 已落库（Span 树：run→plan→task#N→loop#M→llm/tool）。"""

    name = "trace_flush"
    kind = HookKind.POST
    priority = 20

    def run(self, ctx: PipelineContext) -> HookResult:
        tracer = ctx.tracer
        if tracer is None:
            return HookResult.skip("未装配 Tracer，跳过")
        ctx.trace_spans = int(tracer.count())
        in_db = int(tracer.count_in_db()) if ctx.session is not None else 0
        ctx.extra["trace_spans_in_db"] = in_db
        if ctx.session is not None:
            ctx.session.flush()
        return HookResult.skip(f"Span 结算 {ctx.trace_spans} 个（落库 {in_db} 个）")

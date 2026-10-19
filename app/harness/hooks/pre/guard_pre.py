"""PreHook ③：目标合规前置检查（架构 §3.1「含越界诉求直接拦截」）。

统一规则要求：**不得给出操作建议 / 涨跌预测 / 收益承诺**（PRD §6 / 架构 §11.3）。
若用户目标本身就是越界诉求（如「保证这只股票翻倍」「帮我买入」），应当**在启动前拦截**，
而不是等跑到最后再拦——既省成本，也避免中间过程已产出违规内容。

M1 版实现为**关键词硬红线**（结构性检查与分级处置归属 M6 的 `compliance/guard.py`）。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["GuardPreHook", "GUARD_PRE_PATTERNS"]

#: 硬红线关键词：命中即拦截（越界诉求）。
GUARD_PRE_PATTERNS: tuple[str, ...] = (
    "保证收益",
    "保证盈利",
    "稳赚",
    "包赚",
    "必涨",
    "必跌",
    "一定会涨",
    "一定会跌",
    "翻倍",
    "帮我买入",
    "帮我买",
    "帮我卖出",
    "帮我卖",
    "代我下单",
    "推荐买入",
    "推荐股票",
    "预测股价",
    "预测涨跌",
    "内幕消息",
    "操纵股价",
)


class GuardPreHook(Hook):
    """目标越界 → 短路拦截（不启动 Run）。"""

    name = "guard_pre"
    kind = HookKind.PRE
    priority = 30

    def run(self, ctx: PipelineContext) -> HookResult:
        for pattern in GUARD_PRE_PATTERNS:
            if pattern in ctx.goal:
                reason = f"目标命中合规红线（{pattern}）：不予受理越界诉求"
                ctx.blocked = True
                ctx.block_reason = reason
                return HookResult.reject(reason)
        return HookResult.passed()

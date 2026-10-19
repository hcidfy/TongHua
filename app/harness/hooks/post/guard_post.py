"""PostHook ⑤：产物合规安检（架构 §3.1「产物发布前合规安检」）。

M1 版实现为**发布前的最小硬红线检查**（完整三类检查与分级处置归属 M6 的
`compliance/guard.py`）：

1. 结论文本命中预测/承诺/建议关键词 → **拒绝发布**（记 `guard_verdict=REJECTED`）；
2. 有结论但**无任何证据引用** → 拒绝（统一规则：无引用的 FACT 不得发布）。

**幂等**：只读取与标记上下文，不写库。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["GuardPostHook", "GUARD_POST_PATTERNS"]

#: 发布前硬红线关键词（与 GuardPreHook 共用同一套越界语义）。
GUARD_POST_PATTERNS: tuple[str, ...] = (
    "保证收益",
    "稳赚",
    "必涨",
    "必跌",
    "翻倍",
    "建议买入",
    "建议卖出",
    "推荐买入",
    "目标价",
    "预测",
)


class GuardPostHook(Hook):
    """产物发布前安检；判定结果写入 `ctx.extra["guard_post_verdict"]`。"""

    name = "guard_post"
    kind = HookKind.POST
    priority = 50

    def run(self, ctx: PipelineContext) -> HookResult:
        text = str(ctx.extra.get("conclusion_text") or "")
        if not text:
            ctx.extra.setdefault("guard_post_verdict", "PASS")
            return HookResult.skip("本次无结论文本，安检通过")

        for pattern in GUARD_POST_PATTERNS:
            if pattern in text:
                verdict = f"REJECTED（命中红线：{pattern}）"
                ctx.extra["guard_post_verdict"] = verdict
                ctx.extra["guard_post_blocked"] = True
                return HookResult.skip(f"产物未通过合规安检：{verdict}")

        if not ctx.evidences:
            ctx.extra["guard_post_verdict"] = "REJECTED（结论无证据引用）"
            ctx.extra["guard_post_blocked"] = True
            return HookResult.skip("产物未通过合规安检：结论缺少证据引用")

        ctx.extra["guard_post_verdict"] = "PASS"
        return HookResult.passed()

"""Harness 内核：分层压缩（架构 §7.2；03_BUILD_PLAN §3 M4）。

契约（03_BUILD_PLAN §3 M4 接口契约）::

    Compactor.compact(ctx) -> CompactResult{before_tokens, after_tokens, dropped_steps}

分层压缩表（架构 §7.2「关键：证据不能丢」）::

    | 层级         | 处理方式                                       | 保留     |
    | 系统提示     | 不压缩                                         | 全量     |
    | 计划（Task） | 不压缩，仅折叠已完成任务标题                   | 结构     |
    | 历史 Step    | 摘要化：N 个 Step 合并为一段摘要               | 结论要点 |
    | Evidence     | **下沉到 DB**，上下文只留 evidence_id + 一行摘要 | **引用** |
    | 长期记忆召回 | 按相关性截断                                   | Top-K    |

**正确性保证（FR-04 / 架构 §7.3）**：压缩只丢弃**渲染体积**，不丢弃事实——
`CompactResult.kept_evidence_ids` 恒等于压缩前上下文引用的**全部** `evidence_id`
（即便模型在 JSON 里漏报/误报，代码层也兜底补齐），因此 `Artifact` 引用的每个
`evidence_id` 在压缩后仍可在 `evidences` 表解析。

降级：未配置 LLM 时 `ModelRouter.complete("Compactor", ...)` 返回 `DEGRADED`，
本模块改用**确定性本地摘要**——只描述结构、不引入任何新数字或事实
（提示词 `compactor.md` 硬约束 ④）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

from .context import ContextBundle, StepDigest

__all__ = ["KEEP_RECENT_STEPS", "CompactResult", "Compactor"]

#: 压缩后至少保留的「最近 Step」条数（其余折叠进摘要，保证最新上下文不丢）。
KEEP_RECENT_STEPS = 3


class _CompactionDecision(BaseModel):
    """Compactor 角色的输出契约（提示词 `prompts/compactor.md`）。

    `dropped_steps` 是**上下文中的全局 Step 序号**（1-based，按渲染顺序），
    而不是 `StepDigest.seq`——后者只在单个 Task 内唯一，跨 Task 会重复。
    """

    summary: str = ""
    dropped_steps: list[int] = Field(default_factory=list)
    kept_evidence_ids: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class CompactResult:
    """一次分层压缩的结果（契约字段 + 证据引用/降级标记）。"""

    before_tokens: int = 0
    after_tokens: int = 0
    #: 被折叠的 Step 的**全局序号**（1-based，按上下文渲染顺序；非 `StepDigest.seq`）。
    dropped_steps: list[int] = field(default_factory=list)
    #: 压缩后上下文仍引用的全部 `evidence_id`（FR-04：恒等于压缩前的全集）。
    kept_evidence_ids: list[str] = field(default_factory=list)
    summary: str = ""
    degraded: bool = False


class Compactor:
    """分层压缩器（契约 `Compactor.compact(ctx) -> CompactResult`）。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model_router: Any = None,
        tracer: Any = None,
        keep_recent: int = KEEP_RECENT_STEPS,
    ) -> None:
        self.settings = settings or get_settings()
        self.model_router = model_router
        self.tracer = tracer
        self.keep_recent = max(0, int(keep_recent))
        self.top_k = int(getattr(self.settings, "memory_recall_top_k", 5) or 5)

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def compact(self, ctx: ContextBundle) -> CompactResult:
        """对 `ContextBundle` 就地分层压缩，返回压缩事实（不落库、不抛异常）。"""
        before = ctx.token_estimate()
        summary, dropped, degraded = self._decide(ctx)
        dropped = self._sanitize(ctx, dropped)

        # --- 应用分层压缩（就地改写 bundle）---
        ctx.dropped_steps = list(dropped)
        ctx.summary = summary
        # 折叠口径按**全局序号**（1-based）定位，`StepDigest.seq` 仅用于展示：
        # `seq` 在单 Task 内唯一、跨 Task 会重复，用它做集合过滤会误伤最新 Step。
        drop_set = set(dropped)
        kept_steps = [
            step for index, step in enumerate(ctx.steps, 1) if index not in drop_set
        ]
        for step in kept_steps:
            if not step.summary:
                step.summary = self._step_summary(step)
        ctx.steps = kept_steps
        # 长期记忆召回按相关性截断（Top-K）
        ctx.memory_lines = list(ctx.memory_lines[: self.top_k])
        ctx.compacted = True

        after = ctx.token_estimate()
        return CompactResult(
            before_tokens=before,
            after_tokens=after,
            dropped_steps=list(dropped),
            # FR-04 兜底：证据引用**永不**被压缩丢弃（含证据 id 全集，顺序不变）。
            kept_evidence_ids=ctx.evidence_ids(),
            summary=summary,
            degraded=degraded,
        )

    # ------------------------------------------------------------------ #
    # 决策：LLM 摘要 → 确定性兜底
    # ------------------------------------------------------------------ #
    def _decide(self, ctx: ContextBundle) -> tuple[str, list[int], bool]:
        """返回 `(summary, dropped_steps, degraded)`。LLM 不可用即走确定性兜底。"""
        if self.model_router is not None:
            result = self.model_router.complete(
                "Compactor",
                [{"role": "user", "content": self._payload(ctx)}],
                _CompactionDecision,
            )
            if result.ok and result.parsed:
                summary = str(result.parsed.get("summary") or "").strip()
                dropped = result.parsed.get("dropped_steps") or []
                return (
                    summary or self._fallback_summary(ctx),
                    [int(seq) for seq in dropped if isinstance(seq, (int, float))],
                    False,
                )
        return self._fallback_summary(ctx), self._fallback_dropped(ctx), True

    def _sanitize(self, ctx: ContextBundle, dropped: list[int]) -> list[int]:
        """清洗折叠决定：只认合法**全局序号**，且**最近 `keep_recent` 个 Step 永不折叠**。"""
        total = len(ctx.steps)
        valid = set(range(1, total + 1))
        protected = (
            set(range(max(1, total - self.keep_recent + 1), total + 1)) if self.keep_recent else set()
        )
        cleaned = sorted({index for index in dropped if index in valid and index not in protected})
        if not cleaned and total > self.keep_recent:
            # 模型没给出可用的折叠清单（或全被保护）→ 用确定性口径折叠最旧的部分
            cleaned = self._fallback_dropped(ctx)
        return cleaned

    def _fallback_dropped(self, ctx: ContextBundle) -> list[int]:
        """确定性折叠口径：除最近 `keep_recent` 个 Step 外，其余全部折叠（全局序号）。"""
        cut = max(0, len(ctx.steps) - self.keep_recent)
        return list(range(1, cut + 1))

    # ------------------------------------------------------------------ #
    # 确定性摘要（不引入新数字 / 事实）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _step_summary(step: StepDigest) -> str:
        """单个 Step 的一行摘要：保留 `seq / type / 工具名 / 证据引用`。"""
        head = f"#{step.seq} {step.type}"
        if step.tool:
            head += f"/{step.tool}"
        if step.evidence_id:
            head += f" → {step.evidence_id}"
        reason = step.payload.get("degrade_reason")
        if reason:
            head += f"（降级：{reason}）"
        return head

    def _fallback_summary(self, ctx: ContextBundle) -> str:
        """本地摘要：只描述「哪些 Step 被折叠」，不引入任何领域数字。"""
        dropped = self._fallback_dropped(ctx)
        if not dropped:
            return "上下文未发生折叠（无可折叠的历史 Step）。"
        heads = ", ".join(
            f"#{step.seq}/{step.type}" for step in ctx.steps if step.seq in set(dropped)
        )
        return f"已折叠历史 Step（{heads}）；证据引用与结论要点全部保留。"

    # ------------------------------------------------------------------ #
    # 送给模型的可压缩内容
    # ------------------------------------------------------------------ #
    @staticmethod
    def _payload(ctx: ContextBundle) -> str:
        lines: list[str] = ["请压缩以下研究上下文，严格按系统提示输出 JSON。", f"研究目标：{ctx.goal}"]
        if ctx.plan_lines:
            lines.append("【计划】" + " | ".join(ctx.plan_lines))
        if ctx.steps:
            lines.append("【历史 Step】")
            for step in ctx.steps:
                payload = json.dumps(step.payload, ensure_ascii=False, sort_keys=True, default=str)
                lines.append(f"#{step.seq} {step.type} {step.tool or ''} payload={payload}")
        if ctx.evidence_refs:
            lines.append("【证据（只做下沉引用，必须原样保留 evidence_id）】")
            for ref in ctx.evidence_refs:
                lines.append(
                    f"{ref.evidence_id} source={ref.source} as_of={ref.as_of} "
                    f"unit={ref.unit} caliber={ref.caliber} value={ref.value_preview}"
                )
        lines.append(
            '输出 JSON：{"summary": "...", "dropped_steps": [要折叠的 Step seq...], '
            '"kept_evidence_ids": ["..."]}'
        )
        return "\n".join(lines)

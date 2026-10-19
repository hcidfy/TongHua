"""Harness 内核：主循环（ReAct；架构 C3；03_BUILD_PLAN §3 M1）。

契约（架构 §3.2 C3）::

    AgentLoop.iterate(task, ctx) -> Step
    AgentLoop.run_task(task, ctx) -> TaskOutcome      # 多次 iterate 直到出口条件

一轮 ReAct = **Think(LLM) → Act(Tool) → Observe**，每轮落 3 个 `Step`
（`THINK` / `ACT` / `OBSERVE`，同 `task_id` 内 `seq` 递增）。

**Tracer 埋点在此同步完成**（03_BUILD_PLAN §2 时序硬约束 1 / 风险 R-8）：

```text
task#N (Pipeline 开)
└── loop#M (本模块开)
    ├── llm   —— 每次 Think 一个 span（attrs: role/provider/model/tokens/cost/status）
    └── tool  —— 由 ToolExecutor.invoke(tracer=...) 开（attrs: name/args_hash/status/attempts）
```

出口条件（M1 版，M3 的 `StopRule` 会接管更完整的 8 条规则）：
1. 计划内工具已全部调用 → 收口；
2. 模型主动 `finish` → 收口；
3. 达到 `max_rounds` → 强制收口；
4. 无可调用工具（计划为空）→ 直接 DONE。

**M4：Step 级停止规则判定（逐轮内嵌）**——每轮 ReAct 结束时调 `_judge()`，复用
`Pipeline` 注入的**同一个** `BudgetManager` / `StopState` / `StopRuleSet` 实例
（HANDOFF §17.5 硬约束「不要另建计量器」），命中即把原因写进 `LoopContext.stop_reason`，
`run_task` 随即中断本轮循环、交由管线收口。口径细则：

- 「一轮」= **真正执行了一个动作**的 ReAct 轮；模型判定收口的空转轮不计 Step、不参与判定；
- `no_progress` / `loop_detect` 的历史**按 Task 重置**（每个 Task 是独立取证单元），
  `budget.step()` 与 `action_count` 则是 Run 级累计。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.domain import Evidence, StepType, now_ms
from app.observ import Tracer
from app.store.repo import StepRepository
from app.store.schema import StepRow, TaskRow

from .budget import BudgetManager
from .context import evidence_ref
from .model_router import ModelRouter
from .stop_rules import StepView, StopRuleSet, StopState, action_signature
from .tool_executor import (
    DegradedResult,
    ToolExecutor,
    ToolInvocation,
    ToolResult,
    _validate_args,
)
from .tool_registry import ToolRegistry

__all__ = ["LoopContext", "TaskOutcome", "AgentLoop"]

#: 从研究目标里显式写出的证券代码（如 `600519.SH` / `000001.SZ`）。
_THSCODE_RE = re.compile(r"\b(\d{6})\.(SH|SZ|BJ)\b", re.IGNORECASE)

#: Think 的入参回灌重试上限（参数名/类型不合规时给模型一次自我修正机会，§10.2）。
MAX_ARG_RETRIES = 1

#: `get_price_history` 离线兜底的默认回看窗口（自然日）。
_FALLBACK_LOOKBACK_DAYS = 90


class _ReasonerDecision(BaseModel):
    """Reasoner 角色一轮决策的输出契约。"""

    thought: str = ""
    action: str = "tool"  # tool | finish
    tool_name: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LoopContext:
    """一次 Run 内贯穿主循环的上下文（由 Pipeline 构造并持有）。"""

    run_id: str
    goal: str
    registry: ToolRegistry
    tracer: Tracer | None = None
    #: 当前任务允许调用的工具（来自 TaskDAG；即 §10.2 的字段白名单）。
    tool_plan: list[str] = field(default_factory=list)
    #: 每个工具的固定入参（优先取这里；缺项时 `AgentLoop` 按 schema 做离线派生兜底）。
    args_by_tool: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 记忆召回（M4 由 `ContextInjectHook` 产出、`Pipeline` 回填），供 Reasoner 参考用户偏好。
    memory_lines: list[str] = field(default_factory=list)
    #: 累积的证据（落库由 PostHook 的 EvidencePersistHook 负责）。
    evidences: list[Evidence] = field(default_factory=list)
    #: 过程备注（降级原因、模型提前收口等），供 Trace 与调试。
    notes: list[str] = field(default_factory=list)

    # --- M4：Step 级停止规则判定（与 Pipeline **共用同一组实例**，见 HANDOFF §17.5）---
    #: 四维预算计量器（`Pipeline.budget`，不要另建）。
    budget: BudgetManager | None = None
    #: Run 级停止状态（`Pipeline.stop_state`，不要另建）。
    stop_state: StopState | None = None
    #: 8 条停止规则集合（`Pipeline.stop_rules`，无状态、可跨 Run 共享）。
    stop_rules: StopRuleSet | None = None
    #: 取本 Run 的 `(cost, tokens)` 累计（唯一数据来源是 `llm_calls`，由 Pipeline 注入）。
    llm_totals: Callable[[], tuple[float, int]] | None = None
    #: Run 开始时刻（毫秒），供 `max_wall_time` 判定。
    started_ms: int | None = None
    #: Step 级判定命中的停止原因 / 规则名（Pipeline 读取后收口）。
    stop_reason: str | None = None
    stop_rule: str | None = None


@dataclass(slots=True)
class TaskOutcome:
    """一个 Task 的执行结果。"""

    task_id: str
    status: str  # DONE | DEGRADED | FAILED
    rounds: int = 0
    steps: list[StepRow] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    degradation_reason: str | None = None


@dataclass(slots=True)
class _RoundState:
    """一次 `run_task` 内的轮次状态（轮次序号与已消费工具）。"""

    round_index: int = 0
    step_seq: int = 0
    consumed: set[str] = field(default_factory=set)


class AgentLoop:
    """ReAct 主循环：产出 Step、证据与 Tracer span。"""

    def __init__(
        self,
        model_router: ModelRouter,
        tool_executor: ToolExecutor,
        *,
        session: Session | None = None,
        tracer: Tracer | None = None,
        max_rounds: int = 6,
    ) -> None:
        self.model_router = model_router
        self.tool_executor = tool_executor
        self._steps = StepRepository(session) if session is not None else None
        self._tracer = tracer
        self.max_rounds = max(1, max_rounds)

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def iterate(
        self,
        task: TaskRow,
        ctx: LoopContext,
        *,
        state: _RoundState | None = None,
    ) -> StepRow:
        """执行**一轮** ReAct，落 `THINK` / `ACT` / `OBSERVE` 三个 Step，返回 `OBSERVE`。"""
        state = state if state is not None else _RoundState()
        state.round_index += 1
        tracer = ctx.tracer or self._tracer
        remaining = [name for name in ctx.tool_plan if name not in state.consumed]

        if tracer is not None:
            with tracer.span(f"loop#{state.round_index}") as loop_span:
                loop_span.set(task=task.seq, planned=len(ctx.tool_plan), remaining=len(remaining))
                step = self._round(task, ctx, state, remaining, tracer)
                loop_span.set(step_type=str(step.type), status="OK")
                return step
        return self._round(task, ctx, state, remaining, None)

    def run_task(
        self,
        task: TaskRow,
        ctx: LoopContext,
        *,
        tools: list[str] | None = None,
    ) -> TaskOutcome:
        """按出口条件连续 `iterate`，直到任务收口；返回 `TaskOutcome`。"""
        if tools is not None:
            ctx.tool_plan = list(tools)

        planned = list(ctx.tool_plan)
        if not planned:
            # 梳理/复核类任务：无工具可调，不产生证据，直接 DONE（不算降级）。
            ctx.notes.append(f"任务#{task.seq} 无工具调用（梳理类），直接收口")
            return TaskOutcome(task_id=task.id, status="DONE", rounds=0,
                               degradation_reason=None)

        state = _RoundState()
        before = len(ctx.evidences)
        steps: list[StepRow] = []
        # M4：每个 Task 的 ReAct 循环是**独立的进展计量单元**——`no_progress` / `loop_detect`
        # 的历史按 Task 重置。跨 Task 累加会把「各 Task 末尾的无工具收口轮」当成空转，
        # 从而在「全工具降级但仍逐 Task 取证」的正常路径上误报 NO_PROGRESS。
        # `action_count` 属 Run 级累计（Step 计数），**不重置**。
        if ctx.stop_state is not None:
            ctx.stop_state.no_progress_streak = 0
            ctx.stop_state.repeat_count = 0
            ctx.stop_state.last_signature = None
        while state.round_index < self.max_rounds:
            step = self.iterate(task, ctx, state=state)
            steps.append(step)
            # M4：Step 级判定已在 `_round` 末尾逐轮执行；命中即中断本轮循环。
            if ctx.stop_reason:
                ctx.notes.append(
                    f"任务#{task.seq} 第{state.round_index}轮起中断：停止规则 {ctx.stop_rule}"
                    f"（{ctx.stop_reason}）"
                )
                break
            if step.payload_json.get("action") == "finish":
                break

        produced = ctx.evidences[before:]
        degraded_notes = [
            s.payload_json.get("degrade_reason")
            for s in steps
            if s.type == str(StepType.OBSERVE) and s.payload_json.get("degrade_reason")
        ]
        if produced:
            status = "DEGRADED" if degraded_notes else "DONE"
            reason = "；".join(str(n) for n in degraded_notes) or None
        else:
            status = "DEGRADED"
            reason = "本轮未取到任何可用证据（工具全部降级）"
            ctx.notes.append(f"任务#{task.seq}：{reason}")

        return TaskOutcome(
            task_id=task.id,
            status=status,
            rounds=state.round_index,
            steps=steps,
            evidence_ids=[ev.id for ev in produced],
            degradation_reason=reason,
        )

    # ------------------------------------------------------------------ #
    # 一轮 ReAct 本体
    # ------------------------------------------------------------------ #
    def _round(
        self,
        task: TaskRow,
        ctx: LoopContext,
        state: _RoundState,
        remaining: list[str],
        tracer: Tracer | None,
    ) -> StepRow:
        # --- ① THINK ---
        thought, tool_name, args, llm_status, llm_span_id = self._think(
            task, ctx, remaining, tracer
        )
        self._add_step(
            task, state, StepType.THINK,
            {"thought": thought, "tool": tool_name, "llm_status": llm_status},
            llm_span_id=llm_span_id,
        )

        # --- ② ACT（模型判定收口时不调用工具）---
        if tool_name is None:
            ctx.notes.append(f"任务#{task.seq} 第{state.round_index}轮：模型判定收口")
            step = self._add_step(
                task, state, StepType.OBSERVE, {"action": "finish", "remaining": remaining}
            )
            # 收口轮**没有执行任何动作**，不算一个 Step、也不参与进展计量
            # （否则每个 Task 末尾都会白白累积一次 no_progress）。
            return step

        result = self.tool_executor.invoke(
            ctx.run_id, task.id, ToolInvocation(tool_name, args), tracer=tracer
        )
        state.consumed.add(tool_name)
        self._add_step(
            task, state, StepType.ACT,
            {
                "tool": tool_name,
                "args": args,
                "ok": bool(result.ok),
                "attempts": result.attempts,
                "reused": bool(getattr(result, "reused", False)),
                "cached": bool(getattr(result, "cached", False)),
            },
            tool_call_id=result.tool_call_id,
        )

        # --- ③ OBSERVE ---
        evidence = self._observe(task, ctx, result)
        degrade_reason = getattr(result, "reason", None) or getattr(result, "degrade_reason", None)
        step = self._add_step(
            task, state, StepType.OBSERVE,
            {
                "action": "observe",
                "tool": tool_name,
                "evidence_id": evidence.id if evidence else None,
                "degrade_reason": degrade_reason,
                "remaining": [n for n in ctx.tool_plan if n not in state.consumed],
            },
            tool_call_id=result.tool_call_id,
        )
        # M4：Step 级判定（本轮动作签名 + 是否产出新证据）
        self._judge(
            ctx,
            signature=action_signature(tool_name, args),
            new_evidence=1 if evidence else 0,
        )
        return step

    # ------------------------------------------------------------------ #
    # M4：Step 级停止规则判定（逐轮内嵌；共用 Pipeline 的同一组实例）
    # ------------------------------------------------------------------ #
    def _judge(self, ctx: LoopContext, *, signature: str | None, new_evidence: int) -> None:
        """每轮结束后：①推进四维计量 ②跑 8 条停止规则（record + check）。

        计量与判定的实例**全部来自 `LoopContext`**（即 `Pipeline` 的那一组），
        因此不会出现第二套计量器（HANDOFF §17.5 硬约束）。命中即把原因写回 `ctx`。
        """
        if ctx.stop_rules is None or ctx.stop_state is None:
            return  # 未注入计量器（纯主循环单测）→ 不判定
        if ctx.budget is not None:
            ctx.budget.step()  # 一轮 ReAct = 1 step
            if ctx.llm_totals is not None:
                cost, tokens = ctx.llm_totals()
                ctx.budget.charge_llm(tokens=tokens, cost=cost)
            if ctx.started_ms is not None:
                ctx.budget.charge(wall_time_ms=max(0, now_ms() - ctx.started_ms))
        if ctx.stop_reason:
            return  # 已命中，不重复判定
        step = StepView(
            index=ctx.stop_state.action_count + 1,
            signature=signature,
            new_evidence=new_evidence,
        )
        reason = ctx.stop_rules.observe(ctx.stop_state, step)
        if reason is not None:
            ctx.stop_reason = str(reason)
            ctx.stop_rule = ctx.stop_rules.hit_rule
            ctx.notes.append(f"Step 级停止：命中规则 {ctx.stop_rule}（{reason}）")

    def _think(
        self,
        task: TaskRow,
        ctx: LoopContext,
        remaining: list[str],
        tracer: Tracer | None,
    ) -> tuple[str, str | None, dict[str, Any], str, str | None]:
        """Think：LLM 决策下一动作；LLM 降级时退化为「按计划取下一个工具」。

        **入参正确性保障**（HANDOFF §21.4）：消息里带上**每个待调用工具的完整参数签名**
        （参数名 + 类型 + 必填标记，取自 `ToolSpec.schema_json`）与**已取证事实摘要**，
        模型照抄参数名即可，不再靠猜；若模型给出的 `args` 仍不合 schema，则把校验错误
        回灌重试一次（`MAX_ARG_RETRIES`），仍不合规则按计划回退。
        """
        if not remaining:
            return "计划内工具已全部调用，收口", None, {}, "PLAN_DONE", None

        fallback_tool = remaining[0]
        conversation: list[dict[str, Any]] = [
            {"role": "user", "content": self._reasoner_prompt(task, ctx, remaining)}
        ]
        span_id: str | None = None
        result = None

        for _ in range(1 + MAX_ARG_RETRIES):
            result, span_id = self._ask_reasoner(ctx, task, conversation, tracer)
            if not (result.ok and result.parsed):
                break

            action = str(result.parsed.get("action") or "").lower()
            tool_name = result.parsed.get("tool_name")
            if action == "finish":
                return str(result.parsed.get("thought") or ""), None, {}, result.status, span_id

            if not (isinstance(tool_name, str) and tool_name in remaining):
                # 幻觉工具名 → 白名单拦截，退回计划内工具（不静默放行）
                ctx.notes.append(f"模型给出非法工具名 {tool_name!r}，已按白名单退回 {fallback_tool}")
                break

            raw_args = result.parsed.get("args")
            args = dict(raw_args) if isinstance(raw_args, dict) else {}
            problem = self._args_problem(ctx, tool_name, args)
            if problem is None:
                return (
                    str(result.parsed.get("thought") or ""),
                    tool_name,
                    args,
                    result.status,
                    span_id,
                )
            ctx.notes.append(f"模型给出 {tool_name} 的入参不合规（{problem}），已回灌重试")
            conversation = [
                *conversation,
                {"role": "assistant", "content": json.dumps(result.parsed, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"上一次 tool_name={tool_name} 的 args 不合规：{problem}。"
                        "参数名与类型必须严格照抄上面列出的工具签名（带 * 的是必填），"
                        "请只输出修正后的 JSON。"
                    ),
                },
            ]

        # 降级：按计划顺序取下一个工具，入参优先用调用方给定值、否则按 schema 离线派生
        args = ctx.args_by_tool.get(fallback_tool) or self._derive_args(fallback_tool, ctx)
        reason = (result.error if result is not None else None) or "模型未给出可用决策"
        return (
            f"模型不可用（{reason}），按计划调用 {fallback_tool}",
            fallback_tool,
            dict(args),
            result.status if result is not None else "DEGRADED",
            span_id,
        )

    def _ask_reasoner(
        self,
        ctx: LoopContext,
        task: TaskRow,
        messages: list[dict[str, Any]],
        tracer: Tracer | None,
    ) -> tuple[Any, str | None]:
        """调一次 Reasoner（带 `llm` span），返回 `(LLMResult, span_id)`。"""
        if tracer is None:
            result = self.model_router.complete(
                "Reasoner", messages, _ReasonerDecision, run_id=ctx.run_id, task_id=task.id
            )
            return result, None
        with tracer.span("llm", role="Reasoner") as span:
            result = self.model_router.complete(
                "Reasoner", messages, _ReasonerDecision, run_id=ctx.run_id, task_id=task.id
            )
            span.set(
                status=result.status,
                provider=result.provider,
                model=result.model,
                tokens=result.total_tokens,
                cost=result.cost,
                failover=result.failover,
                llm_call_id=result.call_id,
            )
            return result, span.span_id

    # ------------------------------------------------------------------ #
    # Reasoner 入参：签名渲染 / 事实回灌 / 离线派生
    # ------------------------------------------------------------------ #
    def _reasoner_prompt(self, task: TaskRow, ctx: LoopContext, remaining: list[str]) -> str:
        """组装 Reasoner 的单条 user 消息（目标 / 任务 / 工具签名 / 已取证事实 / 待调工具）。"""
        lines = [
            f"研究目标：{ctx.goal}",
            f"当前任务：{task.title}（{task.intent}）",
            "可用工具与参数（参数名与类型必须严格照抄，不得改写；带 * 为必填）：",
            *self._tool_signatures(ctx, remaining),
        ]
        if ctx.memory_lines:
            lines.append("记忆召回（用户偏好，仅作参考）：")
            lines.extend(f"- {line}" for line in ctx.memory_lines)
        digest = self._evidence_lines(ctx)
        if digest:
            lines.append("已取证事实（不得重复调用工具取同样的数据）：")
            lines.extend(digest)
        lines.append(f"待调用工具（只能选这些或收口）：{', '.join(remaining)}")
        lines.append(
            '只输出 JSON：{"thought": "...", "action": "tool 或 finish", '
            '"tool_name": "工具名", "args": {}}'
        )
        return "\n".join(lines)

    def _tool_signatures(self, ctx: LoopContext, names: list[str]) -> list[str]:
        """渲染待调用工具的参数签名：`- 名(参数*:类型, 可选:类型)`（无 schema 时退化为 `名()`）。"""
        rendered: list[str] = []
        for name in names:
            schema = self._schema_of(ctx, name)
            properties = dict(schema.get("properties") or {})
            required = set(schema.get("required") or [])
            if not properties:
                rendered.append(f"- {name}()")
                continue
            args = ", ".join(
                f"{key}{'*' if key in required else ''}:{_type_hint(properties.get(key) or {})}"
                for key in properties
            )
            rendered.append(f"- {name}({args})")
        return rendered

    @staticmethod
    def _evidence_lines(ctx: LoopContext, *, limit: int = 10) -> list[str]:
        """已取证事实的一行摘要（复用 `context.evidence_ref`，不改写 `evidence_id`）。"""
        lines: list[str] = []
        for item in ctx.evidences[-limit:]:
            ref = evidence_ref(item)
            head = f"- evidence_id={ref.evidence_id} {ref.source} {ref.interface}"
            detail = ref.value_preview or ref.digest
            lines.append(f"{head} value={detail}" if detail else head)
        return lines

    @staticmethod
    def _schema_of(ctx: LoopContext, name: str) -> dict[str, Any]:
        if ctx.registry is None:
            return {}
        spec = ctx.registry.get(name)
        return dict(getattr(spec, "schema_json", None) or {})

    def _args_problem(self, ctx: LoopContext, name: str, args: dict[str, Any]) -> str | None:
        """模型给出的 `args` 是否满足该工具 schema（复用执行器同一套校验口径）。"""
        return _validate_args(self._schema_of(ctx, name), args)

    def _derive_args(self, name: str, ctx: LoopContext) -> dict[str, Any]:
        """离线兜底入参：按 schema 的必填项 + 目标里可确定的取值派生（不猜、缺则缺）。

        取值来源（按优先级）：① `ctx.args_by_tool`（由调用方给定，见 `Pipeline`）；
        ② 已取证事实里的 `thscode`；③ 研究目标里显式写出的 `thscode`；④ 目标原文（`q`）。
        派生不出必填值时返回 `{}` —— 让执行器按「参数校验失败」正常降级，而不是编造标的。
        """
        required = list(self._schema_of(ctx, name).get("required") or [])
        if not required:
            return {}
        codes = self._known_thscodes(ctx)
        derived: dict[str, Any] = {}
        for key in required:
            if key == "q":
                derived[key] = ctx.goal.strip()
            elif key == "thscode":
                derived[key] = codes[0] if codes else ""
            elif key == "thscodes":
                derived[key] = list(codes)
            elif key == "start_ms":
                derived[key] = now_ms() - _FALLBACK_LOOKBACK_DAYS * 86_400_000
            elif key == "end_ms":
                derived[key] = now_ms()
        return {key: value for key, value in derived.items() if value not in ("", [], None)}

    @staticmethod
    def _known_thscodes(ctx: LoopContext) -> list[str]:
        """已知标的代码：优先取已取证事实里的 `thscode`，其次取目标里显式写出的代码。"""
        found: list[str] = []
        for item in ctx.evidences:
            value = getattr(item, "value_json", None)
            for code in _collect_thscodes(value):
                if code not in found:
                    found.append(code)
        if not found:
            found = [f"{code}.{market.upper()}" for code, market in _THSCODE_RE.findall(ctx.goal)]
        return found

    def _observe(
        self, task: TaskRow, ctx: LoopContext, result: ToolResult | DegradedResult
    ) -> Evidence | None:
        """Observe：成功取数 → 构造一条 `Evidence`（C3 强制字段全部显式给出）。"""
        if not isinstance(result, ToolResult):
            return None
        evidence = Evidence(
            run_id=ctx.run_id,
            task_id=task.id,
            tool_call_id=result.tool_call_id,
            source=result.source or "unknown",
            interface=result.interface or result.tool_name,
            as_of=result.as_of,
            unit=result.unit,
            caliber=result.caliber,
            raw_ref=result.raw_ref or f"tool://{result.tool_name}",
            confidence=result.confidence,
            fetched_at=now_ms(),
            staleness_ms=result.staleness_ms,
            value_json=result.data,
            degrade_reason=result.degrade_reason,
        )
        ctx.evidences.append(evidence)
        return evidence

    def _add_step(
        self,
        task: TaskRow,
        state: _RoundState,
        step_type: StepType,
        payload: dict[str, Any],
        *,
        llm_span_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> StepRow:
        state.step_seq += 1
        if self._steps is not None:
            return self._steps.create(
                task_id=task.id,
                seq=state.step_seq,
                type=str(step_type),
                payload_json=payload,
                llm_span_id=llm_span_id,
                tool_call_id=tool_call_id,
            )
        # 无 session 时也返回一个未落库的 StepRow（纯内存场景）
        return StepRow(
            id=f"mem-{task.id}-{state.step_seq}",
            task_id=task.id,
            seq=state.step_seq,
            type=str(step_type),
            payload_json=payload,
            llm_span_id=llm_span_id,
            tool_call_id=tool_call_id,
            created_at=now_ms(),
        )


def _type_hint(prop: dict[str, Any]) -> str:
    """JSON Schema 属性的短类型标记（`array<string>` / `object` / `string`…）。"""
    kind = str(prop.get("type") or "any")
    if kind == "array":
        item = str((prop.get("items") or {}).get("type") or "")
        return f"array<{item}>" if item else "array"
    return kind


def _collect_thscodes(value: Any, *, depth: int = 3) -> list[str]:
    """从取数结果里递归收集 `thscode` 取值（供后续工具复用已解析标的）。"""
    found: list[str] = []
    if depth < 0:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "thscode" and isinstance(item, str) and item.strip():
                found.append(item.strip())
            elif isinstance(item, (dict, list, tuple)):
                found.extend(_collect_thscodes(item, depth=depth - 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_collect_thscodes(item, depth=depth - 1))
    return found

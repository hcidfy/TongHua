"""Harness 内核：停止规则（FR-08 的 **8 条**；03_BUILD_PLAN §3 M3）。

契约（03_BUILD_PLAN §3 M3 接口契约）::

    StopRule.check(run, step) -> StopReason | None

职责：在每一步之后判定「是否必须停下来」，命中即返回 `StopReason`（8 取值已由 M0 契约
`app/domain/enums.py` 冻结）。**只判定，不落库**——`StopRuleHit` 事件与 `runs.stop_reason`
由管线（`Pipeline`）在收到非 None 时写入，这样规则本身可被单测直接驱动。

8 条规则（顺序即判定优先级，与 `BudgetManager.exhausted()` 的四维顺序一致）::

    ① max_steps      —— budget.used.steps      >= BUDGET_MAX_STEPS
    ② max_tokens     —— budget.used.tokens     >= BUDGET_MAX_TOKENS
    ③ max_cost       —— budget.used.cost       >= BUDGET_MAX_COST_CNY
    ④ max_wall_time  —— budget.used.wall_time_s>= BUDGET_MAX_WALL_TIME_S
    ⑤ loop_detect    —— 同一动作签名连续重复   >= LOOP_DETECT_REPEAT
    ⑥ no_progress    —— 连续无新证据的步数     >= NO_PROGRESS_STEPS
    ⑦ user_abort     —— 用户主动暂停 / 中止
    ⑧ guard_reject   —— 合规护栏拦截（PreHook GuardPre / PostHook GuardPost）

口径约束：
- 前 4 条的阈值**只有一份**——复用 `budget.BudgetLimits`，本模块不另起一套数字；
- 判定所需的「历史」放在 `StopState` 里（属 Run 级状态），规则对象本身**无状态**，
  因此同一 `StopRuleSet` 实例可被多个 Run 共享（见 `StopState` 的 `record()`）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.domain import StopReason

from .budget import BudgetLimits, BudgetManager

__all__ = [
    "STOP_RULE_COUNT",
    "RULE_REASON",
    "StepView",
    "StopState",
    "StopRule",
    "MaxStepsRule",
    "MaxTokensRule",
    "MaxCostRule",
    "MaxWallTimeRule",
    "LoopDetectRule",
    "NoProgressRule",
    "UserAbortRule",
    "GuardRejectRule",
    "StopRuleSet",
    "action_signature",
]

#: FR-08 规定的停止规则条数（契约数字，改动即返工）。
STOP_RULE_COUNT = 8


# ---------------------------------------------------------------------- #
# 视图对象：`StopRule.check(run, step)` 的两侧
# ---------------------------------------------------------------------- #
@dataclass(slots=True)
class StepView:
    """`check(run, step)` 的 **step 侧**视图：本步的观测事实（由调用方填）。"""

    index: int = 0
    #: 本步的动作签名（`action_signature()` 产出）；`None` 表示"不算作一个动作"。
    signature: str | None = None
    #: 本步新增的证据条数（`<=0` 视为无进展）。
    new_evidence: int = 0


@dataclass(slots=True)
class StopState:
    """`check(run, step)` 的 **run 侧**视图：一次 Run 的实时可判定状态。

    四维用量直接读 `budget`（唯一数据来源），不做二次累计；重复/无进展的历史由
    `record()` 维护，规则只读不算。
    """

    budget: BudgetManager = field(default_factory=BudgetManager)
    #: 循环检测阈值（`LOOP_DETECT_REPEAT`）。
    loop_detect_repeat: int = 3
    #: 无进展检测阈值（`NO_PROGRESS_STEPS`）。
    no_progress_steps: int = 4
    #: 用户是否已请求暂停/中止（架构 §6.1 第 3 类中断）。
    abort_requested: bool = False
    abort_reason: str | None = None
    #: 是否已被合规护栏拦截（PreHook `GuardPreHook` / PostHook `GuardPostHook`）。
    guard_rejected: bool = False
    guard_reason: str | None = None

    # --- 观测历史（由 `record()` 维护）---
    action_count: int = 0
    last_signature: str | None = None
    #: 与 `last_signature` 连续相同的次数（首次记 1）。
    repeat_count: int = 0
    #: 连续无新证据的步数（只要出现一条新证据即归零）。
    no_progress_streak: int = 0

    @classmethod
    def from_settings(
        cls,
        settings: object,
        *,
        budget: BudgetManager | None = None,
    ) -> "StopState":
        """从 `Settings` 构造（阈值缺字段时回落架构 §14.1 默认值）。"""
        return cls(
            budget=budget or BudgetManager(limits=BudgetLimits.from_settings(settings)),
            loop_detect_repeat=int(getattr(settings, "loop_detect_repeat", 3) or 3),
            no_progress_steps=int(getattr(settings, "no_progress_steps", 4) or 4),
        )

    # --- 事件回填 ---
    def record(self, signature: str | None = None, new_evidence: int = 0) -> "StopState":
        """登记本步动作，推进循环/无进展计数。"""
        if signature:
            self.action_count += 1
            if signature == self.last_signature:
                self.repeat_count += 1
            else:
                self.last_signature = signature
                self.repeat_count = 1
        if new_evidence > 0:
            self.no_progress_streak = 0
        else:
            self.no_progress_streak += 1
        return self

    def request_abort(self, reason: str | None = None) -> "StopState":
        self.abort_requested = True
        self.abort_reason = reason
        return self

    def reject_guard(self, reason: str | None = None) -> "StopState":
        self.guard_rejected = True
        self.guard_reason = reason
        return self


def action_signature(tool_name: str | None, args: Mapping[str, Any] | None = None) -> str | None:
    """动作签名 = `工具名 + 入参指纹`；无工具名时返回 `None`（不参与循环检测）。"""
    if not tool_name:
        return None
    payload = json.dumps(dict(args or {}), sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{tool_name}#{digest}"


# ---------------------------------------------------------------------- #
# 8 条规则（无状态；阈值来自 `StopState`）
# ---------------------------------------------------------------------- #
class StopRule(Protocol):
    """单条停止规则（`name` 用于 `runs.stop_reason` 之外的排障与 Trace 展示）。"""

    name: str

    def check(self, run: StopState, step: StepView) -> StopReason | None:  # pragma: no cover
        ...


@dataclass(frozen=True, slots=True)
class MaxStepsRule:
    name: str = "max_steps"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.budget.used.steps >= run.budget.limits.max_steps:
            return StopReason.MAX_STEPS
        return None


@dataclass(frozen=True, slots=True)
class MaxTokensRule:
    name: str = "max_tokens"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.budget.used.tokens >= run.budget.limits.max_tokens:
            return StopReason.MAX_TOKENS
        return None


@dataclass(frozen=True, slots=True)
class MaxCostRule:
    name: str = "max_cost"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.budget.used.cost >= run.budget.limits.max_cost:
            return StopReason.MAX_COST
        return None


@dataclass(frozen=True, slots=True)
class MaxWallTimeRule:
    name: str = "max_wall_time"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.budget.used.wall_time_s >= run.budget.limits.max_wall_time_s:
            return StopReason.MAX_WALL_TIME
        return None


@dataclass(frozen=True, slots=True)
class LoopDetectRule:
    """同一动作签名连续重复 `LOOP_DETECT_REPEAT` 次 → 死循环。"""

    name: str = "loop_detect"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.loop_detect_repeat > 0 and run.repeat_count >= run.loop_detect_repeat:
            return StopReason.LOOP_DETECT
        return None


@dataclass(frozen=True, slots=True)
class NoProgressRule:
    """连续 `NO_PROGRESS_STEPS` 个动作未产出新证据 → 空转。"""

    name: str = "no_progress"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.no_progress_steps > 0 and run.no_progress_streak >= run.no_progress_steps:
            return StopReason.NO_PROGRESS
        return None


@dataclass(frozen=True, slots=True)
class UserAbortRule:
    name: str = "user_abort"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.abort_requested:
            return StopReason.USER_ABORT
        return None


@dataclass(frozen=True, slots=True)
class GuardRejectRule:
    name: str = "guard_reject"

    def check(self, run: StopState, step: StepView) -> StopReason | None:
        if run.guard_rejected:
            return StopReason.GUARD_REJECT
        return None


# ---------------------------------------------------------------------- #
# 规则集合
# ---------------------------------------------------------------------- #
#: 规则名 ↔ `StopReason` 的一一对应（FR-08 的 8 条）。
RULE_REASON: dict[str, StopReason] = {
    "max_steps": StopReason.MAX_STEPS,
    "max_tokens": StopReason.MAX_TOKENS,
    "max_cost": StopReason.MAX_COST,
    "max_wall_time": StopReason.MAX_WALL_TIME,
    "loop_detect": StopReason.LOOP_DETECT,
    "no_progress": StopReason.NO_PROGRESS,
    "user_abort": StopReason.USER_ABORT,
    "guard_reject": StopReason.GUARD_REJECT,
}


def _default_rules() -> tuple[StopRule, ...]:
    return (
        MaxStepsRule(),
        MaxTokensRule(),
        MaxCostRule(),
        MaxWallTimeRule(),
        LoopDetectRule(),
        NoProgressRule(),
        UserAbortRule(),
        GuardRejectRule(),
    )


class StopRuleSet:
    """按固定顺序求值的 8 条规则集合（命中即返回，不再往下判）。

    `check()` 返回的 `StopReason` 即"命中规则名"的业务别名（8 个取值与 8 条规则一一对应），
    排障用的 `hit_rule` 记录规则对象名（如 `loop_detect`）。
    """

    def __init__(self, rules: list[StopRule] | None = None) -> None:
        self.rules: list[StopRule] = list(rules) if rules else list(_default_rules())
        self.hit_rule: str | None = None
        self.hit_reason: StopReason | None = None

    # --- 契约入口 ---
    def check(self, run: StopState, step: StepView | None = None) -> StopReason | None:
        """逐条判定；返回首个命中的 `StopReason`，无命中返回 `None`。"""
        view = step if step is not None else StepView()
        for rule in self.rules:
            reason = rule.check(run, view)
            if reason is not None:
                self.hit_rule = rule.name
                self.hit_reason = reason
                return reason
        return None

    # --- 便捷：登记本步并判定（等价于 record + check）---
    def observe(self, run: StopState, step: StepView) -> StopReason | None:
        run.record(signature=step.signature, new_evidence=step.new_evidence)
        return self.check(run, step)

    # --- 可观测 ---
    @property
    def names(self) -> list[str]:
        return [rule.name for rule in self.rules]

    def rule_for(self, reason: StopReason) -> str | None:
        """由 `StopReason` 反查规则名（8 取值 ↔ 8 规则，一一对应）。"""
        for name, value in RULE_REASON.items():
            if value == reason:
                return name
        return None

    def snapshot(self) -> dict[str, Any]:
        """可观测快照（供 Trace / HANDOFF 验收记录）。"""
        return {
            "rules": self.names,
            "count": len(self.rules),
            "hit_rule": self.hit_rule,
            "hit_reason": str(self.hit_reason) if self.hit_reason else None,
        }

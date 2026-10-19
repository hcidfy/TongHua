"""Harness 内核：预算计量（架构 §3.2 / §6.1；03_BUILD_PLAN §3 M3）。

契约（03_BUILD_PLAN §3 M3 接口契约）::

    BudgetManager.charge(usage) / remaining()

职责：**step / token / 成本 / 时延的实时计量与限额判定**；任一维度耗尽即触发
`BudgetExhausted`（架构 §6.1 的第 4 类中断「预算耗尽」→ 产出现有成果、状态 `STOPPED`）。

口径约束（HANDOFF §6.2 / §8.2）：
- token / 成本的**唯一数据来源是 `llm_calls` 表**（`LlmCallRepository.totals_for_run()`），
  因此本模块只做**累加与判定**，不自己去猜用量；
- 时延统一毫秒（D2），对外按「秒」比较（配置项 `BUDGET_MAX_WALL_TIME_S` 单位是秒）；
- 配置缺省值取自架构 §14.1：20 / 100000 / 2.0 / 180。

与 `stop_rules.py` 的分工：本模块负责**记账 + 是否耗尽**；`StopRuleSet` 负责把耗尽
映射成 8 条停止规则之一的 `StopReason`（两者共用同一份 `BudgetLimits`，避免两套阈值）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.domain import StopReason

__all__ = [
    "BUDGET_KEYS",
    "BudgetLimits",
    "Usage",
    "BudgetExhausted",
    "BudgetManager",
]

#: 与 `hooks/pre/budget_check.py::BUDGET_KEYS` 同名同序——`to_hook_budget()` 的键即取自此处。
BUDGET_KEYS: tuple[str, ...] = ("max_steps", "max_tokens", "max_cost", "max_wall_time_s")


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """一次 Run 的四维限额（架构 §14.1 的 `BUDGET_*`）。"""

    max_steps: int = 20
    max_tokens: int = 100000
    max_cost: float = 2.0
    max_wall_time_s: float = 180.0

    @classmethod
    def from_settings(cls, settings: object) -> "BudgetLimits":
        """从 `Settings` 构造；缺字段时回落到架构默认值（不抛错）。"""
        return cls(
            max_steps=int(getattr(settings, "budget_max_steps", 20) or 20),
            max_tokens=int(getattr(settings, "budget_max_tokens", 100000) or 100000),
            max_cost=float(getattr(settings, "budget_max_cost_cny", 2.0) or 2.0),
            max_wall_time_s=float(getattr(settings, "budget_max_wall_time_s", 180.0) or 180.0),
        )

    def as_hook_budget(self) -> dict[str, float]:
        """转成 `BudgetCheckHook` 认识的键（M1 预留、M3 正式启用）。"""
        return {
            "max_steps": self.max_steps,
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
            "max_wall_time_s": self.max_wall_time_s,
        }


@dataclass(slots=True)
class Usage:
    """截至当前检查点的累计用量（绝对值，非增量）。"""

    steps: int = 0
    tokens: int = 0
    cost: float = 0.0
    wall_time_ms: int = 0

    @property
    def wall_time_s(self) -> float:
        return self.wall_time_ms / 1000.0


class BudgetExhausted(RuntimeError):
    """预算耗尽（架构 §6.1 第 4 类中断）——携带命中的 `StopReason`。"""

    def __init__(self, reason: StopReason, message: str, *, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.usage = usage or Usage()


@dataclass(slots=True)
class BudgetManager:
    """四维预算的实时计量器（`charge` 幂等覆盖绝对值，`remaining` 返回余量）。

    `charge()` 传入的是**累计绝对值**（step 数 / token / 成本 / 墙钟），不是增量：
    这样与「`llm_calls` 聚合是唯一数据来源」的口径一致，重复 charge 同一检查点也安全。
    """

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    used: Usage = field(default_factory=Usage)

    # --- 契约入口 ---
    def charge(self, usage: Usage | None = None, **fields: float) -> Usage:
        """累加/覆盖用量：显式字段优先，未给的维度保持上一次的值。

        典型调用：`manager.charge(Usage(steps=n, tokens=t, cost=c, wall_time_ms=w))`
        或 `manager.charge(steps=n, tokens=t, cost=c, wall_time_ms=w)`。
        """
        if usage is not None:
            self.used = Usage(
                steps=int(usage.steps),
                tokens=int(usage.tokens),
                cost=float(usage.cost),
                wall_time_ms=int(usage.wall_time_ms),
            )
            return self.used
        if "steps" in fields:
            self.used.steps = int(fields["steps"])
        if "tokens" in fields:
            self.used.tokens = int(fields["tokens"])
        if "cost" in fields:
            self.used.cost = float(fields["cost"])
        if "wall_time_ms" in fields:
            self.used.wall_time_ms = int(fields["wall_time_ms"])
        return self.used

    def charge_llm(self, *, tokens: int, cost: float) -> Usage:
        """只推进 token / 成本两维（其余保持），适配 `llm_calls` 聚合结果的落点。"""
        self.used.tokens = int(tokens)
        self.used.cost = float(cost)
        return self.used

    def step(self, n: int = 1) -> Usage:
        """推进 step 计数（每完成一个 Task / Step 调一次）。"""
        self.used.steps += int(n)
        return self.used

    def reset(self, *, wall_time_ms: int = 0) -> Usage:
        """重置为一次 Run 的起点（切 Run 时调用）。"""
        self.used = Usage(wall_time_ms=int(wall_time_ms))
        return self.used

    def remaining(self) -> dict[str, float]:
        """四维剩余量；无上限的维度返回 `math.inf`（不臆造 0）。"""
        return {
            "steps": float(self.limits.max_steps - self.used.steps),
            "tokens": float(self.limits.max_tokens - self.used.tokens),
            "cost": float(self.limits.max_cost - self.used.cost),
            "wall_time_s": float(self.limits.max_wall_time_s - self.used.wall_time_s),
        }

    # --- 限额判定 ---
    def exhausted(self) -> StopReason | None:
        """返回首个耗尽的维度对应停止原因；均未耗尽返回 `None`。

        判定顺序与 8 条停止规则的登记顺序一致（steps → tokens → cost → wall_time）。
        """
        if self.used.steps >= self.limits.max_steps:
            return StopReason.MAX_STEPS
        if self.used.tokens >= self.limits.max_tokens:
            return StopReason.MAX_TOKENS
        if self.used.cost >= self.limits.max_cost:
            return StopReason.MAX_COST
        if self.used.wall_time_s >= self.limits.max_wall_time_s:
            return StopReason.MAX_WALL_TIME
        return None

    def guard(self) -> None:
        """耗尽即抛 `BudgetExhausted`（供「预算耗尽 → 立即中断」的强约束调用点）。"""
        reason = self.exhausted()
        if reason is not None:
            raise BudgetExhausted(
                reason,
                f"预算耗尽（{reason}）：已用 steps={self.used.steps} tokens={self.used.tokens} "
                f"cost={self.used.cost:.4f} wall={self.used.wall_time_s:.1f}s",
                usage=self.used,
            )

    # --- 供 Hook / 可观测 ---
    def to_hook_budget(self) -> dict[str, float]:
        """`ctx.extra["budget"]` 的取值——让 M1 预留的 `BudgetCheckHook` 正式生效。"""
        return self.limits.as_hook_budget()

    def snapshot(self) -> dict[str, float]:
        """可观测快照：已用 + 剩余 + 限额（供 Trace / M6 成本看板）。"""
        return {
            "steps_used": float(self.used.steps),
            "tokens_used": float(self.used.tokens),
            "cost_used": float(self.used.cost),
            "wall_time_s_used": float(self.used.wall_time_s),
            "max_steps": float(self.limits.max_steps),
            "max_tokens": float(self.limits.max_tokens),
            "max_cost": float(self.limits.max_cost),
            "max_wall_time_s": float(self.limits.max_wall_time_s),
            "unlimited": math.inf in self.remaining().values(),
        }

"""可观测层的计价与汇总（03_BUILD_PLAN §3 M6：`observ/cost.py`）。

**只做事后计价与汇总查询**（`/api/runs/{id}/cost`）：实时限额判定在
`harness/budget.py`（`BudgetManager`），二者职责不重叠。

口径唯一来源（HANDOFF §6.2）：`LlmCallRepository.totals_for_run(run_id)`——
本模块**复用同一聚合，不另建计量**；`runs.cost_total` / `tokens_total`（由
`CostSettleHook` 覆盖写）与这里的结果必然一致。

**确定性 / 离线**：只读本地库，不触网、不调 LLM。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.store.repo import LlmCallRepository

__all__ = ["CostSummary", "CostReporter"]


@dataclass(slots=True)
class CostSummary:
    """一次 Run 的成本汇总（契约：`CostReporter.summary(run_id) -> CostSummary`）。"""

    run_id: str
    cost_total: float = 0.0
    tokens_total: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    failed_calls: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "cost_total": self.cost_total,
            "tokens_total": self.tokens_total,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_calls": self.llm_calls,
            "failed_calls": self.failed_calls,
            "by_model": self.by_model,
        }


class CostReporter:
    """成本汇总查询面（唯一口径：`llm_calls` 聚合）。"""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._repo = LlmCallRepository(session)

    def summary(self, run_id: str) -> CostSummary:
        """汇总本次 Run 的成本与 token。

        口径说明：`cost_total` / `tokens_total` **逐字取自
        `LlmCallRepository.totals_for_run`**（仅统计 `status == "OK"` 的成功调用）；
        `by_model` 同样只计成功调用，保证 `Σ by_model.cost == cost_total`
        （否则看板会出现「分项之和 ≠ 总计」的口径裂缝）。
        """
        cost_total, tokens_total = self._repo.totals_for_run(run_id)
        calls = self._repo.list_by_run(run_id)

        summary = CostSummary(run_id=run_id, cost_total=float(cost_total), tokens_total=int(tokens_total))
        for call in calls:
            status = str(getattr(call, "status", "") or "")
            if status != "OK":
                summary.failed_calls += 1
                continue
            prompt = int(getattr(call, "prompt_tokens", 0) or 0)
            completion = int(getattr(call, "completion_tokens", 0) or 0)
            summary.llm_calls += 1
            summary.prompt_tokens += prompt
            summary.completion_tokens += completion
            bucket = summary.by_model.setdefault(
                str(getattr(call, "model", "") or ""),
                {"calls": 0.0, "tokens": 0.0, "cost": 0.0},
            )
            bucket["calls"] += 1
            bucket["tokens"] += float(getattr(call, "total_tokens", 0) or 0)
            bucket["cost"] += float(getattr(call, "cost", 0.0) or 0.0)
        return summary

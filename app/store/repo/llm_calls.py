"""llm_calls 表 CRUD（LLM 调用审计）。

归属 M1（`harness/model_router.py` 的落库端）。
口径（HANDOFF §6.2 / 架构 §4.1）：`runs.cost_total` / `runs.tokens_total` 的**唯一数据来源是
本表**，不靠内存累计——因此 `totals_for_run()` 是成本结算的唯一入口。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.domain import new_id, now_ms
from app.store.schema import LlmCallRow

from .base import BaseRepository

__all__ = ["LlmCallRepository"]


class LlmCallRepository(BaseRepository[LlmCallRow]):
    model = LlmCallRow

    def create(
        self,
        *,
        run_id: str,
        provider: str,
        model: str,
        status: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: float = 0.0,
        latency_ms: int | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        error_code: int | None = None,
        request_ref: str | None = None,
    ) -> LlmCallRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            request_ref=request_ref,
            created_at=now_ms(),
        )

    def list_by_run(self, run_id: str) -> list[LlmCallRow]:
        stmt = (
            select(LlmCallRow)
            .where(LlmCallRow.run_id == run_id)
            .order_by(LlmCallRow.created_at)
        )
        return list(self.session.scalars(stmt).all())

    def totals_for_run(self, run_id: str) -> tuple[float, int]:
        """返回 `(cost_total, tokens_total)`；仅统计 `status == "OK"` 的成功调用。"""
        stmt = (
            select(
                func.coalesce(func.sum(LlmCallRow.cost), 0.0),
                func.coalesce(func.sum(LlmCallRow.total_tokens), 0),
            )
            .where(LlmCallRow.run_id == run_id, LlmCallRow.status == "OK")
        )
        cost, tokens = self.session.execute(stmt).one()
        return float(cost or 0.0), int(tokens or 0)

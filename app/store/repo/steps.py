"""steps 表 CRUD。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import new_id, now_ms
from app.store.schema import StepRow

from .base import BaseRepository

__all__ = ["StepRepository"]


class StepRepository(BaseRepository[StepRow]):
    model = StepRow

    def create(
        self,
        *,
        task_id: str,
        seq: int,
        type: str,
        payload_json: dict[str, Any] | None = None,
        llm_span_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> StepRow:
        return self.add(
            id=new_id(),
            task_id=task_id,
            seq=seq,
            type=type,
            payload_json=dict(payload_json or {}),
            llm_span_id=llm_span_id,
            tool_call_id=tool_call_id,
            created_at=now_ms(),
        )

    def list_by_task(self, task_id: str) -> list[StepRow]:
        stmt = select(StepRow).where(StepRow.task_id == task_id).order_by(StepRow.seq)
        return list(self.session.scalars(stmt).all())

"""traces 表 CRUD（Span 扁平存储）。

归属 M1（时序硬约束：Tracer 埋点必须在 M1 写 AgentLoop 时同步完成，见 03_BUILD_PLAN §2 / R-8）。
落库口径（架构 §12.2）：扁平存 + `parent_span_id` 指针，前端组树。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain import new_id, now_ms
from app.store.schema import TraceRow

from .base import BaseRepository

__all__ = ["TraceRepository"]


class TraceRepository(BaseRepository[TraceRow]):
    model = TraceRow

    def create(
        self,
        *,
        run_id: str,
        span_id: str,
        name: str,
        parent_span_id: str | None = None,
        attrs_json: dict[str, Any] | None = None,
        started_at: int | None = None,
        duration_ms: int | None = None,
        status: str = "OK",
    ) -> TraceRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            attrs_json=dict(attrs_json or {}),
            started_at=started_at if started_at is not None else now_ms(),
            duration_ms=duration_ms,
            status=status,
        )

    def list_by_run(self, run_id: str) -> list[TraceRow]:
        stmt = (
            select(TraceRow).where(TraceRow.run_id == run_id).order_by(TraceRow.started_at)
        )
        return list(self.session.scalars(stmt).all())

    def count_by_run(self, run_id: str) -> int:
        stmt = select(func.count()).select_from(TraceRow).where(TraceRow.run_id == run_id)
        return int(self.session.scalar(stmt) or 0)

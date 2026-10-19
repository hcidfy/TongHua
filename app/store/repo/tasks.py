"""tasks 表 CRUD。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import new_id
from app.store.schema import TaskRow

from .base import BaseRepository

__all__ = ["TaskRepository"]


class TaskRepository(BaseRepository[TaskRow]):
    model = TaskRow

    def create(
        self,
        *,
        run_id: str,
        seq: int,
        title: str,
        intent: str,
        status: str = "PENDING",
        deps_json: list[Any] | None = None,
    ) -> TaskRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            seq=seq,
            title=title,
            intent=intent,
            status=status,
            deps_json=list(deps_json or []),
            attempts=0,
            evidence_count=0,
        )

    def list_by_run(self, run_id: str) -> list[TaskRow]:
        stmt = select(TaskRow).where(TaskRow.run_id == run_id).order_by(TaskRow.seq)
        return list(self.session.scalars(stmt).all())

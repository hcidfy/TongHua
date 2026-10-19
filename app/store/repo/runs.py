"""runs 表 CRUD。"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.models import new_id, now_ms
from app.store.schema import RunRow

from .base import BaseRepository

__all__ = ["RunRepository"]


class RunRepository(BaseRepository[RunRow]):
    model = RunRow

    def create(
        self,
        *,
        thread_id: str,
        status: str = "CREATED",
        parent_run_id: str | None = None,
        resume_from_event_id: str | None = None,
        started_at: int | None = None,
    ) -> RunRow:
        return self.add(
            id=new_id(),
            thread_id=thread_id,
            status=status,
            parent_run_id=parent_run_id,
            resume_from_event_id=resume_from_event_id,
            started_at=started_at if started_at is not None else now_ms(),
        )

    def list_by_thread(self, thread_id: str) -> list[RunRow]:
        return self.list(thread_id=thread_id)

    def list_resumable(self) -> list[RunRow]:
        """供 `ResumeEngine` 扫描：非终态且未结束的 Run（架构 §6.1）。"""
        terminal = ("COMPLETED", "STOPPED", "FAILED")
        stmt = (
            select(RunRow)
            .where(RunRow.status.not_in(terminal), RunRow.ended_at.is_(None))
            .order_by(RunRow.started_at)
        )
        return list(self.session.scalars(stmt).all())

    def update_status(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        error: str | None = None,
        ended_at: int | None = None,
    ) -> RunRow | None:
        run = self.get(run_id)
        if run is None:
            return None
        run.status = status
        if stop_reason is not None:
            run.stop_reason = stop_reason
        if error is not None:
            run.error = error
        if ended_at is not None:
            run.ended_at = ended_at
        self.session.flush()
        return run

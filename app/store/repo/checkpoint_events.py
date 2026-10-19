"""checkpoint_events 表 CRUD（事件溯源写入端）。

`append()` 签名在 M0 就定死——M1 写 AgentLoop 时直接复用，避免返工（HANDOFF §7 前置提醒）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.models import new_id, now_ms
from app.store.schema import CheckpointEventRow

from .base import BaseRepository

__all__ = ["CheckpointEventRepository"]


class CheckpointEventRepository(BaseRepository[CheckpointEventRow]):
    model = CheckpointEventRow

    def next_seq(self, run_id: str) -> int:
        current = self.session.scalar(
            select(func.max(CheckpointEventRow.seq)).where(CheckpointEventRow.run_id == run_id)
        )
        return int(current or 0) + 1

    def append(
        self,
        run_id: str,
        event_type: str,
        payload_json: dict[str, Any] | None = None,
    ) -> CheckpointEventRow:
        """追加一条事件；`seq` 由本仓库保证单调递增（配合 UNIQUE(run_id, seq)）。"""
        return self.add(
            id=new_id(),
            run_id=run_id,
            seq=self.next_seq(run_id),
            event_type=event_type,
            payload_json=dict(payload_json or {}),
            created_at=now_ms(),
        )

    def list_by_run(self, run_id: str, *, after_seq: int = 0) -> list[CheckpointEventRow]:
        """按 seq 升序返回事件（`after_seq` 支持增量重放）。"""
        stmt = (
            select(CheckpointEventRow)
            .where(CheckpointEventRow.run_id == run_id, CheckpointEventRow.seq > after_seq)
            .order_by(CheckpointEventRow.seq)
        )
        return list(self.session.scalars(stmt).all())

    def last(self, run_id: str) -> CheckpointEventRow | None:
        stmt = (
            select(CheckpointEventRow)
            .where(CheckpointEventRow.run_id == run_id)
            .order_by(CheckpointEventRow.seq.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

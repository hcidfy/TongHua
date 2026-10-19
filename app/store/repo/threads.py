"""threads 表 CRUD。"""

from __future__ import annotations

from app.domain.models import new_id, now_ms
from app.store.schema import ThreadRow

from .base import BaseRepository

__all__ = ["ThreadRepository"]


class ThreadRepository(BaseRepository[ThreadRow]):
    model = ThreadRow

    def create(self, *, title: str, goal: str, status: str = "ACTIVE") -> ThreadRow:
        ts = now_ms()
        return self.add(
            id=new_id(), title=title, goal=goal, status=status, created_at=ts, updated_at=ts
        )

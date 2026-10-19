"""memories 表 CRUD（分层记忆读写端）。

归属 M1 的 `MemoryWriteHook` 落点（03_BUILD_PLAN §3 M1 post 钩子 ③）。
**召回打分（关键词 + 时间衰减）归属 M4 的 `harness/memory.py`**，本文件只做 CRUD。
"""

from __future__ import annotations

from app.domain import MemoryScope, new_id, now_ms
from app.store.schema import MemoryRow

from .base import BaseRepository

__all__ = ["MemoryRepository"]


class MemoryRepository(BaseRepository[MemoryRow]):
    model = MemoryRow

    def create(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        thread_id: str | None = None,
        run_id: str | None = None,
        embedding_ref: str | None = None,
    ) -> MemoryRow:
        return self.add(
            id=new_id(),
            scope=scope,
            thread_id=thread_id,
            run_id=run_id,
            kind=kind,
            content=content,
            embedding_ref=embedding_ref,
            created_at=now_ms(),
        )

    def list_by_thread(self, thread_id: str, *, scope: str | None = None) -> list[MemoryRow]:
        filters: dict[str, object] = {"thread_id": thread_id}
        if scope is not None:
            filters["scope"] = scope
        return self.list(**filters)

    def list_by_run(self, run_id: str) -> list[MemoryRow]:
        return self.list(run_id=run_id)

    @staticmethod
    def scopes() -> tuple[str, ...]:
        """可选 scope 取值（契约来自 `MemoryScope`，此处只做转发避免调用方重复导入）。"""
        return tuple(str(s) for s in MemoryScope)

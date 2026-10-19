"""ui_blocks 表 CRUD（M5 补；03_BUILD_PLAN §3 M5）。

动态 UI 增量块按 `seq`（Run 内**单调递增**）落库，前端按序插入（架构 §11.3-3）。
`render_status` 默认 `"ok"`；校验失败 / 未实现组件落 `"failed"`（由 C19 UIRenderer 决定，
架构 §11.3-1）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.models import new_id, now_ms
from app.store.schema import UIBlockRow

from .base import BaseRepository

__all__ = ["UIBlockRepository"]


class UIBlockRepository(BaseRepository[UIBlockRow]):
    model = UIBlockRow

    def create(
        self,
        *,
        run_id: str,
        source_type: str,
        component: str,
        props_json: dict[str, Any] | None = None,
        refs_json: list[Any] | None = None,
        render_status: str = "ok",
        seq: int | None = None,
        block_id: str | None = None,
        created_at: int | None = None,
    ) -> UIBlockRow:
        return self.add(
            id=block_id or new_id(),
            run_id=run_id,
            seq=self.next_seq(run_id) if seq is None else int(seq),
            source_type=str(source_type),
            component=str(component),
            props_json=dict(props_json or {}),
            refs_json=list(refs_json or []),
            render_status=str(render_status),
            created_at=created_at if created_at is not None else now_ms(),
        )

    def next_seq(self, run_id: str) -> int:
        """Run 内下一个可用 `seq`（`max+1`；无块时从 1 开始）。"""
        stmt = select(func.max(UIBlockRow.seq)).where(UIBlockRow.run_id == run_id)
        current = self.session.scalar(stmt)
        return int(current or 0) + 1

    def list_by_run(self, run_id: str, *, after_seq: int | None = None) -> list[UIBlockRow]:
        """按 `seq` 升序返回；`after_seq` 用于 SSE 断线续传（只推增量）。"""
        stmt = select(UIBlockRow).where(UIBlockRow.run_id == run_id)
        if after_seq is not None:
            stmt = stmt.where(UIBlockRow.seq > int(after_seq))
        return list(self.session.scalars(stmt.order_by(UIBlockRow.seq)).all())

    def count_by_run(self, run_id: str) -> int:
        return self.count(run_id=run_id)

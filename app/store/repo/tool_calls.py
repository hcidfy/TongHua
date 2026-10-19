"""tool_calls 表 CRUD（含 `dedupe_key` 幂等查询）。

归属 M1（`harness/tool_executor.py` 的审计与幂等端）。
`dedupe_key` UNIQUE 是 M3 恢复时「不重复调用工具」的唯一依据（架构 §6.2、HANDOFF §6.1）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain import new_id, now_ms
from app.store.schema import ToolCallRow

from .base import BaseRepository

__all__ = ["ToolCallRepository"]


class ToolCallRepository(BaseRepository[ToolCallRow]):
    model = ToolCallRow

    def create(
        self,
        *,
        run_id: str,
        task_id: str,
        tool_name: str,
        args_json: dict[str, Any] | None,
        dedupe_key: str,
        idempotent: bool = True,
        status: str = "PENDING",
    ) -> ToolCallRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            task_id=task_id,
            tool_name=tool_name,
            args_json=dict(args_json or {}),
            status=status,
            attempts=0,
            idempotent=idempotent,
            dedupe_key=dedupe_key,
            created_at=now_ms(),
        )

    def get_by_dedupe_key(self, dedupe_key: str) -> ToolCallRow | None:
        return self.first(dedupe_key=dedupe_key)

    def list_by_run(self, run_id: str) -> list[ToolCallRow]:
        stmt = (
            select(ToolCallRow)
            .where(ToolCallRow.run_id == run_id)
            .order_by(ToolCallRow.created_at)
        )
        return list(self.session.scalars(stmt).all())

    def list_by_task(self, task_id: str) -> list[ToolCallRow]:
        return self.list(task_id=task_id)

    def mark(
        self,
        call_id: str,
        *,
        status: str,
        attempts: int | None = None,
        latency_ms: int | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> ToolCallRow | None:
        """更新调用状态；只覆盖显式传入的字段，未传的保持原值。"""
        row = self.get(call_id)
        if row is None:
            return None
        row.status = status
        if attempts is not None:
            row.attempts = attempts
        if latency_ms is not None:
            row.latency_ms = latency_ms
        if error_code is not None:
            row.error_code = error_code
        if error_message is not None:
            row.error_message = error_message
        self.session.flush()
        return row

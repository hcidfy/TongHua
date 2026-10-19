"""approvals 表 CRUD（HITL 审批）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import new_id, now_ms
from app.store.schema import ApprovalRow

from .base import BaseRepository

__all__ = ["ApprovalRepository"]


class ApprovalRepository(BaseRepository[ApprovalRow]):
    model = ApprovalRow

    def create(
        self,
        *,
        run_id: str,
        type: str,
        payload_json: dict[str, Any] | None = None,
        status: str = "PENDING",
        requested_at: int | None = None,
    ) -> ApprovalRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            type=type,
            payload_json=dict(payload_json or {}),
            status=status,
            requested_at=requested_at if requested_at is not None else now_ms(),
            decided_at=None,
            decided_by=None,
        )

    def list_by_run(self, run_id: str) -> list[ApprovalRow]:
        stmt = (
            select(ApprovalRow)
            .where(ApprovalRow.run_id == run_id)
            .order_by(ApprovalRow.requested_at)
        )
        return list(self.session.scalars(stmt).all())

    def list_pending(self, *, run_id: str | None = None) -> list[ApprovalRow]:
        stmt = select(ApprovalRow).where(ApprovalRow.status == "PENDING")
        if run_id is not None:
            stmt = stmt.where(ApprovalRow.run_id == run_id)
        return list(self.session.scalars(stmt.order_by(ApprovalRow.requested_at)).all())

    def latest_pending(self, run_id: str) -> ApprovalRow | None:
        rows = self.list_pending(run_id=run_id)
        return rows[-1] if rows else None

    def decide(
        self,
        approval_id: str,
        status: str,
        *,
        decided_by: str | None = None,
        decided_at: int | None = None,
    ) -> ApprovalRow | None:
        """落审批结论（`GRANTED` / `REJECTED` / `EXPIRED`）。"""
        row = self.get(approval_id)
        if row is None:
            return None
        row.status = str(status)
        row.decided_at = decided_at if decided_at is not None else now_ms()
        row.decided_by = decided_by
        self.session.flush()
        return row

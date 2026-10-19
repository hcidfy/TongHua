"""`api/approvals.py`：审批列表与决定（03_BUILD_PLAN §3 M5）。

对应架构 §13：`GET /api/approvals?status=PENDING`、`POST /api/approvals/{id}/decide`。

**逻辑全部调用 M3 的 `HITLGate`**（03_BUILD_PLAN §3 M5 原文「归属 M5，逻辑调用 M3 的
`HITLGate`」）：本文件只做 HTTP 适配——不自己改 `approvals` 行、不自己迁 Run 状态，
因此「审批同意 → 回 `EXECUTING`」「审批拒绝 / 过期 → 置 `STOPPED`」的口径与 M3 唯一一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.harness.hitl import ApprovalTimeout, HITLGate
from app.store.repo import ApprovalRepository, RunRepository
from app.store.schema import ApprovalRow

from .common import ApprovalDecision, approval_dict, get_app_settings, get_db, not_found

__all__ = ["router"]

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

#: `status=ALL` 的别名（列表要「全部」时用）。
_ALL = {"ALL", "*", ""}


@router.get("")
def list_approvals(
    status: str = "PENDING",
    run_id: str | None = None,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """审批列表：默认只列 `PENDING`（前端审批面板的默认视图）。

    待决列表走 `ApprovalRepository.list_pending`（M3 已落地，「刷新页面状态不丢」的入口）；
    其他状态由本层按 `status` 过滤。
    """
    repo = ApprovalRepository(session)
    wanted = str(status).strip().upper()
    if wanted in _ALL:
        stmt = select(ApprovalRow)
        if run_id is not None:
            stmt = stmt.where(ApprovalRow.run_id == run_id)
        rows = list(session.scalars(stmt.order_by(ApprovalRow.requested_at)).all())
        wanted = "ALL"
    elif wanted == "PENDING":
        rows = repo.list_pending(run_id=run_id)
    elif run_id is not None:
        rows = [row for row in repo.list_by_run(run_id) if str(row.status) == wanted]
    else:
        stmt = select(ApprovalRow).where(ApprovalRow.status == wanted)
        rows = list(session.scalars(stmt.order_by(ApprovalRow.requested_at)).all())
    return {"status": wanted, "count": len(rows), "approvals": [approval_dict(r) for r in rows]}


@router.get("/{approval_id}")
def get_approval(approval_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """单条审批详情（含 `payload`：审批要看的上下文）。"""
    row = ApprovalRepository(session).get(approval_id)
    if row is None:
        raise not_found("审批", approval_id)
    return approval_dict(row)


@router.post("/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """裁决审批（`GRANTED` / `REJECTED` / `EXPIRED`，别名由 `HITLGate` 归一）。

    错误口径：
    - 审批不存在 → `404`；
    - 已过期后仍裁决 → `409`（`ApprovalTimeout`）；
    - 结论取值非法 → `422`。
    """
    gate = HITLGate(session, settings=settings)
    try:
        row = gate.resume(
            approval_id,
            payload.decision,
            decided_by=payload.decided_by,
            trigger="api",
        )
    except ApprovalTimeout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise not_found("审批", approval_id)
    data = approval_dict(row)
    run = RunRepository(session).get(row.run_id)
    data["run_status"] = str(getattr(run, "status", "")) or None
    return data

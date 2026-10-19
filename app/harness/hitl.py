"""Harness 内核：人工审批闸门（HITL；架构 §6.1 第 2 类中断；03_BUILD_PLAN §3 M3）。

契约（03_BUILD_PLAN §3 M3 接口契约）::

    HITLGate.request(run_id, type, payload) -> approval_id
    HITLGate.resume(approval_id, decision)

职责：**审批挂起（冻结 Loop）→ 恢复 / 超时过期**。

挂起语义（「冻结 Loop」的落地方式）：
- `request()` 落一条 `approvals` 行（`PENDING`）+ 事件 `ApprovalRequested`，
  并把 Run 迁到 `AWAITING_APPROVAL`（同时落 `RunPaused`）；
- 调用方（`Pipeline`）看到 `AWAITING_APPROVAL` 就**不再推进任何 Task**，
  直接带着 `RunResult(status="AWAITING_APPROVAL")` 返回——进程可以退出，
  状态全在 DB 里，因此「挂起期间刷新页面状态不丢」；
- `resume(approval_id, decision)` 落结论：`GRANTED` → Run 回 `EXECUTING`（事件 `RunResumed`）；
  `REJECTED` / 超时 `EXPIRED` → Run 置 `STOPPED`（不静默继续跑）。

超时：`approval_timeout_s`（默认 1800s / 30min）。过期判定不靠后台线程，而是
**在 `expire_due()` 被调用时结算**（`ResumeEngine.scan_orphans()` 会先调用它），
避免为一个 24h 项目引入调度器。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain import ApprovalStatus, CheckpointEventType, RunStatus, now_ms
from app.store.repo import ApprovalRepository, RunRepository

from .checkpoint import CheckpointStore
from .state_machine import StateMachine

__all__ = ["HITLGate", "ApprovalTimeout"]

#: 审批类型（`approvals.type`）：计划审批 / 高成本工具审批 / 收口前复核。
APPROVAL_TYPES: tuple[str, ...] = ("plan", "expensive_tool", "final_review")


class ApprovalTimeout(RuntimeError):
    """审批已过期（超时结算后仍被 resume）。"""


def _normalize_decision(decision: str | ApprovalStatus) -> ApprovalStatus:
    if isinstance(decision, ApprovalStatus):
        return decision
    key = str(decision).strip().upper()
    aliases = {
        "APPROVE": ApprovalStatus.GRANTED,
        "GRANT": ApprovalStatus.GRANTED,
        "GRANTED": ApprovalStatus.GRANTED,
        "OK": ApprovalStatus.GRANTED,
        "DENY": ApprovalStatus.REJECTED,
        "REJECT": ApprovalStatus.REJECTED,
        "REJECTED": ApprovalStatus.REJECTED,
        "EXPIRE": ApprovalStatus.EXPIRED,
        "EXPIRED": ApprovalStatus.EXPIRED,
    }
    if key not in aliases:
        raise ValueError(f"未知审批结论：{decision!r}（可选 GRANTED / REJECTED / EXPIRED）")
    return aliases[key]


class HITLGate:
    """审批闸门：请求 / 恢复 / 超时过期。"""

    def __init__(
        self,
        session: Session | None = None,
        *,
        approvals: ApprovalRepository | None = None,
        runs: RunRepository | None = None,
        checkpoints: CheckpointStore | None = None,
        state_machine: StateMachine | None = None,
        settings: Settings | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._approvals = approvals or (ApprovalRepository(session) if session is not None else None)
        self._runs = runs or (RunRepository(session) if session is not None else None)
        self._store = checkpoints or (CheckpointStore(session) if session is not None else None)
        self._sm = state_machine or (StateMachine(session) if session is not None else None)
        self.timeout_s = float(
            timeout_s
            if timeout_s is not None
            else getattr(self.settings, "approval_timeout_s", 1800.0) or 1800.0
        )

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def request(
        self,
        run_id: str,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        reason: str | None = None,
        trigger: str = "hitl",
    ) -> str:
        """发起一次审批并**冻结**该 Run；返回 `approval_id`。"""
        if self._approvals is None:
            raise RuntimeError("HITLGate 需要 session / ApprovalRepository 才能 request")
        row = self._approvals.create(
            run_id=run_id,
            type=str(type),
            payload_json=dict(payload or {}),
        )
        self._emit(
            run_id,
            CheckpointEventType.ApprovalRequested,
            {
                "approval_id": row.id,
                "type": row.type,
                "payload": dict(row.payload_json),
                "timeout_s": self.timeout_s,
            },
        )
        # 冻结 Loop：Run 迁到 AWAITING_APPROVAL（落 RunPaused）
        run = self._runs.get(run_id) if self._runs is not None else None
        if run is not None and self._sm is not None:
            self._sm.apply_run(
                run,
                RunStatus.AWAITING_APPROVAL,
                reason=reason or f"等待人工审批（{type}）",
                trigger=trigger,
            )
        return row.id

    def resume(
        self,
        approval_id: str,
        decision: str | ApprovalStatus,
        *,
        decided_by: str | None = None,
        trigger: str = "hitl",
    ) -> Any:
        """落审批结论并解除冻结。

        - `GRANTED` → Run 回 `EXECUTING`（事件 `RunResumed`），调用方可续跑；
        - `REJECTED` / `EXPIRED` → Run 置 `STOPPED`（不静默继续）。

        返回更新后的 `ApprovalRow`；审批不存在返回 `None`。
        """
        if self._approvals is None:
            raise RuntimeError("HITLGate 需要 session / ApprovalRepository 才能 resume")
        row = self._approvals.get(approval_id)
        if row is None:
            return None
        verdict = _normalize_decision(decision)
        if row.status == str(ApprovalStatus.EXPIRED) and verdict is not ApprovalStatus.EXPIRED:
            raise ApprovalTimeout(f"审批 {approval_id} 已过期，不能再裁决为 {verdict}")

        row = self._approvals.decide(
            approval_id, str(verdict), decided_by=decided_by
        )
        self._emit(
            row.run_id,
            CheckpointEventType.ApprovalGranted
            if verdict is ApprovalStatus.GRANTED
            else CheckpointEventType.ApprovalRejected,
            {
                "approval_id": row.id,
                "type": row.type,
                "status": str(verdict),
                "decided_by": decided_by,
            },
        )
        self._release(row.run_id, verdict, trigger=trigger)
        return row

    # ------------------------------------------------------------------ #
    # 超时过期
    # ------------------------------------------------------------------ #
    def expire_due(self, *, now: int | None = None) -> list[str]:
        """把超时仍 `PENDING` 的审批结算为 `EXPIRED`，返回过期的 `approval_id` 列表。"""
        if self._approvals is None:
            return []
        stamp = now if now is not None else now_ms()
        expired: list[str] = []
        for row in self._approvals.list_pending():
            if stamp - int(row.requested_at) < self.timeout_s * 1000:
                continue
            self.resume(row.id, ApprovalStatus.EXPIRED, decided_by="system:timeout")
            expired.append(row.id)
        return expired

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def pending(self, run_id: str | None = None) -> list[Any]:
        if self._approvals is None:
            return []
        return self._approvals.list_pending(run_id=run_id)

    def status(self, approval_id: str) -> str | None:
        if self._approvals is None:
            return None
        row = self._approvals.get(approval_id)
        return str(row.status) if row is not None else None

    def is_pending(self, approval_id: str) -> bool:
        return self.status(approval_id) == str(ApprovalStatus.PENDING)

    def awaiting(self, run_id: str) -> bool:
        """该 Run 是否正被冻结（有待决审批）。——「刷新页面状态不丢」的判定入口。"""
        return bool(self.pending(run_id=run_id))

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _release(self, run_id: str, verdict: ApprovalStatus, *, trigger: str) -> None:
        run = self._runs.get(run_id) if self._runs is not None else None
        if run is None or self._sm is None:
            return
        if verdict is ApprovalStatus.GRANTED:
            self._sm.apply_run(
                run,
                RunStatus.EXECUTING,
                reason="审批通过，恢复执行",
                trigger=trigger,
            )
        else:
            reason = (
                "审批超时过期"
                if verdict is ApprovalStatus.EXPIRED
                else "审批被拒"
            )
            self._sm.apply_run(
                run,
                RunStatus.STOPPED,
                reason=reason,
                trigger=trigger,
            )

    def _emit(self, run_id: str, event_type: CheckpointEventType, payload: dict[str, Any]) -> None:
        if self._store is not None:
            self._store.append(run_id, (str(event_type), payload))

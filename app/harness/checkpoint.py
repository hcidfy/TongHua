"""Harness 内核：检查点（事件追加 + 快照裁剪；03_BUILD_PLAN §3 M3）。

契约（03_BUILD_PLAN §3 M3 接口契约）::

    CheckpointStore.append(run_id, event) / load(run_id) -> EventStream

职责（两件事）：
1. **事件追加**——`seq` 由 `CheckpointEventRepository.next_seq()` 保证在单 Run 内单调递增
   （DB 侧另有 `UNIQUE(run_id, seq)` 兜底，见 `app/store/schema.py`）；
2. **快照裁剪**——把事件流按每 `snapshot_every` 条折叠成一份 `Snapshot`（内存态），
   `load()` 只回放**最后一个快照之后**的事件，避免恢复时从 seq=1 全量重放。

为什么快照只存内存：M0 冻结的 15 张表里没有 `snapshots` 表，凭空加表等于改契约；
事件流本身（`checkpoint_events`）就是**唯一事实来源**，快照是可再生派生物——
`Snapshot.state` 由 `fold_state()` 从事件重新算出，任何时刻都可重建，故不落库。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.domain import CheckpointEvent, now_ms
from app.store.repo import CheckpointEventRepository

__all__ = [
    "DEFAULT_SNAPSHOT_EVERY",
    "Snapshot",
    "EventStream",
    "CheckpointStore",
    "fold_state",
]

#: 每 N 条事件合并一次快照（架构未规定，取工程默认 20）。
DEFAULT_SNAPSHOT_EVERY = 20


# ---------------------------------------------------------------------- #
# 内存态折叠
# ---------------------------------------------------------------------- #
def _blank_state() -> dict[str, Any]:
    return {
        "run_status": None,
        "stop_reason": None,
        "goal": None,
        "plan_source": None,
        "plan_tasks": [],
        "task_statuses": {},
        "task_ids_by_seq": {},
        "completed_tasks": [],
        "failed_tasks": [],
        "degraded_tasks": [],
        "skipped_tasks": [],
        "evidence_ids": [],
        "approval_ids": [],
        "pending_approval_id": None,
        "paused": False,
        "paused_at_seq": None,
        "resumed_at_seq": None,
        "budget_exhausted": False,
        "stop_rule_hit": None,
        "guard_rejected": False,
        "last_event_type": None,
        "last_seq": 0,
    }


def _apply(state: dict[str, Any], event_type: str, payload: dict[str, Any], seq: int) -> None:
    """把一条事件折叠进内存态（对缺字段宽容：事件 schema 随里程碑演进）。"""
    task_id = payload.get("task_id")
    status = payload.get("status") or payload.get("to")
    if event_type == "RunStarted":
        state["goal"] = payload.get("goal") or state["goal"]
    elif event_type == "PlanGenerated":
        state["plan_source"] = payload.get("source") or state["plan_source"]
        # 任务清单的落点：管线写 `plan`（list），`tasks` 是**计数**（int）；
        # 早期/外部事件可能只给 `tasks` 列表——两种形态都要能折，否则事件重放会 TypeError。
        plan_tasks = payload.get("plan")
        if not isinstance(plan_tasks, list):
            candidate = payload.get("tasks")
            plan_tasks = candidate if isinstance(candidate, list) else []
        if plan_tasks:
            state["plan_tasks"] = list(plan_tasks)
    elif event_type == "TaskStarted":
        if task_id:
            state["task_statuses"][task_id] = "RUNNING"
            if payload.get("seq") is not None:
                state["task_ids_by_seq"][str(payload["seq"])] = task_id
    elif event_type in {"TaskCompleted", "TaskFailed", "TaskDegraded", "TaskSkipped"}:
        if task_id:
            state["task_statuses"][task_id] = str(status or event_type.removeprefix("Task").upper())
        bucket = {
            "TaskCompleted": "completed_tasks",
            "TaskFailed": "failed_tasks",
            "TaskDegraded": "degraded_tasks",
            "TaskSkipped": "skipped_tasks",
        }.get(event_type)
        if bucket and task_id and task_id not in state[bucket]:
            state[bucket].append(task_id)
    elif event_type == "EvidenceRecorded":
        eid = payload.get("evidence_id")
        if eid and eid not in state["evidence_ids"]:
            state["evidence_ids"].append(eid)
    elif event_type == "ApprovalRequested":
        approval_id = payload.get("approval_id")
        if approval_id:
            state["approval_ids"].append(approval_id)
            state["pending_approval_id"] = approval_id
    elif event_type in {"ApprovalGranted", "ApprovalRejected"}:
        if payload.get("approval_id") == state["pending_approval_id"]:
            state["pending_approval_id"] = None
    elif event_type == "RunPaused":
        state["paused"] = True
        state["paused_at_seq"] = seq
        state["run_status"] = "AWAITING_APPROVAL"
    elif event_type == "RunResumed":
        state["paused"] = False
        state["resumed_at_seq"] = seq
        state["run_status"] = "EXECUTING"
    elif event_type == "BudgetExhausted":
        state["budget_exhausted"] = True
        state["stop_reason"] = payload.get("reason") or state["stop_reason"]
    elif event_type == "StopRuleHit":
        state["stop_rule_hit"] = payload.get("reason") or state["stop_rule_hit"]
        state["stop_reason"] = payload.get("reason") or state["stop_reason"]
    elif event_type == "GuardRejected":
        state["guard_rejected"] = True
    elif event_type in {"RunCompleted", "RunFailed", "RunStopped"}:
        state["run_status"] = {
            "RunCompleted": "COMPLETED",
            "RunFailed": "FAILED",
            "RunStopped": "STOPPED",
        }[event_type]
        state["stop_reason"] = payload.get("stop_reason") or state["stop_reason"]

    # 状态机迁移事件（payload 带 from/to）：以 `to` 为最新状态（排除已处理的终态事件）
    if event_type not in {"RunCompleted", "RunFailed", "RunStopped", "RunPaused", "RunResumed"}:
        to_status = payload.get("to")
        if to_status and payload.get("entity") == "run":
            state["run_status"] = to_status

    state["last_event_type"] = event_type
    state["last_seq"] = seq


def fold_state(events: Iterable[Any], initial: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 seq 顺序把事件折叠成内存态（`initial` 支持从快照续折）。"""
    state = dict(initial) if initial else _blank_state()
    for event in events:
        _apply(
            state,
            str(getattr(event, "event_type", "")),
            dict(getattr(event, "payload_json", {}) or {}),
            int(getattr(event, "seq", 0)),
        )
    return state


# ---------------------------------------------------------------------- #
# 快照 / 事件流
# ---------------------------------------------------------------------- #
@dataclass(slots=True)
class Snapshot:
    """事件流的一个折叠点（内存派生，不落库）。"""

    run_id: str
    through_seq: int
    event_count: int
    state: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=now_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "through_seq": self.through_seq,
            "event_count": self.event_count,
            "state": dict(self.state),
        }


@dataclass(slots=True)
class EventStream:
    """一次 `load()` 的产物：快照 + 其后的增量事件。"""

    run_id: str
    events: list[Any] = field(default_factory=list)
    snapshot: Snapshot | None = None

    # --- 基本视图 ---
    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def first_seq(self) -> int:
        return int(getattr(self.events[0], "seq", 0)) if self.events else 0

    @property
    def last_seq(self) -> int:
        if self.events:
            return int(getattr(self.events[-1], "seq", 0))
        return self.snapshot.through_seq if self.snapshot else 0

    @property
    def types(self) -> list[str]:
        return [str(getattr(e, "event_type", "")) for e in self.events]

    def of_type(self, event_type: str) -> list[Any]:
        return [e for e in self.events if str(getattr(e, "event_type", "")) == event_type]

    def after(self, seq: int) -> "EventStream":
        return EventStream(
            run_id=self.run_id,
            events=[e for e in self.events if int(getattr(e, "seq", 0)) > seq],
            snapshot=self.snapshot,
        )

    def state(self) -> dict[str, Any]:
        """从「快照 + 增量事件」重建内存态（深拷贝，避免污染快照）。"""
        base = deepcopy(self.snapshot.state) if self.snapshot else None
        return fold_state(self.events, initial=base)


# ---------------------------------------------------------------------- #
# 检查点存储
# ---------------------------------------------------------------------- #
class CheckpointStore:
    """事件追加 + 快照裁剪的统一入口。"""

    def __init__(
        self,
        session: Session | None = None,
        *,
        repo: CheckpointEventRepository | None = None,
        snapshot_every: int = DEFAULT_SNAPSHOT_EVERY,
    ) -> None:
        self.session = session
        self._repo = repo or (CheckpointEventRepository(session) if session is not None else None)
        self.snapshot_every = max(1, int(snapshot_every))

    # --- 契约入口 ---
    def append(self, run_id: str, event: Any = None, **payload: Any) -> Any:
        """追加一条事件。

        `event` 可以是：
        - 领域对象 `CheckpointEvent`（用其 `type/payload`）；
        - `(event_type, payload)` 二元组；
        - 仅事件类型字符串（payload 走 `**payload`）。

        `seq` 由仓库保证单调（单 Run 内）。
        """
        event_type: str
        body: dict[str, Any]
        if isinstance(event, CheckpointEvent):
            event_type, body = str(event.type), dict(event.payload or {})
        elif isinstance(event, tuple):
            event_type, body = str(event[0]), dict(event[1] or {})
        elif isinstance(event, str):
            event_type, body = event, dict(payload)
        elif event is None:
            event_type, body = str(payload.pop("event_type", "")), dict(payload)
        else:  # 已是 Row / 任意带字段的对象
            event_type = str(getattr(event, "event_type", ""))
            body = dict(getattr(event, "payload_json", {}) or {})

        if self._repo is None:
            raise RuntimeError("CheckpointStore 需要 session / repo 才能 append")
        return self._repo.append(run_id, event_type, body)

    def load(self, run_id: str, *, after_seq: int = 0, clipped: bool = True) -> EventStream:
        """加载事件流；`clipped=True` 时只回放最后一个快照之后的增量事件。"""
        if self._repo is None:
            raise RuntimeError("CheckpointStore 需要 session / repo 才能 load")
        snapshot = self.latest_snapshot(run_id) if clipped else None
        floor = max(after_seq, snapshot.through_seq if snapshot else 0)
        events = self._repo.list_by_run(run_id, after_seq=floor)
        return EventStream(run_id=run_id, events=events, snapshot=snapshot)

    def tail(self, run_id: str, *, n: int = 5) -> EventStream:
        """取最后 n 条事件（供 Trace / 人工排障）。"""
        if self._repo is None:
            raise RuntimeError("CheckpointStore 需要 session / repo")
        events = self._repo.list_by_run(run_id)
        return EventStream(run_id=run_id, events=events[-n:] if n > 0 else [])

    # --- 快照裁剪 ---
    def snapshots(self, run_id: str) -> list[Snapshot]:
        """把事件流按 `snapshot_every` 折叠成若干快照（末尾不足一组的为最后一个）。"""
        if self._repo is None:
            raise RuntimeError("CheckpointStore 需要 session / repo")
        events = self._repo.list_by_run(run_id)
        return self._fold_snapshots(run_id, events)

    def latest_snapshot(self, run_id: str) -> Snapshot | None:
        snaps = self.snapshots(run_id)
        return snaps[-1] if snaps else None

    def _fold_snapshots(self, run_id: str, events: list[Any]) -> list[Snapshot]:
        snaps: list[Snapshot] = []
        state = _blank_state()
        chunk: list[Any] = []
        for event in events:
            chunk.append(event)
            if len(chunk) >= self.snapshot_every:
                state = fold_state(chunk, initial=state)
                snaps.append(
                    Snapshot(
                        run_id=run_id,
                        through_seq=int(getattr(chunk[-1], "seq", 0)),
                        event_count=len(chunk),
                        state=deepcopy(state),
                    )
                )
                chunk = []
        return snaps

    # --- 可观测 ---
    def snapshot(self, run_id: str) -> dict[str, Any]:
        """可观测摘要（供 HANDOFF 验收记录）。"""
        if self._repo is None:
            return {"run_id": run_id, "events": 0, "snapshots": 0}
        events = self._repo.list_by_run(run_id)
        snaps = self._fold_snapshots(run_id, events)
        return {
            "run_id": run_id,
            "events": len(events),
            "last_seq": int(getattr(events[-1], "seq", 0)) if events else 0,
            "snapshot_every": self.snapshot_every,
            "snapshots": len(snaps),
            "latest_snapshot_seq": snaps[-1].through_seq if snaps else 0,
        }

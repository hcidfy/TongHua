"""Harness 内核：会话与运行生命周期管理（架构 C1）。

契约（架构 §3.2 C1）::

    SessionManager.create_run / acquire_lane / release_lane

职责：
1. **Thread / Run 生命周期**：建线程、建 Run、推进 Run 状态（每次迁移由调用方落事件）。
2. **Lane 锁**：**同一 Thread 内的 Run 串行、跨 Thread 并行**（架构 §3.2 / C1）。
   实现为「按 `thread_id` 分桶的可重入锁」——同一线程内可重入（Pipeline 取锁后
   AgentLoop 再取不会自锁），不同 Thread 各自独立、互不阻塞。

> 本模块**只做生命周期与并发编排**；状态机的合法性校验（非法迁移拒绝）归属 M3 的
> `harness/state_machine.py`，此处只负责「写下去」。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.store.repo import RunRepository, ThreadRepository
from app.store.schema import RunRow, ThreadRow

__all__ = ["SessionManager", "LaneBusy"]


class LaneBusy(RuntimeError):
    """`acquire_lane(blocking=False)` 未能拿到 Lane（同 Thread 已有 Run 在跑）。"""


class SessionManager:
    """Thread / Run 生命周期 + Lane 锁。

    有 `session` 时可直接落库建线程/建 Run；无 `session` 时仅提供 Lane 编排
    （纯内存使用场景与单元测试）。
    """

    def __init__(self, session: Session | None = None) -> None:
        self._session = session
        self._threads = ThreadRepository(session) if session is not None else None
        self._runs = RunRepository(session) if session is not None else None
        #: 每个 Thread 一把可重入锁（跨 Thread 天然并行）。
        self._lanes: dict[str, threading.RLock] = {}
        #: 锁的重入深度（仅用于可观测 / 测试断言，不参与并发控制）。
        self._depth: dict[str, int] = {}
        self._lanes_guard = threading.Lock()

    # ------------------------------------------------------------------ #
    # Thread / Run 生命周期
    # ------------------------------------------------------------------ #
    def create_thread(self, *, title: str, goal: str, status: str = "ACTIVE") -> ThreadRow:
        """新建研究线程（`threads` 表）。"""
        if self._threads is None:
            raise LaneBusy("SessionManager 未绑定 session，无法创建 Thread")
        return self._threads.create(title=title, goal=goal, status=status)

    def create_run(
        self,
        *,
        thread_id: str,
        status: str = "CREATED",
        parent_run_id: str | None = None,
        resume_from_event_id: str | None = None,
    ) -> RunRow:
        """新建 Run（`runs` 表）；恢复场景传 `parent_run_id` / `resume_from_event_id`。"""
        if self._runs is None:
            raise LaneBusy("SessionManager 未绑定 session，无法创建 Run")
        return self._runs.create(
            thread_id=thread_id,
            status=status,
            parent_run_id=parent_run_id,
            resume_from_event_id=resume_from_event_id,
        )

    def get_run(self, run_id: str) -> RunRow | None:
        return self._runs.get(run_id) if self._runs is not None else None

    def set_run_status(self, run_id: str, *, status: str, **fields: object) -> RunRow | None:
        """推进 Run 状态（非法迁移的校验归 M3 的 state_machine）。"""
        if self._runs is None:
            return None
        return self._runs.update_status(run_id, status=status, **fields)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    # Lane 锁：同 Thread 串行、跨 Thread 并行
    # ------------------------------------------------------------------ #
    def _lane_for(self, thread_id: str) -> threading.RLock:
        with self._lanes_guard:
            lane = self._lanes.get(thread_id)
            if lane is None:
                lane = threading.RLock()
                self._lanes[thread_id] = lane
            return lane

    def acquire_lane(
        self, thread_id: str, *, blocking: bool = True, timeout: float | None = None
    ) -> bool:
        """占用某 Thread 的 Lane；返回是否拿到（`blocking=False` 时不等待）。"""
        lane = self._lane_for(thread_id)
        if blocking:
            acquired = lane.acquire(timeout=timeout) if timeout is not None else lane.acquire()
        else:
            acquired = lane.acquire(blocking=False)
        if acquired:
            with self._lanes_guard:
                self._depth[thread_id] = self._depth.get(thread_id, 0) + 1
        return bool(acquired)

    def release_lane(self, thread_id: str) -> None:
        """释放 Lane（与 `acquire_lane` 配对；重入深度归零才真正释放）。"""
        with self._lanes_guard:
            depth = self._depth.get(thread_id, 0)
            if depth <= 0:
                raise RuntimeError(f"Lane 未占用，无法释放：{thread_id}")
            self._depth[thread_id] = depth - 1
        self._lane_for(thread_id).release()

    @contextmanager
    def lane(
        self, thread_id: str, *, blocking: bool = True, timeout: float | None = None
    ) -> Iterator[bool]:
        """Lane 上下文管理器；`yield` 出是否拿到锁。

        `blocking=False` 且未拿到时不执行 `with` 体（调用方应据此直接拒绝并发启动）。
        """
        acquired = self.acquire_lane(thread_id, blocking=blocking, timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self.release_lane(thread_id)

    def is_locked(self, thread_id: str) -> bool:
        """某 Thread 的 Lane 是否被占用（并发编排的可观测 / 测试断言用）。"""
        with self._lanes_guard:
            return self._depth.get(thread_id, 0) > 0

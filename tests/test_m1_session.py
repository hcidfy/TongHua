"""M1：会话编排（架构 C1；03_BUILD_PLAN §3 M1）。

契约：**同一 Thread 内的 Run 串行、跨 Thread 并行**（Lane 锁 = 按 `thread_id` 分桶的可重入锁）。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy.orm import Session

from app.harness import LaneBusy, SessionManager


def test_m1_thread_and_run_lifecycle(session: Session) -> None:
    sm = SessionManager(session)
    thread = sm.create_thread(title="半导体板块研究", goal="半导体板块最近资金情况")
    assert thread.status == "ACTIVE"

    run = sm.create_run(thread_id=thread.id)
    assert run.status == "CREATED" and run.thread_id == thread.id

    fetched = sm.get_run(run.id)
    assert fetched is not None and fetched.id == run.id

    moved = sm.set_run_status(run.id, status="EXECUTING")
    assert moved is not None and moved.status == "EXECUTING"


def test_m1_session_manager_without_session_cannot_create_rows() -> None:
    sm = SessionManager()
    with pytest.raises(LaneBusy):
        sm.create_thread(title="t", goal="g")
    with pytest.raises(LaneBusy):
        sm.create_run(thread_id="whatever")


def test_m1_lane_is_reentrant_within_same_thread(session: Session) -> None:
    sm = SessionManager(session)
    thread = sm.create_thread(title="t", goal="g")

    assert sm.acquire_lane(thread.id) is True
    assert sm.is_locked(thread.id) is True
    # 同线程重入：Pipeline 取锁后 AgentLoop 再取不会自锁
    assert sm.acquire_lane(thread.id) is True

    sm.release_lane(thread.id)
    assert sm.is_locked(thread.id) is True  # 深度归零前仍占用
    sm.release_lane(thread.id)
    assert sm.is_locked(thread.id) is False


def test_m1_lane_release_without_acquire_raises(session: Session) -> None:
    sm = SessionManager(session)
    thread = sm.create_thread(title="t", goal="g")
    with pytest.raises(RuntimeError):
        sm.release_lane(thread.id)


def test_m1_lane_serializes_same_thread_but_parallelizes_across_threads(session: Session) -> None:
    sm = SessionManager(session)
    main = sm.create_thread(title="主线程", goal="g")
    other = sm.create_thread(title="另一线程", goal="g")

    denied: list[bool] = []

    def worker() -> None:
        # 另一个 OS 线程尝试占同一条 Lane → 必须被拒（同 Thread 串行）
        denied.append(sm.acquire_lane(main.id, blocking=False))

    with sm.lane(main.id) as acquired:
        assert acquired is True
        # 跨 Thread 并行：不互相阻塞
        assert sm.acquire_lane(other.id, blocking=False) is True
        sm.release_lane(other.id)

        handle = threading.Thread(target=worker)
        handle.start()
        handle.join(timeout=2)

    assert denied == [False]


def test_m1_lane_context_manager_releases_on_exit(session: Session) -> None:
    sm = SessionManager(session)
    thread = sm.create_thread(title="t", goal="g")

    with sm.lane(thread.id) as acquired:
        assert acquired is True and sm.is_locked(thread.id) is True
    assert sm.is_locked(thread.id) is False

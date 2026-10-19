"""M0 存储断言：建表 15 张、6 个 Repository CRUD、2 条 UNIQUE 真生效。"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.store.repo import (
    CheckpointEventRepository,
    EvidenceRepository,
    RunRepository,
    StepRepository,
    TaskRepository,
    ThreadRepository,
)
from app.store.schema import CheckpointEventRow, ToolCallRow


def test_m0_init_db_creates_15_tables(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == set(
        {
            "threads", "runs", "tasks", "steps", "tool_calls", "evidences", "artifacts",
            "ui_blocks", "checkpoint_events", "memories", "traces", "approvals",
            "tool_registry", "llm_calls", "run_metrics",
        }
    )


def test_m0_repo_crud_roundtrip(session: Session) -> None:
    thread = ThreadRepository(session).create(title="半导体板块", goal="评估半导体板块景气度")
    run = RunRepository(session).create(thread_id=thread.id, status="EXECUTING")
    task = TaskRepository(session).create(
        run_id=run.id, seq=1, title="取行情", intent="获取 600519.SH 快照", deps_json=[]
    )
    step = StepRepository(session).create(task_id=task.id, seq=1, type="ACT", payload_json={"a": 1})
    evidence = EvidenceRepository(session).create(
        run_id=run.id,
        task_id=task.id,
        source="fuyao",
        interface="/api/a-share/prices/snapshot",
        as_of=None,
        unit="CNY",
        caliber="未复权·快照",
        raw_ref="fuyao:///api/a-share/prices/snapshot#rid=mock",
        confidence=1.0,
        value_json={"last_price": 1325.08},
    )

    assert ThreadRepository(session).get(thread.id).goal == "评估半导体板块景气度"
    assert RunRepository(session).list_by_thread(thread.id)[0].id == run.id
    assert TaskRepository(session).list_by_run(run.id)[0].id == task.id
    assert StepRepository(session).list_by_task(task.id)[0].type == "ACT"
    assert EvidenceRepository(session).list_by_run(run.id)[0].id == evidence.id
    assert evidence.unit == "CNY" and evidence.confidence == 1.0

    assert RunRepository(session).update_status(
        run.id, status="COMPLETED", ended_at=1
    ).status == "COMPLETED"
    assert RunRepository(session).list_resumable() == []


def test_m0_checkpoint_seq_is_monotonic_per_run(session: Session) -> None:
    thread = ThreadRepository(session).create(title="t", goal="g")
    run = RunRepository(session).create(thread_id=thread.id)
    repo = CheckpointEventRepository(session)

    first = repo.append(run.id, "RunStarted", {"from": "CREATED", "to": "PLANNING"})
    second = repo.append(run.id, "PlanGenerated", {"tasks": 3})
    third = repo.append(run.id, "RunPaused", {"reason": "user"})

    assert (first.seq, second.seq, third.seq) == (1, 2, 3)
    assert [e.event_type for e in repo.list_by_run(run.id)] == [
        "RunStarted", "PlanGenerated", "RunPaused",
    ]
    assert [e.seq for e in repo.list_by_run(run.id, after_seq=1)] == [2, 3]
    assert repo.last(run.id).seq == 3


def test_m0_checkpoint_unique_run_seq_is_enforced(session: Session) -> None:
    thread = ThreadRepository(session).create(title="t", goal="g")
    run = RunRepository(session).create(thread_id=thread.id)
    session.add(
        CheckpointEventRow(
            id="e1", run_id=run.id, seq=1, event_type="RunStarted", payload_json={}, created_at=1
        )
    )
    session.add(
        CheckpointEventRow(
            id="e2", run_id=run.id, seq=1, event_type="RunStarted", payload_json={}, created_at=2
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_m0_tool_call_dedupe_key_unique_is_enforced(session: Session) -> None:
    thread = ThreadRepository(session).create(title="t", goal="g")
    run = RunRepository(session).create(thread_id=thread.id)
    task = TaskRepository(session).create(run_id=run.id, seq=1, title="t1", intent="i1")

    def make(call_id: str) -> ToolCallRow:
        return ToolCallRow(
            id=call_id,
            run_id=run.id,
            task_id=task.id,
            tool_name="get_price_snapshot",
            args_json={"thscode": "600519.SH"},
            dedupe_key="get_price_snapshot|600519.SH",
            created_at=1,
        )

    session.add(make("c1"))
    session.flush()
    session.add(make("c2"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

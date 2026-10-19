"""`api/threads.py`：线程创建 / 列表 / 详情 / 发起 Run（03_BUILD_PLAN §3 M5）。

对应架构 §13：
`POST /api/threads`、`GET /api/threads`、`GET /api/threads/{id}`、
`POST /api/threads/{id}/runs`（返回 `run_id`，执行体在 `runs.start_run`）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.store.repo import RunRepository, ThreadRepository

from .common import ThreadCreate, RunCreate, get_app_settings, get_db, not_found, thread_dict
from .runs import start_run

__all__ = ["router"]

router = APIRouter(prefix="/api/threads", tags=["threads"])

#: 线程标题缺省值：取目标前 40 字（避免把整段目标塞进标题）。
_TITLE_LIMIT = 40


@router.post("")
def create_thread(
    payload: ThreadCreate,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """创建线程（含初始目标）。`title` 缺省时由 `goal` 截取。"""
    goal = payload.goal.strip()
    title = (payload.title or goal[:_TITLE_LIMIT]).strip() or "未命名研究"
    row = ThreadRepository(session).create(title=title, goal=goal)
    return thread_dict(row)


@router.get("")
def list_threads(session: Session = Depends(get_db)) -> dict[str, Any]:
    """线程列表（按创建时间倒序，最新在前）。"""
    rows = ThreadRepository(session).list()
    rows.sort(key=lambda row: int(getattr(row, "created_at", 0) or 0), reverse=True)
    return {"count": len(rows), "threads": [thread_dict(row) for row in rows]}


@router.get("/{thread_id}")
def get_thread(thread_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """线程详情（含该线程的 runs）。"""
    row = ThreadRepository(session).get(thread_id)
    if row is None:
        raise not_found("Thread", thread_id)
    runs = RunRepository(session).list_by_thread(thread_id)
    return thread_dict(row, runs=runs)


@router.post("/{thread_id}/runs")
def create_run(
    thread_id: str,
    payload: RunCreate,
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """发起新 Run（返回 `run_id`）。

    `background=true`（默认）时立即返回，Run 由后台线程驱动，前端随后接
    `GET /api/runs/{id}/stream` 观察增量；`background=false` 时同步跑完再返回
    （测试口径：离线确定性）。
    """
    row = ThreadRepository(session).get(thread_id)
    if row is None:
        raise not_found("Thread", thread_id)
    return start_run(
        payload,
        thread_row=row,
        session=session,
        settings=settings,
        session_factory=request.app.state.session_factory,
    )

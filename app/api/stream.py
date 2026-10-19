"""`api/stream.py`：Run 的 SSE 全链路（03_BUILD_PLAN §3 M5）。

对应架构 §13 / §13.1：`GET /api/runs/{id}/stream`，**冻结 10 类事件**——
`status / plan / step / evidence / ui / approval / degrade / artifact / done / error`。
事件名与 data 形状与 `web/skeleton/lib/sse.ts` **逐字对齐**（改即破坏契约）：

```text
status   -> {run_id, status}
plan     -> {ui_block}          # plan_card / clarify_question
evidence -> {ui_block}          # evidence_card / evidence_table
ui       -> {ui_block}          # conclusion_card / approval_card 等
degrade  -> {ui_block}          # degradation_notice / FallbackJsonCard
step     -> {task_id, seq, type, tool?}
approval -> {approval_id, type}
artifact -> {artifact_id}
done     -> {run_id, status, cost?}
error    -> {code, message}
```

数据来源（两条独立的时间线，各自按 `seq` 增量推进）：

- **`checkpoint_events`**（事件溯源表）→ `status` / `step` / `approval` / `artifact`；
- **`ui_blocks`**（M5 动态 UI 表）→ `plan` / `evidence` / `ui` / `degrade`。

推送策略：**先重放已落库的增量，再按 `poll_interval` 轮询新数据**，Run 落入终态时发
`done` 并结束；`AWAITING_APPROVAL` 属**非终态**，流保持打开等待审批裁决（前端刷新不丢状态）。
`?after=<seq>` 与 `Last-Event-ID` 头都可用于断线续传。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.domain import UIBlockSourceType
from app.store.repo import LlmCallRepository, RunRepository, UIBlockRepository

from .common import RUN_TERMINAL_STATUSES, not_found, ui_block_payload
from .runs import pending_events, step_payloads

__all__ = [
    "router",
    "SSE_POLL_INTERVAL_S",
    "SSE_DEFAULT_TIMEOUT_S",
    "SSE_MEDIA_TYPE",
    "sse_frame",
    "block_event_name",
    "checkpoint_event",
    "stream_run",
    "stream_frames",
]

router = APIRouter(prefix="/api/runs", tags=["stream"])

#: SSE 媒体类型（浏览器 `EventSource` 的必需值）。
SSE_MEDIA_TYPE = "text/event-stream"

#: 轮询间隔（秒）：增量落库后最多等待这么久即被推送出去。
SSE_POLL_INTERVAL_S = 0.2

#: 单次连接的默认存活上限（秒）；到点未收口则关闭（浏览器会按 `Last-Event-ID` 续传）。
SSE_DEFAULT_TIMEOUT_S = 30.0

#: Run 生命周期事件 → SSE `status`（data 里的 `status` 取 payload 的 `to`）。
_STATUS_EVENTS: frozenset[str] = frozenset(
    {
        "RunStarted",
        "PlanApproved",
        "RunPaused",
        "RunResumed",
        "RunCompleted",
        "RunStopped",
        "RunFailed",
    }
)


# --------------------------------------------------------------------------- #
# 帧与映射（纯函数，便于单测穷举 10 类事件）
# --------------------------------------------------------------------------- #
def sse_frame(event: str, data: dict[str, Any]) -> str:
    """构造一帧 SSE（`event:` + `data:` + 空行）。"""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n"


def block_event_name(block: Any) -> str:
    """UIBlock → SSE 事件名（按组件与来源分流，恒 ∈ 冻结 10 类）。

    `FallbackJsonCard` 保留原块的 `source_type`，因此「未实现的组件」仍落在它原来的
    通道（如 `metric_block` → `evidence`），前端据此把降级卡插在原位。
    """
    component = str(getattr(block, "component", "") or "")
    if component == "degradation_notice" or component.startswith("FallbackJsonCard"):
        return "degrade"
    source = str(getattr(block, "source_type", "") or "")
    if source == str(UIBlockSourceType.PLAN):
        return "plan"
    if source == str(UIBlockSourceType.EVIDENCE):
        return "evidence"
    return "ui"


def checkpoint_event(event: Any) -> tuple[str, dict[str, Any]] | None:
    """`checkpoint_events` 行 → `(SSE 事件名, data)`；与 SSE 无关的事件返回 `None`。"""
    name = str(getattr(event, "event_type", "") or "")
    payload = dict(getattr(event, "payload_json", None) or {})
    run_id = str(getattr(event, "run_id", "") or "")
    if name in _STATUS_EVENTS:
        status = payload.get("to") or payload.get("status")
        if not status:
            return None
        return "status", {"run_id": run_id, "status": str(status)}
    if name == "ApprovalRequested":
        return "approval", {
            "approval_id": payload.get("approval_id"),
            "type": payload.get("type"),
        }
    if name == "ArtifactBuilt":
        return "artifact", {"artifact_id": payload.get("artifact_id")}
    return None


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/{run_id}/stream")
def stream_run(
    run_id: str,
    request: Request,
    after: int = 0,
    timeout: float = SSE_DEFAULT_TIMEOUT_S,
) -> StreamingResponse:
    """订阅一次 Run 的增量流（SSE）。

    `after`：从该 `seq` 之后开始推送（断线续传；缺省 0 = 全量重放）；
    亦可用 `Last-Event-ID` 头（浏览器 `EventSource` 自动携带）。
    响应头 `X-Accel-Buffering: no` 关闭反向代理缓冲，保证逐帧下发。
    """
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            after = max(int(after), int(last_event_id))
        except ValueError:
            pass

    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:  # pragma: no cover - create_app 必然注入
        raise RuntimeError("app.state.session_factory 未配置")

    with factory() as session:
        if RunRepository(session).get(run_id) is None:
            raise not_found("Run", run_id)

    return StreamingResponse(
        _event_stream(
            run_id,
            factory,
            after=int(after),
            timeout_s=float(timeout),
            poll_s=SSE_POLL_INTERVAL_S,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _event_stream(
    run_id: str,
    factory: Callable[[], Session],
    *,
    after: int,
    timeout_s: float,
    poll_s: float,
) -> Iterator[str]:
    """推流主体：重放 → 轮询 → 终态 `done`。

    每次都开一个短命 Session（不跨轮询持有连接）；游标是纯整数，跨会话安全。
    """
    event_cursor = max(0, int(after))
    block_cursor = max(0, int(after))
    seen_steps: set[tuple[str, Any]] = set()
    deadline = time.monotonic() + max(0.0, float(timeout_s))

    while True:
        try:
            with factory() as session:
                run = RunRepository(session).get(run_id)
                if run is None:
                    yield sse_frame("error", {"code": 404, "message": f"Run 不存在：{run_id}"})
                    return
                status = str(run.status)

                # ① 事件溯源 → status / approval / artifact
                for event in pending_events(session, run_id, event_cursor):
                    event_cursor = max(event_cursor, int(getattr(event, "seq", 0) or 0))
                    mapped = checkpoint_event(event)
                    if mapped is not None:
                        yield sse_frame(*mapped)

                # ② 主循环步进 → step
                for payload in step_payloads(session, run_id):
                    key = (str(payload.get("task_id")), payload.get("seq"))
                    if key in seen_steps:
                        continue
                    seen_steps.add(key)
                    yield sse_frame("step", payload)

                # ③ 动态 UI 块 → plan / evidence / ui / degrade
                for block in UIBlockRepository(session).list_by_run(run_id, after_seq=block_cursor):
                    block_cursor = max(block_cursor, int(getattr(block, "seq", 0) or 0))
                    yield sse_frame(block_event_name(block), {"ui_block": ui_block_payload(block)})

                # ④ 终态收口
                if status in RUN_TERMINAL_STATUSES:
                    cost, _tokens = LlmCallRepository(session).totals_for_run(run_id)
                    yield sse_frame(
                        "done",
                        {"run_id": run_id, "status": status, "cost": float(cost)},
                    )
                    return
        except Exception as exc:  # 推流内的意外不得静默：以 error 事件收口
            yield sse_frame(
                "error",
                {"code": 500, "message": f"{type(exc).__name__}: {exc}"},
            )
            return

        if time.monotonic() >= deadline:
            return
        time.sleep(max(0.0, float(poll_s)))


# 供测试直接构造事件流（避免必须起 HTTP 服务）
def stream_frames(
    run_id: str,
    factory: Callable[[], Session],
    *,
    after: int = 0,
    timeout_s: float = 0.0,
    poll_s: float = 0.0,
) -> list[str]:
    """同步取一次完整帧列表（`timeout=0` 即「重放一遍就走」，测试用）。"""
    return list(
        _event_stream(
            run_id, factory, after=int(after), timeout_s=float(timeout_s), poll_s=float(poll_s)
        )
    )

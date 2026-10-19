"""M5 REST / SSE 层公共构件（03_BUILD_PLAN §3 M5：`api/threads|runs|stream|approvals`）。

四件事：
1. **依赖注入**——请求级 `Session` 与应用级 `Settings` 都从 `app.state` 取，因此
   `create_app(settings=..., session_factory=...)` 一次注入即可让**全部**端点
   （含后台线程里的 Run 驱动）走同一套依赖：测试注入 `offline_settings()` 后，
   进程内**不读 `docs/api-key.txt`**（离线确定性，HANDOFF §18.5）。
2. **请求体模型**——仅声明 API 入参，不含领域字段（契约冻结：13 实体不动）。
3. **行 → JSON**——只暴露契约字段，避免把 ORM 行原样泄给前端。
4. **SSE 事件名**——10 类，与 `web/skeleton/lib/sse.ts` 的 `SSE_EVENT_TYPES`
   逐字对齐（架构 §13.1；改动即破坏契约）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

__all__ = [
    "SSE_EVENT_TYPES",
    "SSE_EVENT_COUNT",
    "RUN_TERMINAL_STATUSES",
    "ThreadCreate",
    "RunCreate",
    "ApprovalDecision",
    "get_db",
    "get_app_settings",
    "as_dict",
    "thread_dict",
    "run_dict",
    "task_dict",
    "evidence_dict",
    "artifact_dict",
    "ui_block_dict",
    "ui_block_payload",
    "approval_dict",
    "trace_dict",
    "step_dict",
    "not_found",
]

#: SSE 事件名（**冻结 10 类**，架构 §13.1 / `web/skeleton/lib/sse.ts`）。
SSE_EVENT_TYPES: tuple[str, ...] = (
    "status",
    "plan",
    "step",
    "evidence",
    "ui",
    "approval",
    "degrade",
    "artifact",
    "done",
    "error",
)

#: 契约数量：10 类（改动即破坏契约）。
SSE_EVENT_COUNT = 10

#: Run 终态（与 `harness.state_machine.RUN_TERMINAL` 同口径，此处用字符串便于 JSON 比较）。
RUN_TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "STOPPED", "FAILED"})


# --------------------------------------------------------------------------- #
# 依赖注入
# --------------------------------------------------------------------------- #
def get_db(request: Request) -> Iterator[Session]:
    """请求级 Session：从 `app.state.session_factory` 取上下文（默认 `session_scope`）。

    测试可用 `app.dependency_overrides[get_db]` 覆盖，或直接给 `create_app` 传
    `session_factory=lambda: session_scope(engine)`。
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:  # pragma: no cover - create_app 必然注入
        raise RuntimeError("app.state.session_factory 未配置")
    with factory() as session:
        yield session


def get_app_settings(request: Request) -> Any:
    """应用级 `Settings`（由 `create_app(settings=...)` 注入）。"""
    return request.app.state.settings


def not_found(what: str, key: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} 不存在：{key}")


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #
class ThreadCreate(BaseModel):
    """`POST /api/threads` 入参（`title` 缺省时由 goal 截取）。"""

    goal: str = Field(min_length=1)
    title: str | None = None


class RunCreate(BaseModel):
    """`POST /api/threads/{id}/runs` 入参。

    `background=True`（默认）：立即返回 `run_id`，Run 由后台线程驱动，
    前端随后接 SSE 观察增量；`background=False` 时同步跑完再返回（测试用，离线确定性）。
    """

    goal: str | None = None
    background: bool = True


class ApprovalDecision(BaseModel):
    """`POST /api/approvals/{id}/decide` 入参（结论别名由 `HITLGate` 归一）。"""

    decision: str = Field(min_length=1)
    decided_by: str | None = None


# --------------------------------------------------------------------------- #
# 行 → JSON
# --------------------------------------------------------------------------- #
def as_dict(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """从 ORM 行（或任意对象）按字段名取值组装成 JSON 友好的 dict。"""
    if row is None:
        return {}
    return {name: getattr(row, name, None) for name in fields}


def thread_dict(row: Any, *, runs: list[Any] | None = None) -> dict[str, Any]:
    data = as_dict(row, ("id", "title", "goal", "status", "created_at", "updated_at"))
    data["thread_id"] = data.pop("id")
    if runs is not None:
        data["runs"] = [run_dict(item) for item in runs]
    return data


def run_dict(row: Any, *, goal: str | None = None) -> dict[str, Any]:
    data = as_dict(
        row,
        (
            "thread_id",
            "status",
            "parent_run_id",
            "stop_reason",
            "cost_total",
            "tokens_total",
            "started_at",
            "ended_at",
            "error",
        ),
    )
    data["run_id"] = getattr(row, "id", None)
    data["terminal"] = str(data.get("status")) in RUN_TERMINAL_STATUSES
    if goal is not None:
        data["goal"] = goal
    return data


def task_dict(row: Any) -> dict[str, Any]:
    data = as_dict(
        row,
        (
            "id",
            "run_id",
            "seq",
            "title",
            "intent",
            "status",
            "attempts",
            "evidence_count",
            "degradation_reason",
        ),
    )
    data["task_id"] = data.pop("id")
    data["deps"] = list(getattr(row, "deps_json", None) or [])
    return data


def step_dict(row: Any) -> dict[str, Any]:
    """`step` SSE 的 data 形状（架构 §13.1：`{task_id, type, tool?}`）。"""
    payload = dict(getattr(row, "payload_json", None) or {})
    data: dict[str, Any] = {
        "task_id": getattr(row, "task_id", None),
        "seq": getattr(row, "seq", None),
        "type": getattr(row, "type", None),
    }
    tool = payload.get("tool") or payload.get("tool_name")
    if tool:
        data["tool"] = str(tool)
    return data


def evidence_dict(row: Any) -> dict[str, Any]:
    """证据详情：C3 强制字段（`source / interface / as_of / unit / caliber / raw_ref`）+ 值。"""
    data = as_dict(
        row,
        (
            "run_id",
            "task_id",
            "tool_call_id",
            "source",
            "interface",
            "as_of",
            "fetched_at",
            "staleness_ms",
            "unit",
            "caliber",
            "raw_ref",
            "confidence",
            "degrade_reason",
            "created_at",
        ),
    )
    data["evidence_id"] = getattr(row, "id", None)
    data["value"] = getattr(row, "value_json", None)
    return data


def artifact_dict(row: Any) -> dict[str, Any]:
    """产物：三层数组（C2，不可合并）+ `citations`。"""
    data = as_dict(row, ("run_id", "version", "created_at"))
    data["artifact_id"] = getattr(row, "id", None)
    data["facts_json"] = list(getattr(row, "facts_json", None) or [])
    data["inferences_json"] = list(getattr(row, "inferences_json", None) or [])
    data["uncertainties_json"] = list(getattr(row, "uncertainties_json", None) or [])
    data["citations_json"] = list(getattr(row, "citations_json", None) or [])
    return data


def ui_block_dict(row: Any) -> dict[str, Any]:
    data = as_dict(row, ("run_id", "seq", "source_type", "component", "render_status", "created_at"))
    data["block_id"] = getattr(row, "id", None)
    data["props"] = dict(getattr(row, "props_json", None) or {})
    data["refs"] = list(getattr(row, "refs_json", None) or [])
    return data


def ui_block_payload(row: Any) -> dict[str, Any]:
    """`ui_block` 的 **SSE 线格式**（`web/skeleton/lib/sse.ts::UiBlockPayload`）。

    契约字段是 `component / props / refs / render_status`（注意：是 `props` 而非 `props_json`），
    `seq` 作为附加字段一并下发，供前端按序插入（架构 §11.3-3）。
    """
    return {
        "component": str(getattr(row, "component", "") or ""),
        "props": dict(getattr(row, "props_json", None) or {}),
        "refs": list(getattr(row, "refs_json", None) or []),
        "render_status": str(getattr(row, "render_status", "ok") or "ok"),
        "seq": int(getattr(row, "seq", 0) or 0),
        "source_type": str(getattr(row, "source_type", "") or ""),
    }


def approval_dict(row: Any) -> dict[str, Any]:
    data = as_dict(
        row,
        (
            "run_id",
            "type",
            "payload_json",
            "status",
            "requested_at",
            "decided_at",
            "decided_by",
        ),
    )
    data["approval_id"] = getattr(row, "id", None)
    data["payload"] = dict(getattr(row, "payload_json", None) or {})
    data.pop("payload_json", None)
    return data


def trace_dict(row: Any) -> dict[str, Any]:
    data = as_dict(
        row,
        ("run_id", "span_id", "parent_span_id", "name", "started_at", "duration_ms", "status"),
    )
    data["attrs"] = dict(getattr(row, "attrs_json", None) or {})
    return data

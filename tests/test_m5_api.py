"""M5 验收（二）：REST + SSE 服务面（03_BUILD_PLAN §3 M5）。

覆盖架构 §13 的端点清单与 §13.1 的 **SSE 10 类事件**契约：

- `threads.py`：`POST/GET /api/threads`、`GET /api/threads/{id}`、`POST /api/threads/{id}/runs`
- `runs.py`：`GET /api/runs/{id}` + `evidences/artifacts/traces/cost`、`pause|resume|abort|plan`
- `stream.py`：`GET /api/runs/{id}/stream`（10 类事件名与 data 形状）
- `approvals.py`：`GET /api/approvals`、`GET /api/approvals/{id}`、`POST /api/approvals/{id}/decide`
  （逻辑全部委托 M3 `HITLGate`）
- 运维自检：`GET /api/health`（契约数字）、`GET /api/tools`（9 个受控工具）

**离线确定性**：`create_app(settings=offline_settings(), engine=engine)` 一次注入，
因此进程内**不读 `docs/api-key.txt`**、不触网、不依赖真实 LLM（`ModelRouter` 走兜底规划）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api import create_app
from app.api.common import SSE_EVENT_COUNT, SSE_EVENT_TYPES
from app.api.stream import block_event_name, checkpoint_event, sse_frame, stream_frames
from app.harness.hitl import HITLGate
from app.harness.ui_renderer import IMPLEMENTED_COMPONENTS, UI_COMPONENT_WHITELIST
from app.store.db import session_scope
from app.store.repo import ApprovalRepository, RunRepository, UIBlockRepository
from m1_support import ACCEPTANCE_GOAL, offline_settings

#: 冻结的 SSE 事件名（架构 §13.1 / `web/skeleton/lib/sse.ts`）——**契约，不可增删**。
FROZEN_SSE_EVENTS: tuple[str, ...] = (
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


@pytest.fixture()
def client(engine: Engine) -> TestClient:
    """离线确定性客户端：注入 `offline_settings()` + 内存库。"""
    return TestClient(create_app(settings=offline_settings(), engine=engine))


# --------------------------------------------------------------------------- #
# 工具：SSE 帧解析
# --------------------------------------------------------------------------- #
def parse_frames(frames: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """`["event: x\\ndata: {...}\\n\\n"] → [(event, data)]`。"""
    parsed: list[tuple[str, dict[str, Any]]] = []
    for frame in frames:
        event, data = "", {}
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event:
            parsed.append((event, data))
    return parsed


def parse_sse_text(text: str) -> list[tuple[str, dict[str, Any]]]:
    """整段 SSE 报文 → `[(event, data)]`（HTTP 响应用）。"""
    return parse_frames([chunk + "\n\n" for chunk in text.split("\n\n") if chunk.strip()])


def _seed_run(session: Session, *, status: str = "CREATED") -> tuple[str, str]:
    """直接落一条 Thread + Run（供不经 HTTP 的纯函数/过渡态用例使用）。"""
    from app.harness import SessionManager

    sm = SessionManager(session)
    thread = sm.create_thread(title="M5 SSE", goal=ACCEPTANCE_GOAL)
    run = sm.create_run(thread_id=thread.id)
    if status != "CREATED":
        RunRepository(session).update_status(run.id, status=status)
    return thread.id, run.id


def _run_once(client: TestClient, *, goal: str = ACCEPTANCE_GOAL) -> dict[str, Any]:
    """建线程 → 同步跑完一次 Run（`background=false`，离线确定性）。"""
    thread = client.post("/api/threads", json={"goal": goal}).json()
    created = client.post(
        f"/api/threads/{thread['thread_id']}/runs",
        json={"background": False},
    ).json()
    return created


# --------------------------------------------------------------------------- #
# 1. 契约数字
# --------------------------------------------------------------------------- #
def test_m5_sse_event_types_are_frozen() -> None:
    assert SSE_EVENT_TYPES == FROZEN_SSE_EVENTS
    assert SSE_EVENT_COUNT == len(FROZEN_SSE_EVENTS) == 10


def test_m5_health_reports_frozen_contracts(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    contracts = body["contracts"]
    assert contracts["tables"] == 15
    assert contracts["events"] == 26
    assert contracts["sse_events"] == 10
    assert contracts["ui_whitelist"] == len(UI_COMPONENT_WHITELIST) == 12
    assert contracts["ui_implemented"] == len(IMPLEMENTED_COMPONENTS) == 7
    assert contracts["render_strict"] is True
    assert contracts["ui_block_max"] == 200


def test_m5_tools_endpoint_lists_nine_controlled_tools(client: TestClient) -> None:
    body = client.get("/api/tools").json()
    assert body["count"] == 10
    assert body["sse_events"] == list(FROZEN_SSE_EVENTS)
    names = {tool["name"] for tool in body["tools"]}
    assert "emit_artifact" in names  # 唯一内部工具
    assert "get_financials" in names  # P0-1：财报工具已接入
    assert len(names) == 10


# --------------------------------------------------------------------------- #
# 2. threads / runs：完整演示一次研究
# --------------------------------------------------------------------------- #
def test_m5_thread_lifecycle(client: TestClient) -> None:
    created = client.post("/api/threads", json={"goal": ACCEPTANCE_GOAL}).json()
    thread_id = created["thread_id"]
    assert created["goal"] == ACCEPTANCE_GOAL
    assert created["title"] == ACCEPTANCE_GOAL  # 缺省取 goal 前 40 字

    listed = client.get("/api/threads").json()
    assert listed["count"] >= 1
    assert thread_id in {row["thread_id"] for row in listed["threads"]}

    detail = client.get(f"/api/threads/{thread_id}").json()
    assert detail["thread_id"] == thread_id
    assert detail["runs"] == []

    assert client.get("/api/threads/不存在").status_code == 404


def test_m5_run_demo_end_to_end(client: TestClient) -> None:
    """验收：**完整演示一次研究**——发起 Run → 产物 + 动态 UI + 证据 + 成本。"""
    created = _run_once(client)
    run_id = created["run_id"]
    assert created["background"] is False
    assert created["status"] == "COMPLETED"

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["run_id"] == run_id
    assert run["status"] == "COMPLETED"
    assert run["terminal"] is True
    assert run["goal"] == ACCEPTANCE_GOAL
    assert run["tasks"], "一次研究必须落 Task 清单"
    assert run["ui_block_count"] > 0
    assert run["artifact_id"], "Run 收口必须落产物"

    evidences = client.get(f"/api/runs/{run_id}/evidences").json()
    # 兜底规划 + Mock 主源：至少产出可渲染证据（条数随 Mock 覆盖率浮动，不写死）
    assert evidences["count"] > 0
    call_names = {call["tool_name"] for call in evidences["tool_calls"]}
    assert call_names, "必须暴露本次实际调用，区别于计划使用"
    assert len(run["tasks"]) >= 3, "兜底规划至少拆出 3 个 Task（M1 基线）"
    for item in evidences["evidences"]:
        # C3 强制字段：**键必须存在**（D4：`null` 透传不补零，故只校验存在性）
        assert {"source", "interface", "as_of", "unit", "caliber", "raw_ref"} <= set(item)
        assert item["source"] and item["interface"]
        assert isinstance(item["as_of"], int) and item["as_of"] > 0
        assert item["evidence_id"], "证据必须可被引用（refs 反查入口）"
        if item["tool_call_id"]:
            assert item["tool_name"] in call_names

    artifacts = client.get(f"/api/runs/{run_id}/artifacts").json()
    assert artifacts["count"] == 1
    latest = artifacts["latest"]
    assert latest["facts_json"] and latest["inferences_json"]
    # 推断必须带切换条件（C2）
    assert all(str(i.get("condition") or "").strip() for i in latest["inferences_json"])
    # C1：三层都不含操作建议字段
    forbidden = {"advice", "action", "suggestion", "target_price", "rating", "buy", "sell"}
    for layer in ("facts_json", "inferences_json", "uncertainties_json"):
        for item in latest[layer]:
            assert not (forbidden & set(item))

    traces = client.get(f"/api/runs/{run_id}/traces").json()
    assert traces["count"] > 0
    assert traces["render_failed"] == 0, "正常 Run 不应有渲染降级"
    assert all(span["name"] for span in traces["spans"])

    cost = client.get(f"/api/runs/{run_id}/cost").json()
    assert cost["status"] == "COMPLETED"
    assert cost["tokens_total"] >= 0
    assert "by_model" in cost


def test_financial_goal_preserves_tool_evidence_and_readable_fact(client: TestClient) -> None:
    """离线财报链：工具名与接口路径不同，事实仍可回查到年度原值。"""
    run_id = _run_once(client, goal="分析茅台近三年公司收入架构变化")["run_id"]
    snapshot = client.get(f"/api/runs/{run_id}/evidences").json()
    calls = [c for c in snapshot["tool_calls"] if c["tool_name"] == "get_financials"]
    financials = [e for e in snapshot["evidences"] if e["tool_name"] == "get_financials"]
    assert calls, "财报目标应实际调用财报工具，不能只出现在计划里"
    assert financials, "财报工具应关联其实际取到的证据"
    assert all(e["interface"] != e["tool_name"] for e in financials)
    assert any(
        row.get("fiscal_year") and isinstance(row.get("operating_income"), (int, float))
        for evidence in financials for row in evidence["value"].get("item", [])
    )
    artifact = client.get(f"/api/runs/{run_id}/artifacts").json()["latest"]
    assert any(
        any(e["evidence_id"] in fact["refs"] for e in financials)
        and "operating_income=" in fact["text"]
        for fact in artifact["facts_json"]
    )

    assert client.get("/api/runs/不存在").status_code == 404


def test_m5_run_commands_pause_resume_abort(client: TestClient, engine: Engine) -> None:
    """`pause` / `resume` / `abort` 经状态机迁移（非法迁移 → 409）。"""
    with session_scope(engine) as session:
        _, run_id = _seed_run(session, status="EXECUTING")

    paused = client.post(f"/api/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "AWAITING_APPROVAL"

    resumed = client.post(f"/api/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "EXECUTING"

    aborted = client.post(f"/api/runs/{run_id}/abort")
    assert aborted.status_code == 200
    body = aborted.json()
    assert body["status"] == "STOPPED"
    assert body["stop_reason"] == "USER_ABORT"
    assert body["terminal"] is True

    # 终态后不可再暂停（状态机拒绝 → 409）
    assert client.post(f"/api/runs/{run_id}/pause").status_code == 409


def test_m5_plan_override_resumes_run(client: TestClient, engine: Engine) -> None:
    """人工改判计划后重新执行（复用 M3 `ResumePlan` 通道，不新增事件/表）。"""
    # 续跑入口要求 Run 已在 EXECUTING（`Pipeline.run(resume_plan=...)` 的入口状态）
    with session_scope(engine) as session:
        _, run_id = _seed_run(session, status="EXECUTING")

    resp = client.post(
        f"/api/runs/{run_id}/plan",
        json={"tasks": [{"seq": 1, "title": "改判后的取证", "intent": "只取快照", "tools": ["get_price_snapshot"]}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["status"] == "COMPLETED"
    assert body["task_count"] >= 1
    assert body["ui_block_count"] > 0

    # 已完成 Run 无需改判 → 409
    completed = _run_once(client)["run_id"]
    assert (
        client.post(f"/api/runs/{completed}/plan", json={"tasks": [{"seq": 1, "title": "x"}]}).status_code
        == 409
    )


# --------------------------------------------------------------------------- #
# 3. approvals：列表 / 详情 / 裁决（逻辑来自 M3 HITLGate）
# --------------------------------------------------------------------------- #
def _pend_approval(engine: Engine) -> tuple[str, str]:
    """造一条 `PENDING` 审批（Run 先置 EXECUTING，`HITLGate.request` 才能挂起）。"""
    with session_scope(engine) as session:
        _, run_id = _seed_run(session, status="EXECUTING")
        approval_id = HITLGate(session, settings=offline_settings()).request(
            run_id, "plan", {"goal": ACCEPTANCE_GOAL, "summary": "计划待审批"}, reason="需人工确认"
        )
    return run_id, approval_id


def test_m5_approval_list_and_decide(client: TestClient, engine: Engine) -> None:
    run_id, approval_id = _pend_approval(engine)

    pending = client.get("/api/approvals").json()
    assert pending["status"] == "PENDING"
    assert approval_id in {row["approval_id"] for row in pending["approvals"]}
    # 按 run_id 过滤
    scoped = client.get("/api/approvals", params={"run_id": run_id}).json()
    assert scoped["count"] == 1
    # 全量别名
    assert client.get("/api/approvals", params={"status": "ALL"}).json()["status"] == "ALL"

    detail = client.get(f"/api/approvals/{approval_id}").json()
    assert detail["approval_id"] == approval_id
    assert detail["type"] == "plan"
    assert detail["payload"]["goal"] == ACCEPTANCE_GOAL

    decided = client.post(
        f"/api/approvals/{approval_id}/decide",
        json={"decision": "GRANTED", "decided_by": "tester"},
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["status"] == "GRANTED"
    assert body["run_status"] == "EXECUTING", "同意后 Run 必须回 EXECUTING（M3 口径）"

    assert client.get("/api/approvals/不存在").status_code == 404
    assert client.post("/api/approvals/不存在/decide", json={"decision": "GRANTED"}).status_code == 404
    assert client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "乱填"}).status_code == 422


def test_m5_approval_reject_stops_run(client: TestClient, engine: Engine) -> None:
    _, approval_id = _pend_approval(engine)
    body = client.post(
        f"/api/approvals/{approval_id}/decide", json={"decision": "REJECTED"}
    ).json()
    assert body["status"] == "REJECTED"
    assert body["run_status"] == "STOPPED", "拒绝后 Run 必须置 STOPPED（M3 口径）"


# --------------------------------------------------------------------------- #
# 4. SSE 全链路
# --------------------------------------------------------------------------- #
def test_m5_sse_frame_and_mapping_units() -> None:
    """纯函数口径：帧格式 + 事件映射（不启动服务即可穷举）。"""

    class _Block:
        def __init__(self, component: str, source_type: str = "EVIDENCE") -> None:
            self.component = component
            self.source_type = source_type

    class _Event:
        def __init__(self, event_type: str, payload: dict[str, Any]) -> None:
            self.event_type = event_type
            self.payload_json = payload
            self.run_id = "r1"
            self.seq = 1

    assert sse_frame("done", {"run_id": "r1"}).startswith("event: done\ndata: ")
    assert sse_frame("done", {"x": "中"}).endswith("\n\n")

    assert block_event_name(_Block("plan_card", "PLAN")) == "plan"
    assert block_event_name(_Block("evidence_card", "EVIDENCE")) == "evidence"
    assert block_event_name(_Block("conclusion_card", "ARTIFACT")) == "ui"
    assert block_event_name(_Block("degradation_notice", "SYSTEM")) == "degrade"
    assert block_event_name(_Block("FallbackJsonCard", "EVIDENCE")) == "degrade"

    assert checkpoint_event(_Event("RunStarted", {"to": "PLANNING"})) == (
        "status",
        {"run_id": "r1", "status": "PLANNING"},
    )
    assert checkpoint_event(_Event("ApprovalRequested", {"approval_id": "a1", "type": "plan"})) == (
        "approval",
        {"approval_id": "a1", "type": "plan"},
    )
    assert checkpoint_event(_Event("ArtifactBuilt", {"artifact_id": "af1"})) == (
        "artifact",
        {"artifact_id": "af1"},
    )
    assert checkpoint_event(_Event("TaskCompleted", {})) is None  # 与 SSE 无关


def test_m5_sse_stream_replays_frozen_events(client: TestClient, engine: Engine) -> None:
    """终态 Run 的流：重放全部增量 → `done` 收口（HTTP 走通，`timeout=0` 不阻塞）。"""
    created = _run_once(client)
    run_id = created["run_id"]

    resp = client.get(f"/api/runs/{run_id}/stream", params={"timeout": 0})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = parse_sse_text(resp.text)
    events = [event for event, _ in frames]
    assert set(events) <= set(FROZEN_SSE_EVENTS)

    # ① status：Run 生命周期
    statuses = [data["status"] for event, data in frames if event == "status"]
    assert "COMPLETED" in statuses
    assert all("run_id" in data for event, data in frames if event == "status")

    # ② step：{task_id, type}
    steps = [data for event, data in frames if event == "step"]
    assert steps, "主循环必须产出 step 增量"
    assert all(data["task_id"] and data["type"] for data in steps)

    # ③ plan / evidence / ui：{ui_block}
    for name in ("plan", "evidence", "ui"):
        blocks = [data["ui_block"] for event, data in frames if event == name]
        assert blocks, f"{name} 通道必须有块"
        for block in blocks:
            # 与 `web/skeleton/lib/sse.ts::UiBlockPayload` 逐字对齐
            assert set(block) >= {"component", "props", "refs", "render_status"}
            assert block["component"] in IMPLEMENTED_COMPONENTS
            assert block["render_status"] == "ok"

    # ④ artifact：{artifact_id}
    artifacts = [data["artifact_id"] for event, data in frames if event == "artifact"]
    assert artifacts and artifacts[-1]

    # ⑤ done：{run_id, status, cost}
    done = [data for event, data in frames if event == "done"]
    assert len(done) == 1
    assert done[0]["run_id"] == run_id
    assert done[0]["status"] == "COMPLETED"
    assert isinstance(done[0]["cost"], (int, float))
    assert events[-1] == "done", "`done` 必须是最后一帧"


def test_m5_sse_stream_emits_approval_and_keeps_stream_open(engine: Engine) -> None:
    """挂起中的 Run：`approval` 事件到达，且**非终态不发 `done`**（刷新不丢状态）。"""
    run_id, approval_id = _pend_approval(engine)
    frames = parse_frames(
        stream_frames(run_id, lambda: session_scope(engine), timeout_s=0.0, poll_s=0.0)
    )
    approvals = [data for event, data in frames if event == "approval"]
    assert approvals == [{"approval_id": approval_id, "type": "plan"}]
    assert "done" not in {event for event, _ in frames}


def test_m5_sse_stream_unknown_run_errors(engine: Engine) -> None:
    frames = parse_frames(
        stream_frames("不存在", lambda: session_scope(engine), timeout_s=0.0, poll_s=0.0)
    )
    assert frames[0][0] == "error"
    assert frames[0][1]["code"] == 404


def test_m5_sse_degrade_channel_carries_fallback_blocks(engine: Engine) -> None:
    """非法块（无引用的 FACT 卡）落库后经 SSE **`degrade` 通道**推给前端降级卡。"""
    with session_scope(engine) as session:
        _, run_id = _seed_run(session)
        UIBlockRepository(session).create(
            run_id=run_id,
            source_type="ARTIFACT",
            component="FallbackJsonCard",
            props_json={"component": "conclusion_card", "reason": "refs 为空"},
            refs_json=[],
            render_status="failed",
        )

    frames = parse_frames(
        stream_frames(run_id, lambda: session_scope(engine), timeout_s=0.0, poll_s=0.0)
    )
    degrade = [data["ui_block"] for event, data in frames if event == "degrade"]
    assert degrade, "降级卡必须走 `degrade` 通道"
    assert degrade[0]["component"] == "FallbackJsonCard"
    assert degrade[0]["render_status"] == "failed"


# --------------------------------------------------------------------------- #
# 5. 一致性：审批与 UIBlock 落库口径
# --------------------------------------------------------------------------- #
def test_m5_approval_repo_pending_after_request(engine: Engine) -> None:
    _, approval_id = _pend_approval(engine)
    with session_scope(engine) as session:
        rows = ApprovalRepository(session).list_pending()
        assert [row.id for row in rows] == [approval_id]

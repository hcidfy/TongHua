"""M4 验收（03_BUILD_PLAN §3 M4）——记忆与上下文。

验收原文（03_BUILD_PLAN §3 M4 L273）：
    构造长会话（≥12 个 Step）触发压缩 → 压缩后 `Artifact` 引用的**全部 `evidence_id`
    仍可在 DB 解析**（FR-04 核心验收）；新开 Thread 能召回历史偏好。

本文件对 M4 的**每个部件**做离线确定性可行性测试（不触网、不读 `api-key.txt`、
不依赖真实 LLM）：

- `harness/context.py`   —— `estimate_tokens` 确定性口径 / `ContextManager.build` 阈值触发与上报；
- `harness/compactor.py` —— 分层压缩（系统提示不压、最近 Step 保留、Evidence 只留引用）；
- `harness/memory.py`    —— 三层记忆读写 + 关键词命中 & 时间衰减打分 + Top-K 截断；
- `hooks/pre/context_inject.py` —— 会话记忆 / 长期记忆注入时机；
- **Step 级停止规则**（逐轮内嵌，复用 `Pipeline` 的同一组计量器，HANDOFF §17.5）。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import Evidence, RunStatus, StopReason, TaskStatus, now_ms
from app.domain import MemoryScope, ToolSpec
from app.harness import (
    KEEP_RECENT_STEPS,
    MS_PER_HOUR,
    AgentLoop,
    BudgetLimits,
    BudgetManager,
    Compactor,
    ContextBundle,
    ContextManager,
    EvidenceRef,
    LoopContext,
    MemoryStore,
    Pipeline,
    SessionManager,
    StepDigest,
    StepView,
    StopRuleSet,
    StopState,
    ToolExecutor,
    estimate_tokens,
    tokenize,
)
from app.harness.hooks.pre import ContextInjectHook
from app.harness.hooks.base import PipelineContext
from app.store.repo import (
    CheckpointEventRepository,
    EvidenceRepository,
    MemoryRepository,
    RunRepository,
    StepRepository,
    TaskRepository,
)
from m1_support import (
    ACCEPTANCE_GOAL,
    FALLBACK_TOOLS,
    always_failing_impl,
    build_registry,
    build_router,
    llm_settings,
    offline_settings,
)


# --------------------------------------------------------------------------- #
# 公共构件
# --------------------------------------------------------------------------- #
def _bootstrap(session: Session) -> tuple[SessionManager, str, str]:
    """建 Thread + Run，返回 `(session_manager, thread_id, run_id)`。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="半导体板块研究", goal=ACCEPTANCE_GOAL)
    run = sm.create_run(thread_id=thread.id)
    return sm, thread.id, run.id


def _pipeline(session: Session, *, settings: Settings | None = None, registry=None) -> Pipeline:
    return Pipeline(
        session=session,
        settings=settings or offline_settings(),
        registry=registry or build_registry(session),
    )


def _small_budget_settings() -> Settings:
    """把 token 上限压到极小 → 压缩阈值 (max_tokens × 0.6) 很低，必然触发压缩。"""
    return offline_settings().model_copy(update={"budget_max_tokens": 60})


def _make_bundle(step_count: int = 12) -> ContextBundle:
    """构造 n 个 Step + n 条证据引用的上下文（默认 12，满足验收的「≥12 个 Step」）。"""
    bundle = ContextBundle(run_id="run-x", goal="长会话压缩验收")
    bundle.steps = [
        StepDigest(
            task_id="task-1",
            seq=index,
            type="OBSERVE",
            tool="get_price_snapshot",
            evidence_id=f"ev{index}",
            payload={"tool": "get_price_snapshot", "evidence_id": f"ev{index}", "sample": index},
        )
        for index in range(1, step_count + 1)
    ]
    bundle.evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev{index}",
            source="fuyao",
            interface="/mock/get_price_snapshot",
            unit="CNY",
            caliber="快照价",
            value_preview='{"sample": ' + str(index) + "}",
        )
        for index in range(1, step_count + 1)
    ]
    return bundle


# --------------------------------------------------------------------------- #
# 部件 ①：token 估算 / 关键词切词（确定性口径）
# --------------------------------------------------------------------------- #
def test_m4_estimate_tokens_is_deterministic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1  # ASCII 4 字符 = 1 token
    assert estimate_tokens("abcde") == 2  # 向上取整
    assert estimate_tokens("中文") == 2  # 非 ASCII 每字符 1 token
    assert estimate_tokens("中ab") == 2  # 1 + ceil(2/4)
    assert estimate_tokens(None) == 0  # 空值安全


def test_m4_tokenize_splits_ascii_words_and_cjk_ngrams() -> None:
    assert tokenize("600519.SH") == ["600519", "sh"]  # ASCII 整词、小写
    cjk = tokenize("半导体")
    # 逐字产出「单字 + 以该字起头的双字」（去重保序）：半 / 半导 / 导 / 导体 / 体
    assert cjk == ["半", "半导", "导", "导体", "体"]
    # 去重保序
    assert tokenize("半导体 半导体") == cjk


# --------------------------------------------------------------------------- #
# 部件 ②：三层记忆读写
# --------------------------------------------------------------------------- #
def test_m4_memory_three_scopes_working_stays_in_memory(session: Session) -> None:
    store = MemoryStore(session, settings=offline_settings())

    working = store.write(MemoryScope.WORKING, kind="note", content="临时工作记忆：半导体")
    session_mem = store.write(
        MemoryScope.SESSION, kind="note", content="会话记忆：半导体资金", thread_id="t1"
    )
    long_mem = store.write(MemoryScope.LONG, kind="preference", content="长期偏好：半导体")

    # WORKING 只落内存：`memories` 表里不应有它
    assert MemoryRepository(session).count(scope=str(MemoryScope.WORKING)) == 0
    # SESSION / LONG 落库
    assert MemoryRepository(session).count() == 2
    assert MemoryRepository(session).get(session_mem.id) is not None
    assert MemoryRepository(session).get(long_mem.id) is not None
    # 工作记忆仍可从内存召回
    assert [m.id for m in store.recall("半导体", MemoryScope.WORKING)] == [working.id]


def test_m4_memory_recall_prioritises_keyword_hits_over_recency(session: Session) -> None:
    now = now_ms()
    store = MemoryStore(settings=offline_settings())
    # 老记忆但命中更多关键词
    rich = store.write(
        MemoryScope.WORKING, kind="note", content="半导体 资金 流向", created_at=now - 200 * MS_PER_HOUR
    )
    # 新记忆但命中更少关键词
    store.write(MemoryScope.WORKING, kind="note", content="半导体 板块 行情", created_at=now)

    hits = store.recall_scored("半导体 资金", MemoryScope.WORKING, now=now)
    assert len(hits) == 2
    assert hits[0].memory.id == rich.id, "命中数优先于时间衰减"
    assert hits[0].keyword_hits > hits[1].keyword_hits


def test_m4_memory_decay_breaks_ties_and_top_k_truncates(session: Session) -> None:
    now = now_ms()
    store = MemoryStore(settings=offline_settings())
    store.write(MemoryScope.WORKING, kind="note", content="半导体资金", created_at=now - 72 * MS_PER_HOUR)
    store.write(MemoryScope.WORKING, kind="note", content="半导体资金", created_at=now)
    store.write(MemoryScope.WORKING, kind="note", content="半导体资金", created_at=now - 24 * MS_PER_HOUR)

    hits = store.recall_scored("半导体资金", MemoryScope.WORKING, now=now)
    assert [h.memory.created_at for h in hits] == [now, now - 24 * MS_PER_HOUR, now - 72 * MS_PER_HOUR]
    # 半衰期 = 72h：整好一个半衰期 → recency = 0.5
    assert abs(hits[-1].recency - 0.5) < 1e-6
    # Top-K 截断（覆盖 settings 默认值）
    assert len(store.recall("半导体资金", MemoryScope.WORKING, top_k=1, now=now)) == 1


def test_m4_memory_gather_dedupes_persisted_memory(session: Session) -> None:
    """回归护栏：已落库记忆同时存在于内存副本与 DB，召回必须按 id 去重。"""
    store = MemoryStore(session, settings=offline_settings())
    store.write(MemoryScope.SESSION, kind="pref", content="半导体资金偏好", thread_id="t1")
    store.write(MemoryScope.LONG, kind="pref", content="半导体资金偏好")

    hits = store.recall_scored("半导体资金", now=now_ms())
    ids = [h.memory.id for h in hits]
    assert len(ids) == 2, "两条不同记忆各命中一次"
    assert len(ids) == len(set(ids)), "同一记忆不得因「内存 + DB」重复出现"


def test_m4_memory_long_scope_recalled_across_threads(session: Session) -> None:
    store = MemoryStore(session, settings=offline_settings())
    store.write(MemoryScope.LONG, kind="preference", content="用户偏好半导体板块资金", created_at=now_ms())
    store.write(
        MemoryScope.SESSION, kind="note", content="用户偏好半导体板块资金", thread_id="thread-A"
    )

    # 新 Thread B：只应召回跨 Thread 的 LONG，不应看到 thread-A 的 SESSION
    hits = store.recall_scored("半导体", thread_id="thread-B", now=now_ms())
    assert len(hits) == 1
    assert str(hits[0].memory.scope) == str(MemoryScope.LONG)


# --------------------------------------------------------------------------- #
# 部件 ③：分层压缩
# --------------------------------------------------------------------------- #
def test_m4_compactor_keeps_recent_steps_and_all_evidence_ids() -> None:
    bundle = _make_bundle(12)
    before_ids = bundle.evidence_ids()
    before_tokens = bundle.token_estimate()

    result = Compactor(settings=offline_settings()).compact(bundle)

    assert len(bundle.steps) == KEEP_RECENT_STEPS, "最近 N 个 Step 必须保留"
    assert result.dropped_steps == [1, 2, 3, 4, 5, 6, 7, 8, 9]  # 全局序号 1..9
    assert result.kept_evidence_ids == before_ids, "FR-04：证据引用全集不丢"
    assert result.after_tokens < before_tokens
    assert result.before_tokens == before_tokens
    assert result.degraded is True and bundle.compacted is True  # 无 LLM → 确定性兜底


def test_m4_compactor_global_index_does_not_drop_latest_step() -> None:
    """回归护栏：`StepDigest.seq` 跨 Task 会重复，折叠口径必须用**全局序号**。"""
    bundle = ContextBundle(run_id="run-y", goal="多 Task 长会话")
    bundle.steps = [
        StepDigest(task_id=task, seq=seq, type="OBSERVE", tool="get_price_snapshot",
                   evidence_id=f"{task}-{seq}", payload={"seq": seq})
        for task in ("t1", "t2", "t3", "t4")
        for seq in (1, 2, 3)
    ]
    # 旧口径（按 seq ∈ dropped 过滤）会把这些 seq=1/2/3 的最新 Step 一并误删
    assert [s.seq for s in bundle.steps[-3:]] == [1, 2, 3]

    result = Compactor(settings=offline_settings()).compact(bundle)

    assert len(bundle.steps) == KEEP_RECENT_STEPS
    assert [s.task_id for s in bundle.steps] == ["t4", "t4", "t4"], "最新 Task 的 Step 未被误伤"
    assert result.dropped_steps == list(range(1, 10))


def test_m4_compactor_uses_model_decision_and_protects_recent(session: Session) -> None:
    """有 LLM 时采用模型摘要与折叠清单，但**最近 N 个 Step 仍强制保留**。"""

    def completer(provider, model, messages, schema_json, timeout_ms):  # type: ignore[no-untyped-def]
        return {
            "text": json.dumps(
                {"summary": "模型摘要：已折叠早期取证", "dropped_steps": [1, 2, 11], "kept_evidence_ids": []},
                ensure_ascii=False,
            ),
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            "cost": 0.0001,
        }

    settings = llm_settings()
    router = build_router(session=None, settings=settings, completer=completer)
    bundle = _make_bundle(12)

    result = Compactor(settings=settings, model_router=router).compact(bundle)

    assert result.degraded is False
    assert result.summary == "模型摘要：已折叠早期取证"
    assert result.dropped_steps == [1, 2], "11 落在最近 3 个（全局序号 10/11/12）内 → 被保护"
    assert bundle.summary == "模型摘要：已折叠早期取证"
    # 模型只请求折叠 [1,2,11]（11 被保护）→ 实际折叠 2 个、保留 10 个：
    # 「保护最近 keep_recent」= 最近 3 个不可被折叠，并非「只留最近 3 个」。
    assert len(bundle.steps) == 10
    assert [s.seq for s in bundle.steps[-3:]] == [10, 11, 12], "最近 3 个 Step 未被折叠"


# --------------------------------------------------------------------------- #
# 部件 ④：上下文组装 + 阈值触发 + 上报
# --------------------------------------------------------------------------- #
def _seed_steps(session: Session, run_id: str, *, count: int = 12) -> list[Evidence]:
    """建 1 个 Task + n 个 Step + n 条证据，返回与 Step 一一对应的 `Evidence` 对象。"""
    task = TaskRepository(session).create(run_id=run_id, seq=1, title="取证", intent="长会话")
    step_repo = StepRepository(session)
    evidences: list[Evidence] = []
    for index in range(1, count + 1):
        evidence_id = f"ev-{run_id}-{index}"
        step_repo.create(
            task_id=task.id,
            seq=index,
            type="OBSERVE",
            payload_json={"tool": "get_price_snapshot", "evidence_id": evidence_id, "sample": index},
        )
        evidences.append(
            Evidence(
                id=evidence_id,
                run_id=run_id,
                task_id=task.id,
                source="fuyao",
                interface="/mock/get_price_snapshot",
                as_of=now_ms(),
                unit="CNY",
                caliber="快照价",
                raw_ref=f"tool://get_price_snapshot#{index}",
                confidence=0.9,
                value_json={"sample": index, "padding": "x" * 40},
            )
        )
    return evidences


def test_m4_context_manager_triggers_compaction_and_reports_event(session: Session) -> None:
    _, thread_id, run_id = _bootstrap(session)
    evidences = _seed_steps(session, run_id, count=12)
    settings = _small_budget_settings()

    ctx = PipelineContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_id,
        session=session,
        settings=settings,
        registry=build_registry(session),
        evidences=list(evidences),
    )
    messages = ContextManager(session, settings=settings).build(ctx)

    assert ctx.extra["context_compacted"] is True
    assert messages[0]["role"] == "system" and messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert ctx.extra["context_evidence_ids"] == [ev.id for ev in evidences], "FR-04：引用全集不变"
    assert ctx.extra["context_tokens"] > 0

    compacted = [
        e for e in CheckpointEventRepository(session).list_by_run(run_id)
        if e.event_type == "ContextCompacted"
    ]
    assert len(compacted) == 1, "压缩事件必须上报（26 类契约事件之一）"
    payload = compacted[0].payload_json
    assert payload["before_tokens"] > payload["after_tokens"]
    assert payload["dropped_steps"] == list(range(1, 10))
    assert payload["kept_evidence_ids"] == [ev.id for ev in evidences]


def test_m4_context_manager_skips_compaction_below_threshold(session: Session) -> None:
    _, thread_id, run_id = _bootstrap(session)
    evidences = _seed_steps(session, run_id, count=2)
    settings = offline_settings()  # 阈值 = 100000 × 0.6，短上下文不会触发

    ctx = PipelineContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_id,
        session=session,
        settings=settings,
        registry=build_registry(session),
        evidences=list(evidences),
    )
    ContextManager(session, settings=settings).build(ctx)

    assert ctx.extra["context_compacted"] is False
    types = [e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)]
    assert "ContextCompacted" not in types


def test_m4_context_renders_tool_schemas_from_registry(session: Session) -> None:
    """部件 ① 补全：上下文渲染「工具 schema」（参数 + 必填标记），而非仅工具名。"""
    _, thread_id, run_id = _bootstrap(session)
    registry = build_registry(session, names=("get_price_snapshot",))
    registry.register(
        ToolSpec(
            name="get_price_snapshot",
            description="行情快照（真实参数 schema）",
            schema_json={
                "type": "object",
                "properties": {"ticker": {"type": "string"}, "as_of": {"type": "number"}},
                "required": ["ticker"],
            },
            domain="price",
        )
    )

    ctx = PipelineContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_id,
        session=session,
        settings=offline_settings(),
        registry=registry,
    )
    manager = ContextManager(session, settings=offline_settings())
    bundle = manager.gather(ctx)
    body = manager.build(ctx)[1]["content"]

    assert bundle.tool_schemas["get_price_snapshot"]["required"] == ["ticker"], "schema 取自 ToolSpec"
    assert "【可用工具】" in body
    assert "- get_price_snapshot(ticker*, as_of)" in body, "参数在列、必填带 * 标记"


# --------------------------------------------------------------------------- #
# 部件 ⑤：ContextInjectHook（记忆注入时机）
# --------------------------------------------------------------------------- #
def test_m4_context_inject_hook_injects_memories_and_messages(session: Session) -> None:
    _, thread_id, run_id = _bootstrap(session)
    MemoryStore(session, settings=offline_settings()).write(
        MemoryScope.SESSION,
        kind="preference",
        content="用户偏好：只关注半导体板块的资金流向",
        thread_id=thread_id,
    )

    ctx = PipelineContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_id,
        session=session,
        settings=offline_settings(),
        registry=build_registry(session),
    )
    result = ContextInjectHook().run(ctx)

    assert ctx.extra["context_injected"] is True
    assert ctx.extra["memories"], "应注入会话记忆"
    assert result.ok is True and result.short_circuit is False
    assert ctx.extra["context_injected_messages"][0]["role"] == "system"
    assert any("半导体" in line for line in ctx.extra["context_bundle"].memory_lines)


# --------------------------------------------------------------------------- #
# M4 验收 ①：长会话压缩 → 全部 evidence_id 仍可在 DB 解析（FR-04）
# --------------------------------------------------------------------------- #
def test_m4_long_session_compaction_keeps_every_evidence_resolvable(session: Session) -> None:
    _, thread_id, run_id = _bootstrap(session)
    pipeline = _pipeline(session)
    assert pipeline.run(run_id, ACCEPTANCE_GOAL).status == "COMPLETED"

    # 长会话：真实主链路落下的 Step 数远多于 12（含 THINK/ACT/OBSERVE）
    task_rows = TaskRepository(session).list_by_run(run_id)
    step_repo = StepRepository(session)
    step_count = sum(len(step_repo.list_by_task(task.id)) for task in task_rows)
    assert step_count >= 12, f"长会话需要 ≥12 个 Step，实际 {step_count}"

    evidence_rows = EvidenceRepository(session).list_by_run(run_id)
    assert len(evidence_rows) >= 5

    settings = _small_budget_settings()  # 强制触发压缩
    ctx = PipelineContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_id,
        session=session,
        settings=settings,
        registry=pipeline.registry,
        evidences=list(evidence_rows),
    )
    ContextManager(session, settings=settings).build(ctx)

    assert ctx.extra["context_compacted"] is True, "≥12 Step 的长会话必须触发压缩"
    kept_ids = ctx.extra["context_evidence_ids"]
    assert set(kept_ids) == {row.id for row in evidence_rows}, "压缩不得丢弃任何证据引用"

    # FR-04 核心验收：压缩后每个 evidence_id 仍可在 `evidences` 表解析
    repo = EvidenceRepository(session)
    for evidence_id in kept_ids:
        row = repo.get(evidence_id)
        assert row is not None, f"压缩后 evidence_id {evidence_id} 无法在 DB 解析"
        assert row.source and row.as_of is not None and row.unit and row.caliber

    types = [e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)]
    assert "ContextCompacted" in types


# --------------------------------------------------------------------------- #
# M4 验收 ②：新开 Thread 能召回历史偏好
# --------------------------------------------------------------------------- #
def test_m4_new_thread_recalls_historical_preference(session: Session) -> None:
    sm = SessionManager(session)
    thread_a = sm.create_thread(title="旧会话", goal=ACCEPTANCE_GOAL)
    run_a = sm.create_run(thread_id=thread_a.id)

    # 长期记忆（跨 Thread）：旧会话沉淀的用户偏好
    MemoryStore(session, settings=offline_settings()).write(
        MemoryScope.LONG,
        kind="preference",
        content="历史偏好：研究目标优先锁定半导体板块资金面",
        run_id=run_a.id,
    )

    # 新开 Thread B（全新线程 + 全新 Run）
    thread_b = sm.create_thread(title="新会话", goal=ACCEPTANCE_GOAL)
    run_b = sm.create_run(thread_id=thread_b.id)
    assert thread_b.id != thread_a.id

    ctx = PipelineContext(
        run_id=run_b.id,
        goal=ACCEPTANCE_GOAL,
        thread_id=thread_b.id,
        session=session,
        settings=offline_settings(),
        registry=build_registry(session),
    )
    ContextInjectHook().run(ctx)

    recalled = ctx.extra["memories"]
    assert recalled, "新 Thread 必须能召回长期记忆"
    assert any("半导体" in memory.content for memory in recalled)
    assert all(str(memory.scope) == str(MemoryScope.LONG) for memory in recalled)


# --------------------------------------------------------------------------- #
# M4 · Step 级停止规则（逐轮内嵌，复用同一组计量器）
# --------------------------------------------------------------------------- #
def test_m4_no_progress_resets_per_task_so_degraded_run_completes(session: Session) -> None:
    """全工具降级仍逐 Task 取证：`no_progress` 历史按 Task 重置，不得误判为空转。"""
    _, _, run_id = _bootstrap(session)
    registry = build_registry(
        session, impls={name: always_failing_impl(name) for name in FALLBACK_TOOLS}
    )
    result = _pipeline(session, registry=registry).run(run_id, ACCEPTANCE_GOAL)

    assert result.status == "COMPLETED", "每个 Task 内不足 NO_PROGRESS_STEPS 个空转动作 → 不得终止"
    assert all(status == "DEGRADED" for status in result.task_statuses.values())
    types = [e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)]
    assert "StopRuleHit" not in types

    # 反证：若跨 Task 累加（旧口径），第 4 个动作就会命中 NO_PROGRESS
    state = StopState(budget=BudgetManager(), no_progress_steps=4)
    rules = StopRuleSet()
    reasons = [
        rules.observe(state, StepView(signature=f"sig-{index}", new_evidence=0))
        for index in range(1, 8)
    ]
    assert reasons[3] is StopReason.NO_PROGRESS


def test_m4_step_level_no_progress_hits_inside_loop_and_shares_meters(session: Session) -> None:
    """单 Task 内 ≥4 个无证据动作 → 循环内即命中 `no_progress`，且用的就是注入的计量器。"""
    _, _, run_id = _bootstrap(session)
    task = TaskRepository(session).create(run_id=run_id, seq=1, title="全降级取证", intent="空转检测")

    tools = ["get_hot_rank", "get_limit_up_stats", "get_news_flash", "get_valuation"]
    registry = build_registry(
        session, names=tuple(tools), impls={name: always_failing_impl(name) for name in tools}
    )
    executor = ToolExecutor(registry, session=session, settings=offline_settings())
    loop = AgentLoop(build_router(session, settings=offline_settings()), executor, session=session)

    budget = BudgetManager(limits=BudgetLimits())
    stop_state = StopState(no_progress_steps=4, loop_detect_repeat=3, budget=budget)
    rules = StopRuleSet()
    loop_ctx = LoopContext(
        run_id=run_id,
        goal=ACCEPTANCE_GOAL,
        registry=registry,
        budget=budget,
        stop_state=stop_state,
        stop_rules=rules,
        llm_totals=lambda: (0.0, 0),
        started_ms=now_ms(),
    )

    outcome = loop.run_task(task, loop_ctx, tools=tools)

    assert loop_ctx.stop_reason == str(StopReason.NO_PROGRESS)
    assert loop_ctx.stop_rule == "no_progress" and rules.hit_rule == "no_progress"
    # 计量器就是注入的那一组（HANDOFF §17.5「不要另建计量器」）
    assert budget.used.steps == 4, "Step 级逐轮判定推进了 Pipeline 的同一 BudgetManager"
    acts = [s for s in StepRepository(session).list_by_task(task.id) if s.type == "ACT"]
    assert len(acts) == 4, "命中后即中断本轮循环，不多调工具"
    assert outcome.rounds == 4


def test_m4_step_level_max_steps_stops_run_and_lands_events(session: Session) -> None:
    """Step 级预算命中 → `runs.stop_reason` 落库 + `StopRuleHit`，Run 以 `STOPPED` 收口。"""
    _, _, run_id = _bootstrap(session)
    settings = offline_settings().model_copy(update={"budget_max_steps": 3})
    pipeline = _pipeline(session, settings=settings)

    result = pipeline.run(run_id, ACCEPTANCE_GOAL)

    assert result.status == str(RunStatus.STOPPED), "停止规则命中后 Run 必须以 STOPPED 收口"
    assert result.stop_reason == str(StopReason.MAX_STEPS)
    # 复用 Pipeline 的同一计量器：Task1（2 步）+ Task2（1 步）正好 3 步后命中
    assert pipeline.budget.used.steps == 3

    run_row = RunRepository(session).get(run_id)
    assert run_row is not None
    assert run_row.status == str(RunStatus.STOPPED)
    assert run_row.stop_reason == str(StopReason.MAX_STEPS)

    events = CheckpointEventRepository(session).list_by_run(run_id)
    types = [e.event_type for e in events]
    assert "StopRuleHit" in types and "RunStopped" in types
    hit = next(e for e in events if e.event_type == "StopRuleHit")
    assert hit.payload_json["rule"] == "max_steps"
    # 后续 Task 不再执行（第 3 个 Task 未落 `TaskStarted`）
    assert TaskStatus.RUNNING.value not in [
        t.status for t in TaskRepository(session).list_by_run(run_id) if t.seq == 3
    ]

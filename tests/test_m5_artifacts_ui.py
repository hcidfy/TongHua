"""M5 验收（一）：产物构建（C18）与动态 UI 渲染（C19）。

对应 03_BUILD_PLAN §3 M5 的两条渲染规则与验收口径：

- **渲染规则**：`可渲染条件 = 组件 ∈ 白名单(12) ∩ 前端已实现(7)`，否则整块降级为
  `FallbackJsonCard` 且 Trace 记 `render_status=failed`；
- **验收**：完整跑一次研究 → 7 个组件渲染成功；人为注入非法 props → 降级卡（不白屏）；
  `conclusion_card.layer=FACT` 且 `refs` 为空 → 渲染层拦下。

**离线确定性**：不触网、不读 `api-key.txt`、不依赖真实 LLM（沿用 `m1_support`）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.harness import ArtifactBuilder, Pipeline, SessionManager, UIRenderer
from app.harness.ui_renderer import (
    FALLBACK_COMPONENT,
    IMPLEMENTED_COMPONENT_COUNT,
    IMPLEMENTED_COMPONENTS,
    REQUIRED_PROPS,
    UI_COMPONENT_WHITELIST,
    UI_COMPONENT_WHITELIST_COUNT,
)
from app.observ import Tracer
from app.store.repo import (
    ArtifactRepository,
    CheckpointEventRepository,
    TraceRepository,
    UIBlockRepository,
)
from m1_support import ACCEPTANCE_GOAL, build_registry, offline_settings

#: 7 个已实现组件的「合法 props」（键必须存在，取值语义与架构 §11.2 一致）。
LEGAL_PROPS: dict[str, dict[str, object]] = {
    "plan_card": {"tasks": [{"seq": 1, "title": "t1", "intent": "i", "deps": [], "tools": []}]},
    "clarify_question": {"questions": [{"question": "范围？", "kind": "scope"}]},
    "evidence_card": {
        "evidence_id": "ev-1",
        "source": "mock",
        "as_of": 1704067200000,
        "unit": "CNY",
        "caliber": "快照价",
        "value": {"close": 10.0},
    },
    "evidence_table": {"evidence_ids": ["ev-1", "ev-2"]},
    "conclusion_card": {"layer": "FACT", "text": "事实陈述", "confidence": 0.9, "refs": ["ev-1"]},
    "approval_card": {"approval_id": "ap-1", "type": "plan", "summary": "待审批"},
    "degradation_notice": {"scope": "evidence", "reason": "源降级", "impact": "medium"},
}


def _bootstrap(session: Session) -> str:
    """建一条真实 Run 行（`traces.run_id` 有外键约束，不能用凭空 id）。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="M5 研究", goal=ACCEPTANCE_GOAL)
    return sm.create_run(thread_id=thread.id).id


def _renderer(session: Session | None = None, *, run_id: str = "") -> UIRenderer:
    tracer = Tracer(session, run_id=run_id) if session is not None else None
    return UIRenderer(settings=offline_settings(), tracer=tracer)


# --------------------------------------------------------------------------- #
# 1. 契约数字（白名单 12 / 已实现 7）
# --------------------------------------------------------------------------- #
def test_m5_ui_whitelist_and_implemented_counts_are_frozen() -> None:
    assert len(UI_COMPONENT_WHITELIST) == UI_COMPONENT_WHITELIST_COUNT == 12
    assert len(IMPLEMENTED_COMPONENTS) == IMPLEMENTED_COMPONENT_COUNT == 7
    assert set(IMPLEMENTED_COMPONENTS) <= set(UI_COMPONENT_WHITELIST)
    # 24h 只收窄实现集：后置 5 个仍在白名单内
    assert set(UI_COMPONENT_WHITELIST) - set(IMPLEMENTED_COMPONENTS) == {
        "metric_block",
        "series_chart",
        "distribution_chart",
        "citation_list",
        "trace_ref",
    }
    # 每个已实现组件都声明了必填 props
    assert all(name in REQUIRED_PROPS for name in IMPLEMENTED_COMPONENTS)


# --------------------------------------------------------------------------- #
# 2. 7 个组件渲染成功（合法 props → ok）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("component", IMPLEMENTED_COMPONENTS)
def test_m5_each_implemented_component_renders_ok(component: str) -> None:
    refs = ["ev-1"] if component == "conclusion_card" else []
    blocks = _renderer().render(
        [{"component": component, "props_json": LEGAL_PROPS[component], "refs_json": refs}]
    )
    assert len(blocks) == 1
    assert blocks[0].component == component
    assert blocks[0].render_status == "ok"


def test_m5_renders_all_seven_components_in_one_pass() -> None:
    descriptors = [
        {
            "component": name,
            "props_json": LEGAL_PROPS[name],
            "refs_json": ["ev-1"] if name == "conclusion_card" else [],
        }
        for name in IMPLEMENTED_COMPONENTS
    ]
    blocks = _renderer().render(descriptors)
    assert [b.component for b in blocks] == list(IMPLEMENTED_COMPONENTS)
    assert all(b.render_status == "ok" for b in blocks)
    # 流式增量按 seq 递增（架构 §11.3-3）
    assert [b.seq for b in blocks] == list(range(1, len(blocks) + 1))


# --------------------------------------------------------------------------- #
# 3. 非法 props / 未实现 / 白名单外 → 降级卡 + Trace render_status=failed
# --------------------------------------------------------------------------- #
def test_m5_invalid_props_degrade_instead_of_blank(session: Session) -> None:
    """人为注入非法 props（缺必填）→ 整块降级为 FallbackJsonCard（不白屏）+ Trace 留痕。"""
    run_id = _bootstrap(session)
    blocks = _renderer(session, run_id=run_id).render(
        [{"component": "evidence_card", "props_json": {}, "refs_json": []}]
    )
    block = blocks[0]
    assert block.component == FALLBACK_COMPONENT
    assert block.render_status == "failed"
    # 原始块原样带进 props，前端降级卡可展示 JSON
    assert block.props_json["component"] == "evidence_card"
    assert "缺少必填 props" in block.props_json["reason"]

    # Trace 记录 render_status=failed（03_BUILD_PLAN §3 M5 渲染规则原文）
    traces = [row for row in TraceRepository(session).list_by_run(run_id) if row.name == "render"]
    assert traces, "降级必须在 Trace 中留下 render Span"
    assert traces[-1].attrs_json["render_status"] == "failed"


def test_m5_not_implemented_component_degrades() -> None:
    """白名单内但属「后置 5 个」→ 降级（24h 收窄实现集）。"""
    blocks = _renderer().render(
        [{"component": "metric_block", "props_json": {"label": "x"}, "refs_json": []}]
    )
    assert blocks[0].component == FALLBACK_COMPONENT
    assert "未实现" in blocks[0].props_json["reason"]


def test_m5_unknown_component_degrades() -> None:
    """白名单外（模型凭空造组件）→ 降级（架构 §11.3-1）。"""
    blocks = _renderer().render([{"component": "evil_card", "props_json": {}, "refs_json": []}])
    assert blocks[0].component == FALLBACK_COMPONENT
    assert "不在白名单" in blocks[0].props_json["reason"]


def test_m5_fact_conclusion_without_refs_is_blocked(session: Session) -> None:
    """无引用的 FACT 卡被渲染层拦下（架构 §11.3-2）。"""
    run_id = _bootstrap(session)
    blocks = _renderer(session, run_id=run_id).render(
        [
            {
                "component": "conclusion_card",
                "props_json": {
                    "layer": "FACT",
                    "text": "凭空结论",
                    "confidence": 0.9,
                    "refs": [],
                },
                "refs_json": [],
            }
        ]
    )
    assert blocks[0].component == FALLBACK_COMPONENT
    assert blocks[0].render_status == "failed"
    assert "refs 为空" in blocks[0].props_json["reason"]


def test_m5_block_budget_truncates() -> None:
    """`UI_BLOCK_MAX` 上限生效（超出截断，防动态 UI 无限增长）。"""
    renderer = UIRenderer(settings=offline_settings(), max_blocks=3)
    descriptors = [
        {"component": "evidence_table", "props_json": {"evidence_ids": [f"ev-{i}"]}}
        for i in range(10)
    ]
    blocks = renderer.render(descriptors)
    assert len(blocks) == 3


# --------------------------------------------------------------------------- #
# 4. C18：三层产物（不可合并 / 推断必带切换条件 / C1 无操作建议）
# --------------------------------------------------------------------------- #
class _Ev:
    """最小证据替身（C3 字段齐全）。"""

    def __init__(
        self,
        eid: str,
        interface: str = "/mock/get_price_snapshot",
        degrade_reason: str | None = None,
    ) -> None:
        self.id = eid
        self.source = "mock"
        self.interface = interface
        self.as_of = 1704067200000
        self.unit = "CNY"
        self.caliber = "快照价"
        self.raw_ref = f"mock://{eid}"
        self.confidence = 0.8
        self.value_json = {"close": 10.0}
        self.degrade_reason = degrade_reason


def test_m5_artifact_builds_three_layers_with_condition_and_citations() -> None:
    builder = ArtifactBuilder(settings=offline_settings())
    built = builder.build_from(
        "run-1",
        goal=ACCEPTANCE_GOAL,
        evidences=[
            _Ev("ev-1"),
            _Ev("ev-2", "/mock/get_valuation"),
            # 一条降级证据 → 不确定层必须有显式缺口（C4：降级不得静默）
            _Ev("ev-3", "/mock/get_news_flash", degrade_reason="主源不可用，已降级备源"),
        ],
        with_detail=True,
    )
    # 三层各自独立（C2：不可合并）
    assert built.facts and built.inferences and built.uncertainties
    # 事实：逐条证据一条，refs 恒非空
    assert [f["refs"] for f in built.facts] == [["ev-1"], ["ev-2"], ["ev-3"]]
    assert "close" in built.facts[0]["text"] and "10.0" in built.facts[0]["text"]
    # 推断：必须带切换条件
    assert all(str(item.get("condition") or "").strip() for item in built.inferences)
    # 不确定：降级证据必须显式成条
    assert any("ev-3" in u["refs"] for u in built.uncertainties)
    # 引用清单可反查（evidence_id → tool_call → span）
    assert [c["evidence_id"] for c in built.citations] == ["ev-1", "ev-2", "ev-3"]
    # C1：不含任何操作建议 / 涨跌预测字段
    forbidden = {"advice", "action", "suggestion", "target_price", "rating", "buy", "sell"}
    for layer in (built.facts, built.inferences, built.uncertainties):
        for item in layer:
            assert not (forbidden & set(item)), f"C1 违规字段：{forbidden & set(item)}"


def test_m5_artifact_inference_condition_defaulted_when_missing() -> None:
    builder = ArtifactBuilder(settings=offline_settings())
    built = builder.build_from(
        "run-1",
        evidences=[_Ev("ev-1")],
        payload={"inferences": [{"text": "资金面偏紧", "refs": ["ev-1"]}]},
        with_detail=True,
    )
    assert built.inferences[0]["text"] == "资金面偏紧"
    assert built.inferences[0]["condition"], "缺省切换条件必须被补齐"


# --------------------------------------------------------------------------- #
# 5. 端到端：完整跑一次研究 → 产物 + 动态 UI 落库
# --------------------------------------------------------------------------- #
def test_m5_end_to_end_run_lands_artifact_and_ui_blocks(session: Session) -> None:
    run_id = _bootstrap(session)
    pipeline = Pipeline(
        session=session,
        settings=offline_settings(),
        registry=build_registry(session),
    )
    result = pipeline.run(run_id, ACCEPTANCE_GOAL)

    assert result.status == "COMPLETED"
    assert result.artifact_id, "Run 收口必须落产物"
    assert result.ui_block_count > 0

    artifacts = ArtifactRepository(session).list_by_run(run_id)
    assert len(artifacts) == 1
    assert artifacts[0].facts_json and artifacts[0].inferences_json
    # 推断必须带切换条件（落库形态同样成立）
    assert all(str(i.get("condition") or "").strip() for i in artifacts[0].inferences_json)

    blocks = UIBlockRepository(session).list_by_run(run_id)
    assert [b.seq for b in blocks] == list(range(1, len(blocks) + 1))
    # 本次 Run 的块全部是可渲染的（组件 ∈ 已实现集）
    components = {b.component for b in blocks}
    assert components <= set(IMPLEMENTED_COMPONENTS)
    assert result.render_failed_count == 0
    # 证据可点：evidence_table/evidence_card 的 refs 非空
    for block in blocks:
        if block.component in {"evidence_table", "evidence_card", "conclusion_card"}:
            assert block.refs_json, f"{block.component} 必须带可点击引用"

    # `ArtifactBuilt` 事件（SSE `artifact` 的唯一载体）
    types = [e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)]
    assert "ArtifactBuilt" in types

"""M6 验收（03_BUILD_PLAN §3 M6）——可观测与合规。

覆盖 5 个组件与 3 条接口契约：

- `observ/tracer.py`：`build_span_tree()` / `Tracer.tree()`（**查询与展示**；埋点已在 M1）；
- `observ/cost.py`：`CostReporter.summary(run_id) -> CostSummary`（口径唯一来源
  `LlmCallRepository.totals_for_run`）；
- `observ/sanitize.py`：Key / 隐私脱敏（**日志中不出现 API Key**）；
- `compliance/guard.py`：`Guard.check(artifact) -> GuardVerdict`（三类检查）；
- `api/traces.py`：`/api/observability/runs/{id}/spans|cost|compliance|metrics`。

验收原文（03_BUILD_PLAN §3 M6）：
    任一结论能在 Trace 中定位到工具调用；注入「建议买入某股」→ Guard 拦截并记录；
    日志中不出现 API Key。

**离线确定性**：`create_app(settings=offline_settings(), engine=engine)` 一次注入，
因此进程内**不读 `docs/api-key.txt`**、不触网、不依赖真实 LLM。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api import create_app
from app.compliance import (
    CATEGORY_ADVICE,
    CATEGORY_FACT_NO_REF,
    CATEGORY_INFERENCE_NO_CONDITION,
    GUARD_FORBIDDEN_FIELDS,
    GUARD_REDLINE_COUNT,
    GUARD_REDLINES,
    Guard,
    GuardVerdict,
)
from app.config.settings import Settings
from app.harness import Pipeline, SessionManager
from app.observ import (
    REDACTED,
    CostReporter,
    Sanitizer,
    Tracer,
    build_span_tree,
    sanitize_text,
    sanitizer_from_settings,
)
from app.store.db import session_scope
from app.store.repo import (
    CheckpointEventRepository,
    LlmCallRepository,
    RunMetricRepository,
    TraceRepository,
)
from m1_support import ACCEPTANCE_GOAL, build_registry, offline_settings

#: 含字母的 32 位十六进制实体主键（`new_id()` 形态）——用于「脱敏不误伤主键」断言。
HEX_ID = "0123456789abcdef0123456789abcdef"

#: 用于脱敏断言的伪密钥（≥8 字符才参与替换，见 `settings._MIN_SECRET_LEN`）。
FAKE_KEY = "supersecretvalue123"


class _FakeArtifact:
    """最小产物替身：仅三层数组（`Guard._layer` 按 `*_json` 取）。"""

    def __init__(
        self,
        *,
        facts: list[dict[str, object]] | None = None,
        inferences: list[dict[str, object]] | None = None,
        uncertainties: list[dict[str, object]] | None = None,
    ) -> None:
        self.id = "af-test"
        self.facts_json = list(facts or [])
        self.inferences_json = list(inferences or [])
        self.uncertainties_json = list(uncertainties or [])


@pytest.fixture()
def client(engine: Engine) -> TestClient:
    """离线确定性客户端：注入 `offline_settings()` + 内存库。"""
    return TestClient(create_app(settings=offline_settings(), engine=engine))


def _bootstrap(session: Session) -> str:
    """建一条真实 Run 行（`traces.run_id` 有外键约束，不能用凭空 id），返回 run_id。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="M6 可观测", goal=ACCEPTANCE_GOAL)
    return sm.create_run(thread_id=thread.id).id


def _run_once(client: TestClient) -> str:
    """建线程 → 同步跑完一次 Run（`background=false`，离线确定性），返回 run_id。"""
    thread = client.post("/api/threads", json={"goal": ACCEPTANCE_GOAL}).json()
    created = client.post(
        f"/api/threads/{thread['thread_id']}/runs", json={"background": False}
    ).json()
    return created["run_id"]


# --------------------------------------------------------------------------- #
# 1. Guard：红线表 + 契约枚举 + 三类检查
# --------------------------------------------------------------------------- #
def test_m6_guard_redline_table_and_verdict_contract() -> None:
    """红线表是内置数据常量；`GuardVerdict` 是本模块枚举，取值即契约字面量。"""
    assert GUARD_REDLINE_COUNT == len(GUARD_REDLINES) > 0
    # 三条主线各有代表：承诺 / 预测 / 建议
    categories = {category for _, category in GUARD_REDLINES}
    assert {"PROMISE", "PREDICTION", "ADVICE"} <= categories
    assert str(GuardVerdict.PASS) == "PASS"
    assert str(GuardVerdict.REJECTED) == "REJECTED"
    assert "target_price" in GUARD_FORBIDDEN_FIELDS


def test_m6_guard_passes_clean_artifact() -> None:
    artifact = _FakeArtifact(
        facts=[{"text": "行情快照已取得", "refs": ["ev-1"]}],
        inferences=[{"text": "覆盖度充足", "condition": "若数据被覆盖则失效"}],
    )
    result = Guard(settings=offline_settings()).inspect(artifact)
    assert result.verdict is GuardVerdict.PASS
    assert result.findings == []
    assert result.summary() == "PASS"


def test_m6_guard_rejects_injected_advice_text() -> None:
    """验收：注入「建议买入某股」→ Guard 拦截并记录（ADVICE）。"""
    artifact = _FakeArtifact(
        facts=[{"text": "建议买入贵州茅台", "refs": ["ev-1"]}],
        inferences=[{"text": "覆盖度充足", "condition": "若数据被覆盖则失效"}],
    )
    guard = Guard(settings=offline_settings())
    result = guard.inspect(artifact)

    assert guard.check(artifact) is GuardVerdict.REJECTED
    assert result.rejected is True
    assert CATEGORY_ADVICE in result.categories
    finding = next(f for f in result.findings if f.category == CATEGORY_ADVICE)
    assert finding.rule == "建议买入"
    assert finding.location == "facts[0]"
    assert "ADVICE" in result.summary()
    assert result.to_dict()["verdict"] == "REJECTED"


def test_m6_guard_rejects_forbidden_structure_field() -> None:
    """C1：产物条目出现 `target_price` 等禁止字段即拦截（结构校验）。"""
    artifact = _FakeArtifact(
        inferences=[{"text": "结论", "target_price": 100.0, "condition": "若…"}]
    )
    result = Guard(settings=offline_settings()).inspect(artifact)
    assert result.verdict is GuardVerdict.REJECTED
    assert CATEGORY_ADVICE in result.categories


def test_m6_guard_rejects_fact_without_refs() -> None:
    """无引用的 FACT 不得发布（架构 §11.3-2 的产物层那一半）。"""
    artifact = _FakeArtifact(facts=[{"text": "不可反查的结论", "refs": []}])
    result = Guard(settings=offline_settings()).inspect(artifact)
    assert result.verdict is GuardVerdict.REJECTED
    assert result.categories == [CATEGORY_FACT_NO_REF]


def test_m6_guard_rejects_inference_without_condition() -> None:
    """C2：推断必须可失效（缺 `condition` 即拦截）。"""
    artifact = _FakeArtifact(inferences=[{"text": "推断", "refs": ["ev-1"]}])
    result = Guard(settings=offline_settings()).inspect(artifact)
    assert result.verdict is GuardVerdict.REJECTED
    assert result.categories == [CATEGORY_INFERENCE_NO_CONDITION]


def test_m6_guard_non_strict_only_blocks_structural_defects() -> None:
    """`GUARD_STRICT=false`：文本类红线降为提示，结构性缺陷仍然拦截。"""
    text_hit = _FakeArtifact(
        facts=[{"text": "建议买入贵州茅台", "refs": ["ev-1"]}],
        inferences=[{"text": "推断", "condition": "若…"}],
    )
    result = Guard(strict=False).inspect(text_hit)
    assert result.verdict is GuardVerdict.PASS
    assert result.categories == [CATEGORY_ADVICE]  # 记录但不拦截

    structural = _FakeArtifact(facts=[{"text": "无引用", "refs": []}])
    assert Guard(strict=False).inspect(structural).verdict is GuardVerdict.REJECTED


# --------------------------------------------------------------------------- #
# 2. Sanitizer：Key / 隐私脱敏，且不误伤主键 / 数值
# --------------------------------------------------------------------------- #
def test_m6_sanitizer_masks_keys_and_pii() -> None:
    sanitizer = Sanitizer(secrets=[FAKE_KEY])
    masked = sanitizer.mask(
        f"key={FAKE_KEY} mail=a@b.com phone=13800138000 id={HEX_ID}"
    )
    assert FAKE_KEY not in masked
    assert "a@b.com" not in masked
    assert "13800138000" not in masked
    assert masked.count(REDACTED) >= 3


def test_m6_sanitize_text_default_patterns() -> None:
    assert "a@b.com" not in sanitize_text("mail=a@b.com")
    assert sanitize_text("plain", patterns=()) == "plain"


def test_m6_sanitizer_does_not_damage_ids_or_non_string_values() -> None:
    """脱敏副作用规避：32 位十六进制主键不被改写；数值/布尔/None 原样保留。"""
    sanitizer = Sanitizer(secrets=[FAKE_KEY])
    assert HEX_ID in sanitizer.mask(f"span_id={HEX_ID}")

    for value in (1, 2.5, True, None):
        assert sanitizer.mask_any(value) == value
        assert isinstance(sanitizer.mask_any(value), type(value))
    assert REDACTED in sanitizer.mask_any("call 13800138000")


def test_m6_sanitizer_from_settings_uses_secret_values() -> None:
    # `_env_file=None`：显式屏蔽本地 `.env`（在线演示用的真实密钥不得污染断言，测试须离线确定）
    settings = Settings(_env_file=None, mock_data_mode=True, fuyao_api_key=FAKE_KEY)
    assert settings.secret_values == [FAKE_KEY]
    assert settings.sanitize_pattern_list == []  # 未配置 → 回落内置隐私集
    masked = sanitizer_from_settings(settings).mask(f"t={FAKE_KEY}")
    assert FAKE_KEY not in masked


def test_m6_tracer_persists_sanitized_and_numeric_attrs(session: Session) -> None:
    """验收「日志中不出现 API Key」+ 数值属性不退化（`attempts` 仍为 int）。"""
    run_id = _bootstrap(session)
    tracer = Tracer(
        session, run_id=run_id, sanitize=Sanitizer(secrets=[FAKE_KEY]).mask_any
    )
    with tracer.span("tool", attempts=1) as span:
        # `name` 与 `span(name, **attrs)` 的形参同名 → 进入 Span 后再 set（同 ToolExecutor 口径）
        span.set(name="get_price_snapshot", token=f"k={FAKE_KEY}")

    rows = TraceRepository(session).list_by_run(run_id)
    tool = next(row for row in rows if row.name == "tool")
    assert tool.attrs_json["attempts"] == 1
    assert isinstance(tool.attrs_json["attempts"], int)
    assert FAKE_KEY not in str(tool.attrs_json["token"])


# --------------------------------------------------------------------------- #
# 3. Span 树：扁平落库 → 嵌套（任一结论可定位到工具调用）
# --------------------------------------------------------------------------- #
def test_m6_build_span_tree_nests_and_promotes_orphans() -> None:
    class _Row:
        def __init__(self, span_id: str, parent: str | None, name: str) -> None:
            self.span_id = span_id
            self.parent_span_id = parent
            self.name = name
            self.status = "OK"
            self.started_at = 1
            self.duration_ms = 1
            self.attrs_json = {}

    rows = [
        _Row("s1", None, "task#1"),
        _Row("s2", "s1", "loop#1"),
        _Row("s3", "s2", "tool"),
        _Row("orphan", "missing", "loop#9"),  # 父缺失 → 提升为根
    ]
    tree = build_span_tree(rows)
    assert {node["span_id"] for node in tree} == {"s1", "orphan"}
    root = next(node for node in tree if node["span_id"] == "s1")
    assert [c["span_id"] for c in root["children"]] == ["s2"]
    assert [c["span_id"] for c in root["children"][0]["children"]] == ["s3"]


def test_m6_tracer_tree_reflects_landed_spans(session: Session) -> None:
    run_id = _bootstrap(session)
    tracer = Tracer(session, run_id=run_id)
    with tracer.span("run"):
        with tracer.span("task#1"):
            with tracer.span("loop#1"):
                with tracer.span("tool") as span:
                    span.set(name="get_price_snapshot")

    tree = tracer.tree()
    assert len(tree) == 1
    task = tree[0]["children"][0]
    assert task["name"] == "task#1"
    tool = task["children"][0]["children"][0]
    assert tool["name"] == "tool"
    assert tool["attrs"]["name"] == "get_price_snapshot"


# --------------------------------------------------------------------------- #
# 4. Cost：口径唯一来源（与 `llm_calls` 聚合逐字一致）
# --------------------------------------------------------------------------- #
def test_m6_cost_reporter_matches_totals_source(session: Session) -> None:
    run_id = _bootstrap(session)
    repo = LlmCallRepository(session)
    repo.create(run_id=run_id, provider="p", model="m1", status="OK",
               prompt_tokens=10, completion_tokens=5, total_tokens=15, cost=0.01)
    repo.create(run_id=run_id, provider="p", model="m2", status="OK",
               prompt_tokens=20, completion_tokens=8, total_tokens=28, cost=0.02)
    repo.create(run_id=run_id, provider="p", model="m1", status="FAILED",
               prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=0.99)

    summary = CostReporter(session).summary(run_id)
    cost_total, tokens_total = repo.totals_for_run(run_id)
    assert summary.cost_total == pytest.approx(cost_total)
    assert summary.tokens_total == tokens_total
    assert summary.llm_calls == 2
    assert summary.failed_calls == 1
    assert summary.prompt_tokens == 30 and summary.completion_tokens == 13
    # Σ by_model.cost == cost_total（分项之和 = 总计，口径无裂缝）
    assert sum(b["cost"] for b in summary.by_model.values()) == pytest.approx(cost_total)
    assert "m1" in summary.by_model and "m2" in summary.by_model
    assert summary.to_dict()["run_id"] == run_id


# --------------------------------------------------------------------------- #
# 5. 查询端点（/api/observability/...）
# --------------------------------------------------------------------------- #
def test_m6_observability_endpoints(client: TestClient, engine: Engine) -> None:
    run_id = _run_once(client)

    spans = client.get(f"/api/observability/runs/{run_id}/spans").json()
    assert spans["count"] > 0
    assert spans["roots"] >= 1
    assert spans["tool_calls"] >= 1, "任一结论都要能在 Trace 中定位到工具调用"
    assert len(spans["tree"]) == spans["roots"]
    assert any("attrs" in span for span in spans["flat"])
    assert any(span["name"] == "tool" for span in spans["flat"])

    cost = client.get(f"/api/observability/runs/{run_id}/cost").json()
    assert cost["run_id"] == run_id
    assert cost["status"] == "COMPLETED"
    # 与 M5 冻结的 `/api/runs/{id}/cost` 同口径（都取自 llm_calls 聚合）
    legacy = client.get(f"/api/runs/{run_id}/cost").json()
    assert cost["cost_total"] == legacy["cost_total"]
    assert cost["tokens_total"] == legacy["tokens_total"]

    compliance = client.get(f"/api/observability/runs/{run_id}/compliance").json()
    assert compliance["artifact_id"]
    assert compliance["verdict"] == "PASS"
    assert compliance["findings"] == []

    metrics = client.get(f"/api/observability/runs/{run_id}/metrics").json()
    assert metrics["count"] > 0
    names = {row["metric_name"] for row in metrics["metrics"]}
    assert {"evidence_count", "ui_block_count", "cost_cny", "guard_findings"} <= names
    guard_metric = next(r for r in metrics["metrics"] if r["metric_name"] == "guard_findings")
    assert guard_metric["metric_value"] == 0


def test_m6_observability_unknown_run_is_404(client: TestClient) -> None:
    for suffix in ("spans", "cost", "compliance", "metrics"):
        assert client.get(f"/api/observability/runs/不存在/{suffix}").status_code == 404


def test_m6_compliance_endpoint_without_artifact(client: TestClient, engine: Engine) -> None:
    with session_scope(engine) as session:
        run_id = _bootstrap(session)

    body = client.get(f"/api/observability/runs/{run_id}/compliance").json()
    assert body["verdict"] == "PASS"
    assert body["artifact_id"] is None
    assert "note" in body


# --------------------------------------------------------------------------- #
# 6. 端到端：正常 Run 的合规判定与指标落库；注入建议 → 拦截并记录
# --------------------------------------------------------------------------- #
def _pipeline(session: Session) -> Pipeline:
    return Pipeline(session=session, settings=offline_settings(), registry=build_registry(session))


def test_m6_pipeline_records_clean_run_and_metrics(session: Session) -> None:
    run_id = _bootstrap(session)
    result = _pipeline(session).run(run_id, ACCEPTANCE_GOAL)

    assert result.guard_verdict == "PASS"  # 干净产物：安检通过（GuardPost 收口）
    metrics = {row.metric_name: row.metric_value for row in RunMetricRepository(session).list_by_run(run_id)}
    assert metrics["guard_findings"] == 0
    assert metrics["evidence_count"] > 0
    assert metrics["ui_block_count"] > 0
    # `replace_for_run` 幂等：同一 Run 的指标不重复累积
    assert len(RunMetricRepository(session).list_by_run(run_id)) == len(metrics)


def test_m6_pipeline_guard_rejects_advice_artifact(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收：注入含「建议买入某股」的产物 → Guard 拦截 + `GuardRejected` 事件 + 指标记录。"""
    advice = {"inferences": [{"text": "建议买入贵州茅台", "condition": "若数据被覆盖则失效"}]}
    monkeypatch.setattr(Pipeline, "_artifact_payload", staticmethod(lambda ctx: advice))

    run_id = _bootstrap(session)
    result = _pipeline(session).run(run_id, ACCEPTANCE_GOAL)

    assert result.guard_verdict and result.guard_verdict.startswith("REJECTED")
    event_types = [e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)]
    assert "GuardRejected" in event_types

    rows = TraceRepository(session).list_by_run(run_id)
    guard_span = next(row for row in rows if row.name == "guard")
    assert guard_span.attrs_json["verdict"] == "REJECTED"
    assert guard_span.attrs_json["findings"] >= 1

    metrics = {row.metric_name: row.metric_value for row in RunMetricRepository(session).list_by_run(run_id)}
    assert metrics["guard_findings"] >= 1

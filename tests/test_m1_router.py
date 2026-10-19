"""M1：模型路由（架构 C14、§10；03_BUILD_PLAN §3 M1）。

断言四件事：角色→档位映射、结构化输出重试、超时/熔断/failover、**降级不崩**。
全部离线：`completer` 注入确定性假实现（见 `m1_support.scripted_completer`）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.harness import SessionManager
from app.harness.model_router import (
    AI_ROLES,
    MAX_SCHEMA_RETRIES,
    ROLE_MODEL_TIER,
    LLMError,
    ModelRouter,
    cost_from_usage,
)
from app.store.repo import LlmCallRepository
from m1_support import llm_settings, offline_settings, scripted_completer

#: Planner 角色的最小输出契约（`tasks` 是 `scripted_completer` 的角色判据）。
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"tasks": {"type": "array"}},
    "required": ["tasks"],
}
PLAN_PAYLOAD = {"tasks": [{"title": "t", "intent": "i", "tools": []}]}
MESSAGES = [{"role": "user", "content": "研究目标：半导体板块最近资金情况"}]


def _run_id(session: Session) -> str:
    sm = SessionManager(session)
    thread = sm.create_thread(title="路由测试", goal="g")
    return sm.create_run(thread_id=thread.id).id


# --------------------------------------------------------------------------- #
# 契约面
# --------------------------------------------------------------------------- #
def test_m1_router_contract_surface() -> None:
    assert AI_ROLES == ("Clarifier", "Planner", "Reasoner", "Compactor", "Synthesizer", "Guard")
    assert len(AI_ROLES) == 6
    assert MAX_SCHEMA_RETRIES == 2
    # 轻任务走小模型档位，其余走旗舰档位（§10.1）
    assert ROLE_MODEL_TIER["Clarifier"] == ROLE_MODEL_TIER["Compactor"] == "small"
    assert {
        ROLE_MODEL_TIER[role] for role in ("Planner", "Reasoner", "Synthesizer", "Guard")
    } == {"primary"}


def test_m1_router_role_tier_selects_model(session: Session) -> None:
    seen: list[str] = []

    def completer(provider, model, messages, schema_json, timeout_ms):
        seen.append(model)
        return {"text": "{}", "usage": {}, "cost": 0.0}

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    router.complete("Clarifier", MESSAGES)
    router.complete("Reasoner", MESSAGES)
    assert seen == ["small-model", "primary-model"]


# --------------------------------------------------------------------------- #
# 降级不崩（HANDOFF §4.3 派生条款）
# --------------------------------------------------------------------------- #
def test_m1_router_unconfigured_degrades_without_raising(session: Session) -> None:
    run_id = _run_id(session)
    router = ModelRouter(session, settings=offline_settings())

    result = router.complete("Planner", MESSAGES, PLAN_SCHEMA, run_id=run_id)
    assert result.status == "DEGRADED" and result.degraded is True and result.ok is False
    assert "未配置" in (result.error or "")

    rows = LlmCallRepository(session).list_by_run(run_id)
    assert len(rows) == 1 and rows[0].status == "DEGRADED"


# --------------------------------------------------------------------------- #
# 结构化输出 + 审计落库
# --------------------------------------------------------------------------- #
def test_m1_router_records_llm_call_and_run_totals(session: Session) -> None:
    run_id = _run_id(session)
    router = ModelRouter(
        session,
        settings=llm_settings(),
        completer=scripted_completer(plan=[PLAN_PAYLOAD]),
    )

    result = router.complete("Planner", MESSAGES, PLAN_SCHEMA, run_id=run_id)
    assert result.ok is True and result.parsed == PLAN_PAYLOAD
    assert (result.total_tokens, result.cost) == (15, 0.001)
    assert result.call_id is not None

    cost, tokens = LlmCallRepository(session).totals_for_run(run_id)
    assert (cost, tokens) == pytest.approx((0.001, 15))


def test_m1_router_schema_retry_twice_then_succeeds(session: Session) -> None:
    calls = {"n": 0}

    def completer(provider, model, messages, schema_json, timeout_ms):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"text": "这不是 JSON", "usage": {}, "cost": 0.0}
        return {"text": json.dumps(PLAN_PAYLOAD, ensure_ascii=False), "usage": {}, "cost": 0.0}

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    result = router.complete("Planner", MESSAGES, PLAN_SCHEMA)

    assert result.ok is True and result.parsed == PLAN_PAYLOAD
    assert calls["n"] == MAX_SCHEMA_RETRIES + 1  # 首轮 + 2 次重试


def test_m1_router_schema_always_invalid_degrades(session: Session) -> None:
    def completer(provider, model, messages, schema_json, timeout_ms):
        return {"text": "{}", "usage": {}, "cost": 0.0}  # 缺 tasks

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    result = router.complete("Planner", MESSAGES, PLAN_SCHEMA)

    assert result.status == "DEGRADED"
    assert "结构化输出校验失败" in (result.error or "")


# --------------------------------------------------------------------------- #
# failover / 熔断
# --------------------------------------------------------------------------- #
def test_m1_router_failover_after_transient_error(session: Session) -> None:
    calls = {"n": 0}

    def completer(provider, model, messages, schema_json, timeout_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("模拟上游超时", retryable=True)
        return {"text": json.dumps(PLAN_PAYLOAD), "usage": {}, "cost": 0.0}

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    result = router.complete("Planner", MESSAGES, PLAN_SCHEMA)

    assert result.ok is True and result.failover is True and calls["n"] == 2


def test_m1_router_breaker_opens_after_three_failures(session: Session) -> None:
    def completer(provider, model, messages, schema_json, timeout_ms):
        raise LLMError("上游 5xx", retryable=True)

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    first = router.complete("Guard", MESSAGES)
    assert first.status == "DEGRADED" and "上游 5xx" in (first.error or "")

    second = router.complete("Guard", MESSAGES)
    assert second.status == "DEGRADED"
    assert "熔断器开路" in (second.error or "")


def test_m1_router_non_retryable_error_skips_failover(session: Session) -> None:
    calls = {"n": 0}

    def completer(provider, model, messages, schema_json, timeout_ms):
        calls["n"] += 1
        raise LLMError("请求被拒 HTTP 400", code=400, retryable=False)

    router = ModelRouter(session, settings=llm_settings(), completer=completer)
    result = router.complete("Synthesizer", MESSAGES)

    assert result.status == "DEGRADED"
    assert calls["n"] == 1, "不可重试错误不得 failover"


# --------------------------------------------------------------------------- #
# 计价（`llm_calls.cost` → `runs.cost_total` 的源头）
# --------------------------------------------------------------------------- #
_USAGE = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}


def test_m1_cost_from_usage_uses_configured_unit_price() -> None:
    # 未配置单价（0）→ 不计价、恒 0（不编造单价）
    assert cost_from_usage(_USAGE, input_per_mtok=0.0, output_per_mtok=0.0) == 0.0
    assert cost_from_usage(None, input_per_mtok=2.0, output_per_mtok=8.0) == 0.0
    # 2 元 / 8 元 每百万 token：(2×1000 + 8×500) / 1e6 = 0.006 元
    assert cost_from_usage(_USAGE, input_per_mtok=2.0, output_per_mtok=8.0) == pytest.approx(0.006)


def test_m1_http_complete_prices_usage_into_run_totals(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认 `_http_complete` 必须按单价把 `usage` 折算进 `llm_calls.cost`。

    回归护栏：此处曾把 cost 硬编码为 `0.0`，导致「调用成功但 `cost_total` 恒为 0」
    （见 `docs/解决文档.md` §2.6）。
    """

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": dict(_USAGE),
            }

    monkeypatch.setattr("app.harness.model_router.httpx.post", lambda *a, **k: _FakeResponse())
    settings = llm_settings().model_copy(
        update={"llm_price_input_per_mtok_cny": 2.0, "llm_price_output_per_mtok_cny": 8.0}
    )
    run_id = _run_id(session)
    router = ModelRouter(session, settings=settings)

    result = router.complete("Reasoner", MESSAGES, run_id=run_id)

    assert result.ok is True and result.total_tokens == 1500
    assert result.cost == pytest.approx(0.006)
    cost, tokens = LlmCallRepository(session).totals_for_run(run_id)
    assert (cost, tokens) == pytest.approx((0.006, 1500))

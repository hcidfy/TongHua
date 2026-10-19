"""M1：规划器（架构 C2；03_BUILD_PLAN §3 M1）。

两条硬约束的断言：
1. 工具名必须来自 `ToolRegistry`（§10.2 字段白名单）——幻觉工具名**拒收并留痕**；
2. LLM 不可用**不得崩**（§10.3）——转确定性兜底规划，保证主链路仍能取证。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.harness.planner import FALLBACK_TEMPLATE, Planner, build_fallback_dag, sanitize_tasks
from m1_support import ACCEPTANCE_GOAL, FALLBACK_TOOLS, build_router, llm_settings, scripted_completer


def test_m1_sanitize_rejects_hallucinated_tools_and_dedupes() -> None:
    tasks, dropped = sanitize_tasks(
        [
            {
                "title": "取行情",
                "intent": "i",
                "tools": ["get_price_snapshot", "hallucinated_tool", "get_price_snapshot"],
                "deps": [1],
            }
        ],
        ["get_price_snapshot", "get_hot_rank"],
    )
    assert dropped == ["hallucinated_tool"]
    assert [t.tools for t in tasks] == [["get_price_snapshot"]]  # 去重且保序
    assert tasks[0].seq == 1 and tasks[0].deps == [1]


def test_m1_sanitize_keeps_task_without_tools() -> None:
    tasks, dropped = sanitize_tasks([{"title": "梳理", "intent": "i"}], ["get_hot_rank"])
    assert dropped == []
    assert len(tasks) == 1 and tasks[0].tools == [] and tasks[0].title == "梳理"


def test_m1_fallback_dag_only_uses_registered_tools() -> None:
    allowed = ["get_price_snapshot", "get_valuation", "get_hot_rank"]
    dag = build_fallback_dag(ACCEPTANCE_GOAL, allowed)
    assert dag.source == "fallback" and dag.degraded is True
    assert dag.task_count == len(FALLBACK_TEMPLATE)
    assert set(dag.tool_names) <= set(allowed)
    assert "search_ticker" not in dag.tool_names  # 未注册的候选工具被过滤掉


def test_m1_fallback_template_covers_m1_acceptance_tools() -> None:
    """M1 验收「≥3 Task / ≥5 Evidence」依赖兜底模板覆盖 8 个契约数据工具。"""
    covered: list[str] = []
    for _title, _intent, candidates in FALLBACK_TEMPLATE:
        covered.extend(candidates)
    assert set(covered) == set(FALLBACK_TOOLS)
    assert len(FALLBACK_TOOLS) == 8
    assert ("get_financials",) in [candidates for _t, _i, candidates in FALLBACK_TEMPLATE]


def test_m1_planner_degrades_to_fallback_without_llm(session: Session) -> None:
    planner = Planner(build_router(session))
    dag = planner.plan(ACCEPTANCE_GOAL, tools=list(FALLBACK_TOOLS))

    assert dag.source == "fallback" and dag.degraded is True
    assert dag.task_count == len(FALLBACK_TEMPLATE)
    assert set(dag.tool_names) <= set(FALLBACK_TOOLS)
    assert "兜底规划" in (dag.note or "")


def test_m1_planner_without_registered_tools_returns_empty_dag(session: Session) -> None:
    dag = Planner(build_router(session)).plan(ACCEPTANCE_GOAL, tools=[])
    assert dag.tasks == []
    assert "未注册任何工具" in (dag.note or "")


def test_m1_planner_uses_llm_plan_and_drops_hallucinated_tools(session: Session) -> None:
    completer = scripted_completer(
        plan=[
            {
                "tasks": [
                    {
                        "title": "取行情",
                        "intent": "i",
                        "tools": ["get_price_snapshot", "get_bogus_tool"],
                        "deps": [],
                    },
                    {"title": "看热度", "intent": "i", "tools": ["get_hot_rank"], "deps": [1]},
                ]
            }
        ]
    )
    router = build_router(session, settings=llm_settings(), completer=completer)
    allowed = ["get_price_snapshot", "get_hot_rank"]
    dag = Planner(router).plan(ACCEPTANCE_GOAL, tools=allowed)

    assert dag.source == "llm"
    assert dag.dropped_tools == ["get_bogus_tool"]
    assert dag.degraded is True  # 出现拒收即标记降级（不静默放行）
    assert dag.task_count == 2
    assert [t.tools for t in dag.tasks] == [["get_price_snapshot"], ["get_hot_rank"]]
    assert set(dag.tool_names) <= set(allowed)

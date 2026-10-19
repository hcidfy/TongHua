"""Harness 内核：规划器（架构 C2；03_BUILD_PLAN §3 M1）。

契约（架构 §3.2 C2）::

    Planner.plan(goal, tools) -> TaskDAG

职责：把一句话目标拆成 **Task DAG**（任务 + 依赖 + 每个任务拟调用的工具）。

两条硬约束：
1. **工具名必须来自 `ToolRegistry`**（架构 §10.2「字段白名单：工具名必须来自真实集合」）。
   模型给出的非法工具名一律**拒收**（丢弃并记录到 `dropped_tools`），不做静默放行。
2. **LLM 不可用不得崩**（§10.3「全部 LLM 不可用 → 降级研判」）：`ModelRouter` 返回降级时，
   走**确定性兜底规划**（模板化 Task DAG），保证主链路仍能跑通并取证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .model_router import ModelRouter

__all__ = [
    "PlannedTask",
    "TaskDAG",
    "Planner",
    "sanitize_tasks",
    "FALLBACK_TEMPLATE",
]

#: 兜底计划模板：`(标题, 意图, 候选工具)`。工具按 Registry 实际注册项过滤。
FALLBACK_TEMPLATE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "定位标的与板块",
        "解析目标涉及的标的 / 板块代码，确认研究范围",
        ("search_ticker", "get_index_constituents"),
    ),
    (
        "取行情与估值",
        "拉取核心标的的行情快照与估值水平，建立基准",
        ("get_price_snapshot", "get_valuation"),
    ),
    (
        "看热度与消息面",
        "统计涨跌停与热榜热度，并检索相关资讯",
        ("get_hot_rank", "get_limit_up_stats", "get_news_flash"),
    ),
    (
        "看财报与收入结构",
        "拉取近几期利润表，建立收入 / 成本 / 费用 / 利润的结构基线",
        ("get_financials",),
    ),
)


class _PlanTask(BaseModel):
    """LLM 结构化输出中的单个任务（Planner 角色的输出契约）。"""

    title: str
    intent: str
    tools: list[str] = Field(default_factory=list)
    deps: list[int] = Field(default_factory=list)


class _PlanOutput(BaseModel):
    """Planner 角色的输出契约（至少要有一个任务）。"""

    tasks: list[_PlanTask] = Field(min_length=1)


@dataclass(slots=True)
class PlannedTask:
    """计划中的一个任务节点（尚未落库；落库后的 `TaskRow` 由 Pipeline 创建）。"""

    seq: int
    title: str
    intent: str
    tools: list[str] = field(default_factory=list)
    deps: list[int] = field(default_factory=list)


@dataclass(slots=True)
class TaskDAG:
    """目标 → 任务 DAG 的规划结果。"""

    goal: str
    tasks: list[PlannedTask] = field(default_factory=list)
    #: `llm`（模型规划）或 `fallback`（降级兜底规划）。
    source: str = "fallback"
    degraded: bool = False
    #: 被拒收的非法工具名（不在 Registry 中）——用于 Trace 与调试。
    dropped_tools: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def tool_names(self) -> list[str]:
        """本计划涉及的全部工具名（去重保序）。"""
        seen: list[str] = []
        for task in self.tasks:
            for name in task.tools:
                if name not in seen:
                    seen.append(name)
        return seen


def sanitize_tasks(
    raw_tasks: list[dict[str, Any]],
    allowed: list[str],
) -> tuple[list[PlannedTask], list[str]]:
    """把模型输出的任务列表裁剪为**只含已注册工具**的任务；返回 `(任务, 被拒收工具名)`。

    - 非法工具名直接丢弃（不静默放行，符合 §10.2 字段白名单）；
    - 某任务裁剪后无可用工具时，该任务仍保留（仅为「定位/复核」类任务），
      但不会产生工具调用——由 `AgentLoop` 判定为无证据任务。
    """
    allowed_set = set(allowed)
    dropped: list[str] = []
    tasks: list[PlannedTask] = []
    for raw in raw_tasks:
        raw_tools = [str(t) for t in (raw.get("tools") or [])]
        valid: list[str] = []
        for name in raw_tools:
            if name in allowed_set:
                if name not in valid:
                    valid.append(name)
            elif name not in dropped:
                dropped.append(name)
        seq = len(tasks) + 1
        tasks.append(
            PlannedTask(
                seq=seq,
                title=str(raw.get("title") or f"任务{seq}"),
                intent=str(raw.get("intent") or ""),
                tools=valid,
                deps=[int(d) for d in (raw.get("deps") or []) if isinstance(d, int)],
            )
        )
    return tasks, dropped


def build_fallback_dag(goal: str, allowed: list[str], *, note: str | None = None) -> TaskDAG:
    """确定性兜底规划：模板工具按 Registry 实际注册项过滤（保证工具名 100% 合法）。"""
    allowed_set = set(allowed)
    tasks: list[PlannedTask] = []
    for title, intent, candidates in FALLBACK_TEMPLATE:
        tools = [name for name in candidates if name in allowed_set]
        tasks.append(
            PlannedTask(
                seq=len(tasks) + 1,
                title=title,
                intent=intent,
                tools=tools,
                deps=[],
            )
        )
    return TaskDAG(goal=goal, tasks=tasks, source="fallback", degraded=True, note=note)


class Planner:
    """目标 → Task DAG。LLM 可用时由 Planner 角色产出，否则走兜底模板。"""

    def __init__(self, model_router: ModelRouter, *, max_tasks: int = 8) -> None:
        self.model_router = model_router
        self.max_tasks = max(1, max_tasks)

    # --- 契约入口 ---
    def plan(
        self,
        goal: str,
        *,
        tools: list[str],
        run_id: str | None = None,
    ) -> TaskDAG:
        """规划一次 Run 的任务 DAG；`tools` 是字段白名单（来自 `ToolRegistry.names()`）。"""
        if not tools:
            return TaskDAG(
                goal=goal,
                tasks=[],
                source="fallback",
                degraded=True,
                note="Registry 未注册任何工具，无法规划",
            )

        result = self.model_router.complete(
            "Planner",
            [{"role": "user", "content": self._user_message(goal, tools)}],
            _PlanOutput,
            run_id=run_id,
        )
        if not result.ok or not result.parsed:
            return build_fallback_dag(
                goal,
                tools,
                note=f"模型不可用，转兜底规划（{result.error or result.status}）",
            )

        raw_tasks = list(result.parsed.get("tasks") or [])[: self.max_tasks]
        if not raw_tasks:
            return build_fallback_dag(goal, tools, note="模型未给出任务，转兜底规划")

        tasks, dropped = sanitize_tasks(raw_tasks, tools)
        return TaskDAG(
            goal=goal,
            tasks=tasks,
            source="llm",
            degraded=bool(dropped),
            dropped_tools=dropped,
        )

    # --- 内部 ---
    @staticmethod
    def _user_message(goal: str, tools: list[str]) -> str:
        return (
            f"研究目标：{goal}\n"
            f"可用工具（只能用这些名字）：{', '.join(tools)}\n"
            "请把目标拆成 3–6 个可执行的 Task，输出 JSON："
            '{"tasks":[{"title":"...","intent":"...","tools":["工具名"],"deps":[依赖的任务序号]}]}。'
            "每个 Task 的 tools 只能从上面的清单里选；tools 可以为空（表示只做梳理/复核）。"
        )

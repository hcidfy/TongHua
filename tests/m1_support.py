"""M1 测试公共构件：契约工具集 + 确定性工具实现 + 可脚本化 ModelRouter。

设计原则：**离线确定性**——不触网、不依赖 `api-key.txt`、不依赖真实 LLM。
`ModelRouter` 的 `completer` 参数就是为这种注入留的口子（见 `model_router.py`）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.data.errors import RateLimited
from app.domain import Phase, ToolSpec
from app.harness import ModelRouter, ToolRegistry
from app.harness.tool_executor import ToolResult

#: 9 个契约数据工具（03_BUILD_PLAN §3 M2 首批 8 个 + 提前落地的 `get_financials`）。
#: M1 用它们的**名字**做规划与白名单校验（实现体归 M2，本模块用桩函数绑定）。
DATA_TOOLS: tuple[tuple[str, str], ...] = (
    ("search_ticker", "meta"),
    ("get_price_snapshot", "price"),
    ("get_price_history", "price"),
    ("get_index_constituents", "market"),
    ("get_valuation", "fundamental"),
    ("get_limit_up_stats", "heat"),
    ("get_hot_rank", "heat"),
    ("get_news_flash", "event"),
    ("get_financials", "fundamental"),
)

#: Planner 兜底模板覆盖到的 8 个工具——M1 验收「≥3 Task / ≥5 Evidence」即由它们产出。
#: （第 8 个 `get_financials` 为「收入 / 财务结构」类目标而补，见 `docs/解决文档.md` §5.1。）
FALLBACK_TOOLS: tuple[str, ...] = (
    "search_ticker",
    "get_index_constituents",
    "get_price_snapshot",
    "get_valuation",
    "get_hot_rank",
    "get_limit_up_stats",
    "get_news_flash",
    "get_financials",
)

#: M1 验收目标（03_BUILD_PLAN §3 M1 原文）。
ACCEPTANCE_GOAL = "半导体板块最近资金情况"


# --------------------------------------------------------------------------- #
# 配置 / 路由器
# --------------------------------------------------------------------------- #
def offline_settings() -> Settings:
    """未配置 LLM 的配置：`is_llm_configured=False` → ModelRouter 一律降级、绝不触网。"""
    return Settings(
        mock_data_mode=True,
        fuyao_api_key=None,
        llm_provider_primary="openai-compatible",
        llm_base_url_primary="",
        llm_api_key_primary=None,
        llm_model_primary="",
        llm_model_small="",
    )


def llm_settings() -> Settings:
    """已配置 LLM 的配置（配合 `scripted_completer` 使用，仍然离线）。"""
    return Settings(
        mock_data_mode=True,
        fuyao_api_key=None,
        llm_provider_primary="openai-compatible",
        llm_base_url_primary="https://llm.invalid/v1",
        llm_api_key_primary="test-key-0123456789",
        llm_model_primary="primary-model",
        llm_model_small="small-model",
        llm_max_failover=2,
    )


def scripted_completer(
    *,
    plan: list[dict[str, Any]] | None = None,
    reasoner: list[dict[str, Any]] | None = None,
) -> Any:
    """返回一个**按角色出队**的确定性 completer。

    角色判定依据 schema 的顶层属性名（Planner 输出 `tasks`，Reasoner 输出 `action`），
    不依赖提示词文本，稳定且与 `ModelRouter` 的真实调用形态一致。
    """
    plan_queue = list(plan or [])
    reasoner_queue = list(reasoner or [])

    def completer(
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        schema_json: dict[str, Any] | None,
        timeout_ms: int,
    ) -> dict[str, Any]:
        props = set((schema_json or {}).get("properties") or {})
        if "tasks" in props:
            payload: dict[str, Any] = (
                plan_queue.pop(0) if plan_queue else {"tasks": [{"title": "t", "intent": "i"}]}
            )
        elif "action" in props:
            payload = (
                reasoner_queue.pop(0)
                if reasoner_queue
                else {"thought": "收口", "action": "finish"}
            )
        else:
            payload = {}
        return {
            "text": json.dumps(payload, ensure_ascii=False),
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "cost": 0.001,
        }

    return completer


# --------------------------------------------------------------------------- #
# 工具注册表
# --------------------------------------------------------------------------- #
def make_spec(name: str, domain: str = "meta") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name}（M1 测试桩）",
        schema_json={"type": "object", "properties": {}, "required": []},
        domain=domain,
        phase=Phase.EXPLORE,
    )


def ok_result(name: str, args: dict[str, Any], *, source: str = "fuyao") -> ToolResult:
    return ToolResult(
        tool_name=name,
        data={"tool": name, "args": dict(args), "sample": 1.0},
        source=source,
        interface=f"/mock/{name}",
        unit="CNY",
        caliber="快照价",
    )


def ok_impl(name: str, *, source: str = "fuyao") -> Any:
    """确定性成功实现。"""

    def impl(args: dict[str, Any]) -> ToolResult:
        return ok_result(name, args, source=source)

    return impl


def flaky_impl(name: str, *, fail_times: int, error_factory: Any = RateLimited) -> Any:
    """先失败 `fail_times` 次（可重试异常），之后成功——用于「退避重试」断言。"""
    state = {"calls": 0}

    def impl(args: dict[str, Any]) -> ToolResult:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise error_factory(f"{name} 模拟限流（第 {state['calls']} 次）")
        return ok_result(name, args)

    impl.calls = state  # type: ignore[attr-defined]
    return impl


def always_failing_impl(name: str, *, error_factory: Any = RateLimited) -> Any:
    """永远抛可重试异常——用于「退避重试后最终降级」断言。"""
    return flaky_impl(name, fail_times=10**9, error_factory=error_factory)


def build_registry(
    session: Session | None = None,
    *,
    names: tuple[str, ...] | None = None,
    impls: dict[str, Any] | None = None,
    fallback_impls: dict[str, Any] | None = None,
) -> ToolRegistry:
    """按契约工具名注册 `ToolSpec` 并绑定桩实现。"""
    selected = names if names is not None else tuple(name for name, _ in DATA_TOOLS)
    registry = ToolRegistry(session)
    for name, domain in DATA_TOOLS:
        if name in selected:
            registry.register(make_spec(name, domain))
    overrides = impls or {}
    for name in selected:
        registry.bind(name, overrides.get(name) or ok_impl(name))
    for name, impl in (fallback_impls or {}).items():
        registry.bind(name, overrides.get(name) or ok_impl(name), fallback=impl)
    return registry


def build_router(
    session: Session | None = None,
    *,
    settings: Settings | None = None,
    completer: Any = None,
) -> ModelRouter:
    return ModelRouter(session, settings=settings or offline_settings(), completer=completer)

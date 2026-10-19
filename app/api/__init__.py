"""M5 REST / SSE 应用装配（03_BUILD_PLAN §3 M5）。

`create_app()` 是**唯一的装配入口**：一次注入 `settings` + `session_factory`，
全部端点（含后台线程驱动的 Run）共用同一套依赖——这是「测试注入 `offline_settings()`
后进程内不读 `docs/api-key.txt`」的实现方式（离线确定性，HANDOFF §18.5）。

路由清单（架构 §13）：

| 模块 | 端点 |
| --- | --- |
| `threads.py` | `POST/GET /api/threads`、`GET /api/threads/{id}`、`POST /api/threads/{id}/runs` |
| `runs.py` | `GET /api/runs/{id}` + `evidences/artifacts/traces/cost`、`POST /api/runs/{id}/pause|resume|abort|plan` |
| `stream.py` | `GET /api/runs/{id}/stream`（**SSE 10 类事件**） |
| `approvals.py` | `GET /api/approvals`、`POST /api/approvals/{id}/decide`（调用 M3 `HITLGate`） |
| `traces.py`（M6） | `GET /api/observability/runs/{id}/spans|cost|compliance|metrics` |
| 本文件 | `GET /api/health`、`GET /api/tools`（运维/自检） |

启动方式（不在此处实例化，避免 import 期读配置/建库）::

    uvicorn app.api:app --factory        # 或 uvicorn app.api:create_app --factory
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import EVENT_TYPE_COUNT
from app.harness.ui_renderer import IMPLEMENTED_COMPONENTS, UI_COMPONENT_WHITELIST
from app.store import init_db, session_scope
from app.store.schema import TABLE_COUNT

from . import approvals, runs, stream, threads, traces
from .common import SSE_EVENT_COUNT, SSE_EVENT_TYPES

__all__ = ["create_app", "app"]


# 本地联调固定放行前端开发地址，解决浏览器对 JSON POST 的 OPTIONS 预检 405。
_LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def create_app(
    *,
    settings: Any = None,
    session_factory: Callable[[], Session] | None = None,
    engine: Engine | None = None,
    bootstrap: bool = True,
) -> FastAPI:
    """装配 FastAPI 应用。

    - `settings`：缺省 `get_settings()`（生产）；测试传 `offline_settings()`；
    - `session_factory`：缺省 `session_scope`（绑定 `engine`，未给则用全局引擎）；
    - `bootstrap=True`：按 ORM 元数据建表（15 张，`create_all` 幂等）。
    """
    resolved = settings if settings is not None else get_settings()
    factory: Callable[[], Any]
    if session_factory is not None:
        factory = session_factory
    elif engine is not None:
        factory = lambda: session_scope(engine)  # noqa: E731 - 仅做参数绑定
    else:
        factory = session_scope

    if bootstrap:
        init_db(engine)

    application = FastAPI(
        title="InvestmentXBuddy API",
        version="0.1.0",
        description="自研 Agent Harness 的 REST + SSE 服务面（M5）。",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_LOCAL_DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(threads.router)
    application.include_router(runs.router)
    application.include_router(stream.router)
    application.include_router(approvals.router)
    application.include_router(traces.router)

    # 依赖注入的单一事实来源（`common.get_db` / `get_app_settings` 从这里取）。
    application.state.settings = resolved
    application.state.session_factory = factory

    @application.get("/api/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """存活 + 契约自检（数字全部取自代码常量，不写死）。"""
        return {
            "status": "ok",
            "env": str(getattr(resolved, "app_env", "")),
            "mock_data_mode": bool(getattr(resolved, "is_mock", True)),
            "llm_configured": bool(getattr(resolved, "is_llm_configured", False)),
            "contracts": {
                "tables": TABLE_COUNT,
                "events": EVENT_TYPE_COUNT,
                "sse_events": SSE_EVENT_COUNT,
                "ui_whitelist": len(UI_COMPONENT_WHITELIST),
                "ui_implemented": len(IMPLEMENTED_COMPONENTS),
                "render_strict": bool(getattr(resolved, "render_strict", True)),
                "ui_block_max": int(getattr(resolved, "ui_block_max", 0) or 0),
            },
        }

    @application.get("/api/tools", tags=["ops"])
    def list_tools() -> dict[str, Any]:
        """9 个受控工具的对外声明（8 数据 + 1 内部；给 LLM 的 schema 同源）。

        直接由蓝图派生（不构造客户端），因此**不触网、不读密钥**。
        """
        from app.tools import DATA_BLUEPRINTS, INTERNAL_BLUEPRINTS

        specs = [bp.to_spec() for bp in DATA_BLUEPRINTS + INTERNAL_BLUEPRINTS]
        return {
            "count": len(specs),
            "sse_events": list(SSE_EVENT_TYPES),
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "domain": spec.domain,
                    "phase": str(spec.phase),
                    "permission_level": str(spec.permission_level),
                    "idempotent": bool(spec.idempotent),
                    "timeout_ms": int(spec.timeout_ms),
                    "est_cost": float(spec.est_cost),
                    "schema_json": dict(spec.schema_json or {}),
                }
                for spec in sorted(specs, key=lambda s: s.name)
            ],
        }

    return application


def __getattr__(name: str) -> Any:
    """惰性提供模块级 `app`（`uvicorn app.api:app` 可用）。

    刻意**不在 import 期实例化**：那会在导入时读配置 / 建库，破坏测试的离线确定性。
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

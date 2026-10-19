"""`api/traces.py`：可观测与合规的查询面（03_BUILD_PLAN §3 M6）。

覆盖 M6 的三个展示接口 + 看板指标：

| 端点 | 内容 |
| --- | --- |
| `GET /api/observability/runs/{id}/spans` | **Span 树**（嵌套；扁平落库 → 前端可树形展开） |
| `GET /api/observability/runs/{id}/cost` | **成本汇总**（`CostReporter`，口径唯一来源 `llm_calls`） |
| `GET /api/observability/runs/{id}/compliance` | **合规报告**（对最新产物跑 `Guard` 三类检查） |
| `GET /api/observability/runs/{id}/metrics` | 运行期指标（`run_metrics`，看板数据） |

**为什么另起 `/api/observability` 前缀**：`/api/runs/{id}/traces` 与 `/api/runs/{id}/cost`
在 M5 已冻结（`runs.py`，面向「Run 详情页的原始清单」）；本模块面向「可观测看板」，
输出的是**加工后**的结构（嵌套树 / 汇总对象 / 合规判定），且不得改动 M5 的既有响应形状。
两条路由并存，互不覆盖（不新增表 / 不新增事件 / 不新增枚举）。

**离线确定性**：只读本地库 + 纯函数 Guard，不触网、不调 LLM。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.compliance import Guard
from app.observ import CostReporter, build_span_tree
from app.store.repo import ArtifactRepository, RunMetricRepository, RunRepository, TraceRepository

from .common import get_app_settings, get_db, not_found, trace_dict

__all__ = ["router"]

router = APIRouter(prefix="/api/observability", tags=["observ"])


def _require_run(session: Session, run_id: str) -> Any:
    run = RunRepository(session).get(run_id)
    if run is None:
        raise not_found("Run", run_id)
    return run


@router.get("/runs/{run_id}/spans")
def get_span_tree(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Span 树：`run → plan → task#N → loop#M → llm/tool/compact`（嵌套 + 扁平双份）。

    验收口径（03_BUILD_PLAN §3 M6）：「任一结论能在 Trace 中定位到工具调用」——
    `flat` 里每个 `tool` Span 带 `attrs.name`（工具名）与 `parent_span_id`
    （可选指明是哪个 Loop / Task 发起）。
    """
    _require_run(session, run_id)
    rows = TraceRepository(session).list_by_run(run_id)
    tree = build_span_tree(rows)
    tool_spans = [row for row in rows if str(getattr(row, "name", "")) == "tool"]
    return {
        "run_id": run_id,
        "count": len(rows),
        "roots": len(tree),
        "tool_calls": len(tool_spans),
        "failed": sum(1 for row in rows if str(getattr(row, "status", "OK")) != "OK"),
        "tree": tree,
        "flat": [trace_dict(row) for row in rows],
    }


@router.get("/runs/{run_id}/cost")
def get_cost_summary(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """成本汇总（`CostReporter.summary`；与 `runs.cost_total` 同口径）。"""
    run = _require_run(session, run_id)
    payload = CostReporter(session).summary(run_id).to_dict()
    payload["status"] = str(getattr(run, "status", ""))
    return payload


@router.get("/runs/{run_id}/compliance")
def get_compliance_report(
    run_id: str,
    session: Session = Depends(get_db),
    settings: Any = Depends(get_app_settings),
) -> dict[str, Any]:
    """合规报告：对**最新产物**跑三类检查（预测/承诺/建议、无引用 FACT、推断无切换条件）。"""
    _require_run(session, run_id)
    artifact = ArtifactRepository(session).latest(run_id)
    if artifact is None:
        return {
            "run_id": run_id,
            "artifact_id": None,
            "verdict": "PASS",
            "strict": bool(getattr(settings, "guard_strict", True)),
            "summary": "PASS",
            "findings": [],
            "note": "本次 Run 尚无产物，无待检内容",
        }
    report = Guard(settings=settings).inspect(artifact).to_dict()
    return {"run_id": run_id, "artifact_id": str(getattr(artifact, "id", "") or ""), **report}


@router.get("/runs/{run_id}/metrics")
def list_run_metrics(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """运行期指标（看板数据；口径见 `RunMetricRepository`）。"""
    _require_run(session, run_id)
    rows = RunMetricRepository(session).list_by_run(run_id)
    return {
        "run_id": run_id,
        "count": len(rows),
        "metrics": [
            {
                "metric_name": str(getattr(row, "metric_name", "")),
                "metric_value": float(getattr(row, "metric_value", 0.0) or 0.0),
                "unit": getattr(row, "unit", None),
                "tags": dict(getattr(row, "tags_json", None) or {}),
            }
            for row in rows
        ],
    }

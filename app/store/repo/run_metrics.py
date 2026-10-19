"""run_metrics 表 CRUD（运行期指标，供 M6 看板）。

归属 M6（`repo/__init__.py` 的历史注释：「run_metrics 归属 M6，届时再补」）。
表结构在 M0 就冻结存在（`TABLE_COUNT` 恒 15），本模块**只补 Repository，不新增表**。

指标口径（可观测看板）：迭代数 / 工具调用数 / 降级次数 / 证据数 / UI 块数 / 渲染降级数
等；`tags_json` 承载维度标签（如 `{"scope": "render"}`）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain import new_id, now_ms
from app.store.schema import RunMetricRow

from .base import BaseRepository

__all__ = ["RunMetricRepository"]


class RunMetricRepository(BaseRepository[RunMetricRow]):
    model = RunMetricRow

    def create(
        self,
        *,
        run_id: str,
        metric_name: str,
        metric_value: float,
        unit: str | None = None,
        tags_json: dict[str, Any] | None = None,
        created_at: int | None = None,
    ) -> RunMetricRow:
        return self.add(
            id=new_id(),
            run_id=run_id,
            metric_name=metric_name,
            metric_value=float(metric_value),
            unit=unit,
            tags_json=dict(tags_json or {}),
            created_at=created_at if created_at is not None else now_ms(),
        )

    def list_by_run(self, run_id: str) -> list[RunMetricRow]:
        stmt = (
            select(RunMetricRow)
            .where(RunMetricRow.run_id == run_id)
            .order_by(RunMetricRow.created_at, RunMetricRow.metric_name)
        )
        return list(self.session.scalars(stmt).all())

    def latest(self, run_id: str, metric_name: str) -> RunMetricRow | None:
        stmt = (
            select(RunMetricRow)
            .where(RunMetricRow.run_id == run_id, RunMetricRow.metric_name == metric_name)
            .order_by(RunMetricRow.created_at.desc())
        )
        return self.session.scalars(stmt).first()

    def names(self, run_id: str) -> list[str]:
        return [row.metric_name for row in self.list_by_run(run_id)]

    def replace_for_run(
        self, run_id: str, metrics: list[tuple[str, float, str | None]]
    ) -> list[RunMetricRow]:
        """**幂等**写入：先清空本 Run 的旧指标再落新值。

        幂等的必要性：一次 Run 可能因恢复/改判计划多次收口（`_finalize_outputs` 会重跑），
        若只追加会出现「同一 Run 多份指标」的口径歧义。
        """
        for row in self.list(run_id=run_id):
            self.session.delete(row)
        self.session.flush()
        return [
            self.create(run_id=run_id, metric_name=name, metric_value=value, unit=unit)
            for name, value, unit in metrics
        ]

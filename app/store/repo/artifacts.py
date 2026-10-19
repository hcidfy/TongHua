"""artifacts 表 CRUD（M5 补；03_BUILD_PLAN §3 M5）。

`Artifact` 的三层数组（`facts / inferences / uncertainties`）**不可合并**（C2），
另有引用清单 `citations`（`citation → evidence` 可反查，架构 §12.2）。
`run_metrics` 表仍归属 M6，本文件只补 artifacts。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import new_id, now_ms
from app.store.schema import ArtifactRow

from .base import BaseRepository

__all__ = ["ArtifactRepository"]


class ArtifactRepository(BaseRepository[ArtifactRow]):
    model = ArtifactRow

    def create(
        self,
        *,
        run_id: str,
        facts_json: list[Any] | None = None,
        inferences_json: list[Any] | None = None,
        uncertainties_json: list[Any] | None = None,
        citations_json: list[Any] | None = None,
        version: int = 1,
        artifact_id: str | None = None,
        created_at: int | None = None,
    ) -> ArtifactRow:
        return self.add(
            id=artifact_id or new_id(),
            run_id=run_id,
            version=version,
            facts_json=list(facts_json or []),
            inferences_json=list(inferences_json or []),
            uncertainties_json=list(uncertainties_json or []),
            citations_json=list(citations_json or []),
            created_at=created_at if created_at is not None else now_ms(),
        )

    def list_by_run(self, run_id: str) -> list[ArtifactRow]:
        stmt = (
            select(ArtifactRow)
            .where(ArtifactRow.run_id == run_id)
            .order_by(ArtifactRow.version, ArtifactRow.created_at)
        )
        return list(self.session.scalars(stmt).all())

    def latest(self, run_id: str) -> ArtifactRow | None:
        """取该 Run 的**最高版本**产物（重跑时版本递增，前端取最新）。"""
        rows = self.list_by_run(run_id)
        return rows[-1] if rows else None

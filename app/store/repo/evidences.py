"""evidences 表 CRUD。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.domain.models import new_id, now_ms
from app.store.schema import EvidenceRow

from .base import BaseRepository

__all__ = ["EvidenceRepository"]


class EvidenceRepository(BaseRepository[EvidenceRow]):
    """`evidences` 表 CRUD + **C17 `EvidenceStore` 契约**（`put` / `resolve`，M5 补）。"""

    model = EvidenceRow

    def create(
        self,
        *,
        run_id: str,
        task_id: str,
        source: str,
        interface: str,
        as_of: int | None,
        unit: str | None,
        caliber: str | None,
        raw_ref: str,
        confidence: float | None,
        tool_call_id: str | None = None,
        fetched_at: int | None = None,
        staleness_ms: int | None = None,
        value_json: Any = None,
        degrade_reason: str | None = None,
    ) -> EvidenceRow:
        """C3 强制字段为**关键字必填**：缺失由 Python 直接报错，不静默补默认。"""
        return self.add(
            id=new_id(),
            run_id=run_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
            source=source,
            interface=interface,
            as_of=as_of,
            fetched_at=fetched_at if fetched_at is not None else now_ms(),
            staleness_ms=staleness_ms,
            unit=unit,
            caliber=caliber,
            value_json=value_json,
            raw_ref=raw_ref,
            confidence=confidence,
            degrade_reason=degrade_reason,
            created_at=now_ms(),
        )

    def list_by_run(self, run_id: str) -> list[EvidenceRow]:
        return self.list(run_id=run_id)

    # ------------------------------------------------------------------ #
    # C17 `EvidenceStore` 契约（架构 §3.2）：put(evidence) / resolve(ids)
    # ------------------------------------------------------------------ #
    def put(self, evidence: Any) -> EvidenceRow:
        """落一条证据（按 `evidence.id` **幂等 upsert**，重复 put 不产生新行）。

        入参是**任意带同名字段的对象**（`app.domain.Evidence` 领域模型即可），
        因此主循环产出的内存证据与 M5 的渲染 / 导出可共用同一落库口径。
        """
        evidence_id = str(getattr(evidence, "id", "") or "")
        existing = self.get(evidence_id) if evidence_id else None
        if existing is not None:
            return existing
        return self.add(
            id=evidence_id or new_id(),
            run_id=str(getattr(evidence, "run_id", "") or ""),
            task_id=str(getattr(evidence, "task_id", "") or ""),
            tool_call_id=getattr(evidence, "tool_call_id", None),
            source=str(getattr(evidence, "source", "") or ""),
            interface=str(getattr(evidence, "interface", "") or ""),
            as_of=getattr(evidence, "as_of", None),
            fetched_at=getattr(evidence, "fetched_at", None) or now_ms(),
            staleness_ms=getattr(evidence, "staleness_ms", None),
            unit=getattr(evidence, "unit", None),
            caliber=getattr(evidence, "caliber", None),
            value_json=getattr(evidence, "value_json", None),
            raw_ref=str(getattr(evidence, "raw_ref", "") or ""),
            confidence=getattr(evidence, "confidence", None),
            degrade_reason=getattr(evidence, "degrade_reason", None),
            created_at=getattr(evidence, "created_at", None) or now_ms(),
        )

    def resolve(self, ids: Iterable[str]) -> list[EvidenceRow]:
        """按 `id` 批量解析证据（**保持入参顺序**，解析不到的 id 直接跳过）。

        M5 渲染 / 导出取证据的唯一路径：压缩后的上下文只留 `evidence_id`，
        必须回到本表解析（HANDOFF §18.5），不得依赖被折叠的 Step payload。
        """
        ordered = [str(i) for i in ids]
        if not ordered:
            return []
        stmt = select(EvidenceRow).where(EvidenceRow.id.in_(ordered))
        rows = {row.id: row for row in self.session.scalars(stmt).all()}
        return [rows[i] for i in ordered if i in rows]

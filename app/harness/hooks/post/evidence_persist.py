"""PostHook ①：证据落库（架构 §3.1 / C17 的落点）。

把主循环累积的 `Evidence`（内存对象）写入 `evidences` 表。

**幂等**：以 `Evidence.id` 为键记录 `ctx.persisted_evidence_ids`，重复执行只补写未落库的。
Pipeline 会在**每个 Task 结束后**单独跑一次本 Hook（让证据尽早落库，供 M3 的幂等恢复复用），
并在 `finally` 中随整条 PostHook 链再跑一次（此时为 no-op）。
"""

from __future__ import annotations

from ..base import Hook, HookKind, HookResult, PipelineContext
from ....store.repo import EvidenceRepository

__all__ = ["EvidencePersistHook"]


class EvidencePersistHook(Hook):
    """证据落库 + 质量元数据（C3 强制字段由 AgentLoop 构造时已写全）。"""

    name = "evidence_persist"
    kind = HookKind.POST
    priority = 10

    def run(self, ctx: PipelineContext) -> HookResult:
        if ctx.session is None:
            return HookResult.skip("无 session，证据仅保留在内存")
        repo = EvidenceRepository(ctx.session)
        written = 0
        for evidence in ctx.evidences:
            if evidence.id in ctx.persisted_evidence_ids:
                continue
            # C17 契约：`put(evidence)` 按 `evidence.id` 幂等 upsert，并以证据自身
            # 的 id / created_at 落库——保证「一条证据一个 id」可被 citations 反查。
            repo.put(evidence)
            ctx.persisted_evidence_ids.add(evidence.id)
            written += 1
        return HookResult.skip(f"本次落库证据 {written} 条，累计 {len(ctx.persisted_evidence_ids)} 条")

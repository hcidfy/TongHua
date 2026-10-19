"""PreHook ①：上下文注入（**文件归属 M4，M1 只保留注册位**）。

架构 §3.1 / 03_BUILD_PLAN §3 M1 明确：`hooks/pre/context_inject.py` 的**文件实现归属 M4**
（`harness/context.py` 的 `ContextManager.build(run)` + 会话记忆 / 长期记忆注入），
M1 只保证它在 PreHook 链里有**注册位且不阻断管线**。

M4 实现（本文件，`run()` 在同一文件内补全，**Pipeline 与链顺序都不需要改动**）：
1. **记忆召回**（架构 §8）——`MemoryStore.recall(query, scope)`：`SESSION` 层按本 Thread 召回、
   `LONG` 层跨 Thread 召回（用户偏好 / 历史主题），结果去重后写 `ctx.extra["memories"]`；
2. **上下文组装**——`ContextManager.build(ctx)` 把「系统提示 + 计划 + 历史 Step + 证据引用 +
   工具 schema + 记忆召回」组装为 `Messages` 并写入 `ctx.extra`（供 M5 UI 与 Trace 复用）。

召回打分（关键词命中 + 时间衰减）在 `harness/memory.py`，本 Hook 只负责**注入时机**：
架构 §8「召回时机：`ContextInjectHook` 注入会话记忆」。
"""

from __future__ import annotations

from typing import Any

from ..base import Hook, HookKind, HookResult, PipelineContext

__all__ = ["ContextInjectHook"]


class ContextInjectHook(Hook):
    """上下文注入 Hook（M4 实现体：会话 / 长期记忆召回 + 上下文组装）。"""

    name = "context_inject"
    kind = HookKind.PRE
    priority = 10

    def run(self, ctx: PipelineContext) -> HookResult:
        ctx.extra["context_injected"] = True

        memories = self._recall(ctx)
        ctx.extra["memories"] = memories

        # 惰性导入：避免 `app.harness` 包导入期（__init__ → pipeline → hooks）的环。
        from ...context import ContextManager

        manager = ContextManager(
            ctx.session,
            settings=ctx.settings,
            model_router=ctx.model_router,
            tracer=ctx.tracer,
        )
        messages = manager.build(ctx)
        ctx.extra["context_injected_messages"] = messages

        if memories:
            return HookResult.skip(f"已注入 {len(memories)} 条记忆召回")
        return HookResult.passed()

    # ------------------------------------------------------------------ #
    # 记忆召回（SESSION 按本 Thread + LONG 跨 Thread）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _recall(ctx: PipelineContext) -> list[Any]:
        from ....domain import MemoryScope
        from ...memory import MemoryStore

        store = MemoryStore(ctx.session, settings=ctx.settings)
        query = ctx.goal or ""
        found: list[Any] = []
        seen: set[str] = set()
        for scope, thread_id in (
            (MemoryScope.SESSION, ctx.thread_id),
            (MemoryScope.LONG, None),
        ):
            for memory in store.recall(query, scope, thread_id=thread_id):
                if memory.id in seen:
                    continue
                seen.add(memory.id)
                found.append(memory)
        return found

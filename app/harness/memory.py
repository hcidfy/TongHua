"""Harness 内核：分层记忆读写 + 召回打分（架构 §8；03_BUILD_PLAN §3 M4）。

契约（03_BUILD_PLAN §3 M4 接口契约）::

    MemoryStore.write(scope, item) -> Memory
    MemoryStore.recall(query, scope) -> Memory[]

职责：
1. **三层记忆读写**（架构 §8）——工作（WORKING，单次 Run，仅内存）/ 会话（SESSION，Thread 内，
   DB）/ 长期（LONG，跨 Thread，DB）；
2. **召回打分**——关键词命中 + 时间衰减（ADR-6：**不引入向量库**，接口 `recall()` 保持稳定，
   后续可无缝替换为 pgvector 语义检索）。

打分口径（确定性，便于离线单测）::

    keyword_hits = 命中的关键词种数（0 → 直接淘汰）
    recency      = 0.5 ** (age_hours / MEMORY_DECAY_HALFLIFE_H)     # (0, 1]
    score        = keyword_hits + RECENCY_WEIGHT * recency

命中数优先（相关性），同命中数时新者优先（时间衰减）；`recency` 让「同样命中但更新」的记忆胜出。

本模块**只做读端打分**：写端 CRUD 归 `app/store/repo/memories.py`（M1 的 `MemoryWriteHook` 已落）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain import Memory, MemoryScope, now_ms
from app.store.repo import MemoryRepository

__all__ = [
    "RECENCY_WEIGHT",
    "MS_PER_HOUR",
    "MemoryHit",
    "MemoryStore",
    "tokenize",
]

#: 时间衰减在总分中的权重（与「命中数」同量纲，1.0 = 半衰期内的新记忆约等于多命中 1 个关键词）。
RECENCY_WEIGHT = 1.0

#: 1 小时的毫秒数（D2：时间统一毫秒时间戳）。
MS_PER_HOUR = 3_600_000

#: ASCII 词（数字 / 字母，含代码如 600519.SH 会被拆成 600519 / SH）。
_ASCII_RE = re.compile(r"[0-9A-Za-z]+")
#: CJK 连续片段（再切成单字 + 双字，做确定性「关键词」抽取，不依赖分词库）。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(query: str) -> list[str]:
    """把查询串切成关键词集合（去重保序，全部小写）。

    - ASCII：整词（`600519` / `sh`）；
    - CJK：单字 + 相邻双字（`半导体` → `半`,`导`,`体`,`半导`,`导体`）——单字保证召回率、
      双字提升相关性排序的区分度。
    """
    text = str(query or "")
    tokens: list[str] = [t.lower() for t in _ASCII_RE.findall(text)]
    for run in _CJK_RE.findall(text):
        for index, char in enumerate(run):
            tokens.append(char)
            if index + 1 < len(run):
                tokens.append(run[index : index + 2])
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


@dataclass(slots=True)
class MemoryHit:
    """一条召回结果及其打分细节（可观测 / 单测断言用）。"""

    memory: Memory
    score: float
    keyword_hits: int
    recency: float


def _scope_value(scope: Any) -> str:
    return str(scope)


class MemoryStore:
    """三层记忆的读写入口（WORKING 内存 + SESSION/LONG 落 DB）。

    - 无 `session`（或未注入 `repository`）时，SESSION / LONG 的读写退化为**无 DB 模式**：
      写入进内存、读取从内存——保证纯内存场景（单测 / 无库）也能跑通，不抛异常。
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        settings: Settings | None = None,
        repository: MemoryRepository | None = None,
        working: Iterable[Memory] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._repo = repository or (MemoryRepository(session) if session is not None else None)
        self.top_k = int(getattr(self.settings, "memory_recall_top_k", 5) or 5)
        self.halflife_h = float(getattr(self.settings, "memory_decay_halflife_h", 72.0) or 72.0)
        #: 工作记忆与「无 DB 模式」的 SESSION/LONG 记忆都落在这里（仅进程内）。
        self._local: list[Memory] = list(working or [])

    # ------------------------------------------------------------------ #
    # 契约入口：写
    # ------------------------------------------------------------------ #
    def write(
        self,
        scope: Any,
        item: Memory | dict[str, Any] | None = None,
        *,
        kind: str | None = None,
        content: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        embedding_ref: str | None = None,
        created_at: int | None = None,
    ) -> Memory:
        """写入一条记忆。

        `item` 可以是 `Memory` 领域对象或 dict；也可只用关键字给出 `kind` / `content`。
        WORKING（及无 DB 模式）只落内存；SESSION / LONG 落 `memories` 表。
        """
        if isinstance(item, Memory):
            fields = {
                "scope": _scope_value(item.scope),
                "kind": item.kind,
                "content": item.content,
                "thread_id": item.thread_id,
                "run_id": item.run_id,
                "embedding_ref": item.embedding_ref,
                "created_at": item.created_at,
            }
        elif isinstance(item, dict):
            fields = {
                "scope": _scope_value(scope),
                "kind": str(item.get("kind") or kind or "note"),
                "content": str(item.get("content") or content or ""),
                "thread_id": item.get("thread_id", thread_id),
                "run_id": item.get("run_id", run_id),
                "embedding_ref": item.get("embedding_ref", embedding_ref),
                "created_at": int(item.get("created_at") or created_at or now_ms()),
            }
        else:
            fields = {
                "scope": _scope_value(scope),
                "kind": str(kind or "note"),
                "content": str(content or ""),
                "thread_id": thread_id,
                "run_id": run_id,
                "embedding_ref": embedding_ref,
                "created_at": int(created_at or now_ms()),
            }

        scope_value = fields["scope"]
        persisted = scope_value != str(MemoryScope.WORKING) and self._repo is not None
        if persisted:
            row = self._repo.create(
                scope=scope_value,
                kind=fields["kind"],
                content=fields["content"],
                thread_id=fields["thread_id"],
                run_id=fields["run_id"],
                embedding_ref=fields["embedding_ref"],
            )
            memory = Memory(
                id=row.id,
                scope=row.scope,
                thread_id=row.thread_id,
                run_id=row.run_id,
                kind=row.kind,
                content=row.content,
                embedding_ref=row.embedding_ref,
                created_at=row.created_at,
            )
        else:
            memory = Memory(**fields)
        self._local.append(memory)
        return memory

    # ------------------------------------------------------------------ #
    # 契约入口：召回
    # ------------------------------------------------------------------ #
    def recall(
        self,
        query: str,
        scope: Any = None,
        *,
        top_k: int | None = None,
        thread_id: str | None = None,
        now: int | None = None,
    ) -> list[Memory]:
        """按「关键词命中 + 时间衰减」召回 Top-K 记忆。

        `scope=None` 表示跨全部三层；`thread_id` 给定时只召回该 Thread 的记忆
        （长期记忆通常不带 thread_id，故传 `None` 即跨 Thread 召回）。
        """
        return [hit.memory for hit in self.recall_scored(
            query, scope, top_k=top_k, thread_id=thread_id, now=now
        )]

    def recall_scored(
        self,
        query: str,
        scope: Any = None,
        *,
        top_k: int | None = None,
        thread_id: str | None = None,
        now: int | None = None,
    ) -> list[MemoryHit]:
        """同 `recall()`，但返回带打分的 `MemoryHit`（供可观测与单测断言）。"""
        scopes = self._normalize_scopes(scope)
        keywords = tokenize(query)
        reference = int(now) if now is not None else now_ms()
        hits: list[MemoryHit] = []
        for memory in self._gather(scopes, thread_id=thread_id):
            keyword_hits = self._keyword_hits(memory.content, keywords)
            if keyword_hits <= 0:
                continue
            recency = self._recency(memory.created_at, reference)
            hits.append(
                MemoryHit(
                    memory=memory,
                    score=float(keyword_hits) + RECENCY_WEIGHT * recency,
                    keyword_hits=keyword_hits,
                    recency=recency,
                )
            )
        hits.sort(key=lambda h: (-h.score, -int(h.memory.created_at), h.memory.id))
        limit = int(top_k) if top_k is not None else self.top_k
        return hits[: max(0, limit)]

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_scopes(scope: Any) -> tuple[str, ...]:
        if scope is None:
            return tuple(str(s) for s in MemoryScope)
        if isinstance(scope, (list, tuple, set, frozenset)):
            return tuple(_scope_value(s) for s in scope)
        return (_scope_value(scope),)

    def _gather(self, scopes: Sequence[str], *, thread_id: str | None) -> list[Memory]:
        wanted = set(scopes)
        found: list[Memory] = []
        # 已落库的记忆同时存在于 `_local` 与 DB：按 `id` 去重，避免同一实例被写入后
        # 立即召回时出现重复命中（幂等：内存副本先入，DB 行只补未见过的新 id）。
        seen: set[str] = set()

        def accept(memory: Memory) -> None:
            if memory.id in seen:
                return
            seen.add(memory.id)
            found.append(memory)

        # 内存副本：WORKING 永远在内存；无 DB 模式时 SESSION/LONG 也在内存。
        for memory in self._local:
            if str(memory.scope) not in wanted:
                continue
            if thread_id is not None and memory.thread_id not in (None, thread_id):
                continue
            accept(memory)
        if self._repo is None:
            return found
        for scope_value in scopes:
            if scope_value == str(MemoryScope.WORKING):
                continue  # 工作记忆只存内存
            for row in self._repo.list_by_thread(thread_id, scope=scope_value) if thread_id else \
                    self._repo.list(scope=scope_value):
                accept(
                    Memory(
                        id=row.id,
                        scope=row.scope,
                        thread_id=row.thread_id,
                        run_id=row.run_id,
                        kind=row.kind,
                        content=row.content,
                        embedding_ref=row.embedding_ref,
                        created_at=row.created_at,
                    )
                )
        return found

    @staticmethod
    def _keyword_hits(content: str, keywords: Sequence[str]) -> int:
        text = str(content or "").lower()
        return sum(1 for keyword in keywords if keyword in text)

    def _recency(self, created_at: int, reference: int) -> float:
        halflife = self.halflife_h if self.halflife_h > 0 else 72.0
        age_hours = max(0.0, (reference - int(created_at)) / MS_PER_HOUR)
        return float(0.5 ** (age_hours / halflife))

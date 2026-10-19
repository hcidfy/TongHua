"""Harness 内核：上下文组装与压缩触发（架构 §7；03_BUILD_PLAN §3 M4）。

契约（03_BUILD_PLAN §3 M4 接口契约）::

    ContextManager.build(run) -> Messages

职责（架构 §7.2「分层压缩表」）：
1. **上下文组装**——把「系统提示 + 计划 + 历史 Step + 证据引用 + 工具 schema + 记忆召回」
   组装成消息列表（`Messages`）；
2. **token 估算**——确定性口径（ASCII 4 字符 ≈ 1 token、其余 1 字符 ≈ 1 token），
   不引外部分词器，离线可测；
3. **阈值触发**——估算 token 超过「`BUDGET_MAX_TOKENS` × `COMPACT_THRESHOLD_RATIO`」（默认 60%）
   时调用 `Compactor.compact()` 做分层压缩，并把压缩事实上报（Trace + `ContextCompacted` 事件）。

分层（架构 §7.2）：**系统提示不压缩**；计划只折叠已完成标题；历史 Step 摘要化；
**证据下沉只留 `evidence_id` + 一行摘要**（FR-04 可回溯）；记忆召回按相关性 Top-K 截断。

> 关键正确性口径（架构 §7.3 / FR-04）：压缩**只丢弃上下文的渲染体积**，
> 不丢弃事实本身——所有 `evidence_id` 原样保留，值仍留在 `evidences` 表可解析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.store.repo import (
    CheckpointEventRepository,
    StepRepository,
    TaskRepository,
)

__all__ = [
    "Messages",
    "CONTEXT_LAYER_COUNT",
    "SYSTEM_PROMPT",
    "estimate_tokens",
    "EvidenceRef",
    "StepDigest",
    "ContextBundle",
    "ContextManager",
    "evidence_ref",
]

#: 消息列表（`ContextManager.build()` 的返回类型，与 `ModelRouter.complete(messages=...)` 对接）。
Messages = list[dict[str, Any]]

#: 上下文分层数（架构 §7.2 的表：系统提示 / 计划 / 历史 Step / 证据 / 记忆 + 工具 schema）。
CONTEXT_LAYER_COUNT = 5

#: 上下文里的 harness 级系统提示；**压缩时永不丢弃**（角色提示由 `ModelRouter` 另注入，不冲突）。
SYSTEM_PROMPT = (
    "你是 InvestmentXBuddy 的研究执行器：只依据已取证的事实作答，"
    "不得给出买卖建议或涨跌预测；引用事实必须带 evidence_id，缺失即标注不确定性。"
)

#: ASCII 字符数 / token 的经验比值（确定性估算用；不引 tiktoken 等外部依赖）。
_ASCII_PER_TOKEN = 4


def estimate_tokens(text: Any) -> int:
    """确定性 token 估算：ASCII 每 4 字符计 1 token，非 ASCII（CJK 等）每字符计 1 token。"""
    ascii_count = 0
    wide_count = 0
    for char in str(text or ""):
        if ord(char) < 128:
            ascii_count += 1
        else:
            wide_count += 1
    return (ascii_count + _ASCII_PER_TOKEN - 1) // _ASCII_PER_TOKEN + wide_count


# --------------------------------------------------------------------------- #
# 可压缩的上下文实体
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class EvidenceRef:
    """证据的**下沉引用**：只保留 `evidence_id` + 一行摘要，值留在 `evidences` 表。

    `value_preview` 仅用于「压缩前」的上下文渲染（内联原始值）；压缩后只渲染 `digest`，
    但 `evidence_id` **任何情况下都不改写**（提示词 `compactor.md` 硬约束 ①）。
    """

    evidence_id: str
    source: str = ""
    interface: str = ""
    as_of: int | None = None
    unit: str | None = None
    caliber: str | None = None
    digest: str = ""
    value_preview: str = ""


@dataclass(slots=True)
class StepDigest:
    """一条历史 Step（压缩前携带完整 `payload`；压缩后只留 `summary`）。"""

    task_id: str = ""
    seq: int = 0
    type: str = ""
    tool: str | None = None
    evidence_id: str | None = None
    #: 压缩产出的一行摘要（保留 seq / type / 工具名 / 关键数值）。
    summary: str = ""
    #: 压缩前的原始 payload（值本身已落库，这里仅用于「压缩前」的上下文渲染）。
    payload: dict[str, Any] = field(default_factory=dict)


def evidence_ref(evidence: Any) -> EvidenceRef:
    """把 `Evidence`（领域对象 / DB 行）转成下沉引用（不改写 id、只做一行摘要）。"""
    value = getattr(evidence, "value_json", None)
    return EvidenceRef(
        evidence_id=str(getattr(evidence, "id", "")),
        source=str(getattr(evidence, "source", "") or ""),
        interface=str(getattr(evidence, "interface", "") or ""),
        as_of=getattr(evidence, "as_of", None),
        unit=getattr(evidence, "unit", None),
        caliber=getattr(evidence, "caliber", None),
        digest=_value_digest(value),
        value_preview=_value_preview(value),
    )


def _value_digest(value: Any) -> str:
    """一行摘要（只描述**结构**，不引入新数字/事实）。"""
    if value is None:
        return "值缺失"
    if isinstance(value, dict):
        keys = [str(k) for k in list(value.keys())[:4]]
        return "字段: " + ", ".join(keys) if keys else "空对象"
    if isinstance(value, (list, tuple)):
        return f"列表[{len(value)}]"
    return f"{type(value).__name__}"


def _value_preview(value: Any) -> str:
    """压缩前的内联值预览（截断，避免单条证据撑爆上下文）。"""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - 兜底
        text = str(value)
    return text if len(text) <= 400 else text[:400] + "…"


@dataclass(slots=True)
class ContextBundle:
    """可压缩的上下文载体：`ContextManager.gather()` 产出 → `Compactor` 消费 → `render()`。

    `compacted=False` 时渲染完整内容（Step payload / 证据值内联）；`compacted=True` 时
    只渲染摘要与引用——两者共用同一份 `evidence_id`，因此 FR-04 始终成立。
    """

    run_id: str
    goal: str = ""
    system: str = SYSTEM_PROMPT
    plan_lines: list[str] = field(default_factory=list)
    steps: list[StepDigest] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    #: 工具名 → 参数 JSON schema（架构 §7.2 的「工具 schema」层，来源 `ToolSpec.schema_json`）。
    tool_schemas: dict[str, Any] = field(default_factory=dict)
    memory_lines: list[str] = field(default_factory=list)
    # --- 压缩后回填 ---
    compacted: bool = False
    summary: str = ""
    dropped_steps: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # --- 渲染 ---
    def body(self) -> str:
        """章节正文（不含系统提示）。"""
        parts: list[str] = []
        if self.plan_lines:
            parts.append("【计划】\n" + "\n".join(self.plan_lines))
        if self.memory_lines:
            parts.append("【记忆召回】\n" + "\n".join(self.memory_lines))
        if self.tool_names:
            parts.append(
                "【可用工具】\n" + "\n".join(self._render_tool(name) for name in self.tool_names)
            )
        if self.steps:
            parts.append("【历史 Step】\n" + "\n".join(self._render_step(s) for s in self.steps))
        if self.evidence_refs:
            parts.append("【证据】\n" + "\n".join(self._render_evidence(r) for r in self.evidence_refs))
        if self.compacted and self.summary:
            parts.append("【压缩摘要】" + self.summary)
        return "\n".join(parts)

    def text(self) -> str:
        """系统提示 + 正文（token 估算的输入）。"""
        return f"{self.system}\n{self.body()}"

    def token_estimate(self) -> int:
        return estimate_tokens(self.text())

    def evidence_ids(self) -> list[str]:
        """上下文里引用的全部 `evidence_id`（压缩前后一致，FR-04）。"""
        return [ref.evidence_id for ref in self.evidence_refs]

    def render(self) -> Messages:
        """组装成消息列表（契约 `ContextManager.build(run) -> Messages`）。"""
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.body()},
        ]

    # --- 内部渲染 ---
    def _render_tool(self, name: str) -> str:
        """单个工具的**参数 schema** 一行渲染：`名(参数, 必填参数*)`。

        schema 来源 `ToolSpec.schema_json`（架构 §7.2「工具 schema」层）；无 schema 时退化为
        `名()`——不凭空编造参数。
        """
        schema = self.tool_schemas.get(name) or {}
        properties = [str(key) for key in (schema.get("properties") or {})]
        required = set(schema.get("required") or [])
        if not properties:
            return f"- {name}()"
        args = ", ".join(f"{prop}*" if prop in required else prop for prop in properties)
        return f"- {name}({args})"

    def _render_step(self, step: StepDigest) -> str:
        head = f"#{step.seq} {step.type}"
        if step.tool:
            head += f" {step.tool}"
        if step.evidence_id:
            head += f" →{step.evidence_id}"
        if self.compacted:
            return f"{head} {step.summary}".rstrip()
        payload = json.dumps(step.payload, ensure_ascii=False, sort_keys=True, default=str)
        return f"{head} {payload}".rstrip()

    def _render_evidence(self, ref: EvidenceRef) -> str:
        head = (
            f"evidence_id={ref.evidence_id} source={ref.source} "
            f"as_of={ref.as_of} unit={ref.unit} caliber={ref.caliber}"
        )
        if self.compacted:
            return f"{head} [{ref.digest}]"
        return f"{head} value={ref.value_preview}"


# --------------------------------------------------------------------------- #
# 组装 + 阈值触发
# --------------------------------------------------------------------------- #
class ContextManager:
    """上下文组装器（契约 `ContextManager.build(run) -> Messages`）。

    `run` 是 `PipelineContext`：`build()` 从中读取计划 / 历史 Step / 证据 / 记忆召回，
    组装为 `Messages`；估算 token 超过阈值时触发 `Compactor` 分层压缩并上报压缩事实。
    `Compactor` 可注入（默认在首次需要时按配置惰性构造），避免模块级循环依赖。
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        settings: Settings | None = None,
        compactor: Any = None,
        model_router: Any = None,
        tracer: Any = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.compactor = compactor
        self.model_router = model_router
        self.tracer = tracer
        self.max_tokens = int(getattr(self.settings, "budget_max_tokens", 100000) or 100000)
        self.threshold_ratio = float(getattr(self.settings, "compact_threshold_ratio", 0.6) or 0.6)

    # --- 契约入口 ---
    def build(self, run: Any) -> Messages:
        """组装一次上下文；超过阈值则先压缩（并上报 `ContextCompacted`）。"""
        bundle = self.gather(run)
        before = bundle.token_estimate()
        result = None
        if before > self.threshold and (bundle.steps or bundle.evidence_refs or bundle.plan_lines):
            result = self.compact(bundle)
            self._report(run, result)
        self._stash(run, bundle)
        return bundle.render()

    def gather(self, run: Any) -> ContextBundle:
        """从 Run 上下文收集各层内容（不压缩、不落库）。"""
        bundle = ContextBundle(
            run_id=str(getattr(run, "run_id", "") or ""),
            goal=str(getattr(run, "goal", "") or ""),
        )
        bundle.plan_lines = self._plan_lines(run)
        bundle.tool_names = self._tool_names(run)
        bundle.tool_schemas = self._tool_schemas(run)
        bundle.steps = self._steps(run)
        bundle.evidence_refs = self._evidence_refs(run)
        bundle.memory_lines = self._memory_lines(run)
        return bundle

    def compact(self, bundle: ContextBundle) -> Any:
        """触发分层压缩（Compactor 缺省时按配置惰性构造）。"""
        if self.compactor is None:
            from .compactor import Compactor

            self.compactor = Compactor(
                settings=self.settings, model_router=self.model_router, tracer=self.tracer
            )
        return self.compactor.compact(bundle)

    # --- 阈值 ---
    @property
    def threshold(self) -> int:
        """压缩触发阈值 = `BUDGET_MAX_TOKENS × COMPACT_THRESHOLD_RATIO`（架构 §7.1）。"""
        return int(self.max_tokens * self.threshold_ratio)

    # --- 上报（Trace + 事件）---
    def _report(self, run: Any, result: Any) -> None:
        tracer = self.tracer or getattr(run, "tracer", None)
        if tracer is not None:
            with tracer.span(
                "compact",
                before_tokens=result.before_tokens,
                after_tokens=result.after_tokens,
                dropped_steps=len(result.dropped_steps),
            ) as span:
                span.set(degraded=bool(result.degraded))
        self._append_compacted_event(run, result)

    def _append_compacted_event(self, run: Any, result: Any) -> None:
        """落 `ContextCompacted` 事件（26 类契约事件之一，不新增事件类型）。"""
        session = self.session or getattr(run, "session", None)
        run_id = str(getattr(run, "run_id", "") or "")
        if session is None or not run_id:
            return
        CheckpointEventRepository(session).append(
            run_id,
            "ContextCompacted",
            {
                "before_tokens": int(result.before_tokens),
                "after_tokens": int(result.after_tokens),
                "dropped_steps": list(result.dropped_steps),
                "kept_evidence_ids": list(result.kept_evidence_ids),
                "degraded": bool(result.degraded),
                "summary": result.summary,
            },
        )

    def _stash(self, run: Any, bundle: ContextBundle) -> None:
        """把组装的上下文与压缩状态写进 `ctx.extra`（可观测 / 测试断言 / M5 UI 复用）。"""
        extra = getattr(run, "extra", None)
        if not isinstance(extra, dict):
            return
        extra["context_bundle"] = bundle
        extra["context_messages"] = bundle.render()
        extra["context_tokens"] = bundle.token_estimate()
        extra["context_compacted"] = bundle.compacted
        extra["context_evidence_ids"] = bundle.evidence_ids()

    # --- 各层收集 ---
    @staticmethod
    def _plan_lines(run: Any) -> list[str]:
        plan = getattr(run, "plan", None)
        tasks = list(getattr(plan, "tasks", None) or [])
        lines: list[str] = []
        for task in tasks:
            tools = list(getattr(task, "tools", None) or [])
            tool_text = f"（工具: {', '.join(tools)}）" if tools else "（无工具）"
            lines.append(f"#{getattr(task, 'seq', '?')} {getattr(task, 'title', '')}{tool_text}")
        return lines

    @staticmethod
    def _tool_names(run: Any) -> list[str]:
        registry = getattr(run, "registry", None)
        names = getattr(registry, "names", None)
        if callable(names):
            try:
                return list(names())
            except Exception:  # pragma: no cover - 注册表异常不应阻断上下文组装
                return []
        return []

    @staticmethod
    def _tool_schemas(run: Any) -> dict[str, Any]:
        """工具名 → 参数 schema（`ToolSpec.schema_json`）；注册表异常不阻断上下文组装。"""
        registry = getattr(run, "registry", None)
        lister = getattr(registry, "list", None)
        if not callable(lister):
            return {}
        try:
            specs = list(lister())
        except Exception:  # pragma: no cover - 注册表异常不应阻断上下文组装
            return {}
        return {
            str(getattr(spec, "name", "")): dict(getattr(spec, "schema_json", None) or {})
            for spec in specs
        }

    def _steps(self, run: Any) -> list[StepDigest]:
        session = self.session or getattr(run, "session", None)
        run_id = str(getattr(run, "run_id", "") or "")
        if session is None or not run_id:
            return []
        digests: list[StepDigest] = []
        step_repo = StepRepository(session)
        for task in TaskRepository(session).list_by_run(run_id):
            for row in step_repo.list_by_task(task.id):
                payload = dict(getattr(row, "payload_json", None) or {})
                digests.append(
                    StepDigest(
                        task_id=str(task.id),
                        seq=int(row.seq),
                        type=str(row.type),
                        tool=payload.get("tool"),
                        evidence_id=payload.get("evidence_id"),
                        payload=payload,
                    )
                )
        return digests

    @staticmethod
    def _evidence_refs(run: Any) -> list[EvidenceRef]:
        return [evidence_ref(evidence) for evidence in (getattr(run, "evidences", None) or [])]

    @staticmethod
    def _memory_lines(run: Any) -> list[str]:
        extra = getattr(run, "extra", None)
        memories = extra.get("memories") if isinstance(extra, dict) else None
        lines: list[str] = []
        for memory in memories or []:
            kind = getattr(memory, "kind", "note")
            content = getattr(memory, "content", "")
            lines.append(f"[{kind}] {content}")
        return lines

"""Harness 内核：动态 UI 渲染器（架构 C19；03_BUILD_PLAN §3 M5）。

契约（架构 §3.2 / 03_BUILD_PLAN §3 M5）::

    UIRenderer.render(source) -> UIBlock[]

职责：把「模型 / 内核产出的块描述」校验成**可渲染的 `UIBlock`**，并执行冻结的渲染规则：

```text
可渲染条件 = 组件名 ∈ 白名单(12)  ∩  前端已实现(7)
否则 → FallbackJsonCard + Trace 记录 render_status=failed
```

白名单（架构 §11.2，**契约全集 12 个，不可增删**）：`plan_card` / `clarify_question` /
`evidence_card` / `evidence_table` / `metric_block` / `series_chart` / `distribution_chart` /
`conclusion_card` / `citation_list` / `approval_card` / `degradation_notice` / `trace_ref`。

**24h 只收窄「已实现集」为 7 个**（03_BUILD_PLAN §3 M5）：后置 5 个
（`metric_block` / `series_chart` / `distribution_chart` / `citation_list` / `trace_ref`）
命中即降级为 `FallbackJsonCard`（架构 §11.2 **不改**）。

架构 §11.3 四条关键约束：

1. **模型不能凭空造组件** → 白名单外的组件整块降级；
2. **`conclusion_card` 强制分层**：`layer=FACT` 时 `refs` 不能为空，否则**在渲染层就被拦下**；
3. 流式增量按 `seq` 推送（`seq` 由本渲染器或落库端顺序分配）；
4. 任何 `refs` 都渲染为可点击链接（前端负责，本层只保证 `refs_json` 结构）。

配置项（架构 §14 / 03_BUILD_PLAN §3 M5）：`RENDER_STRICT=true`（严格：任何校验失败即降级）、
`UI_BLOCK_MAX=200`（单 Run 单次渲染的块数上限，超出截断）。
"""

from __future__ import annotations

from typing import Any, Iterable

from app.domain import UIBlock, UIBlockSourceType, new_id, now_ms

__all__ = [
    "UI_COMPONENT_WHITELIST",
    "UI_COMPONENT_WHITELIST_COUNT",
    "IMPLEMENTED_COMPONENTS",
    "IMPLEMENTED_COMPONENT_COUNT",
    "REQUIRED_PROPS",
    "FALLBACK_COMPONENT",
    "DEFAULT_UI_BLOCK_MAX",
    "UIRenderer",
]

#: 组件白名单（架构 §11.2，契约全集 **12**）。
UI_COMPONENT_WHITELIST: tuple[str, ...] = (
    "plan_card",
    "clarify_question",
    "evidence_card",
    "evidence_table",
    "metric_block",
    "series_chart",
    "distribution_chart",
    "conclusion_card",
    "citation_list",
    "approval_card",
    "degradation_notice",
    "trace_ref",
)

#: 契约数量：白名单 12 个（改动即破坏契约）。
UI_COMPONENT_WHITELIST_COUNT = 12

#: 24h **已实现集**（前端实现 7 个；白名单仍是 12 个，只收窄实现集）。
IMPLEMENTED_COMPONENTS: tuple[str, ...] = (
    "plan_card",
    "clarify_question",
    "evidence_card",
    "evidence_table",
    "conclusion_card",
    "approval_card",
    "degradation_notice",
)

#: 已实现数量：7 个。
IMPLEMENTED_COMPONENT_COUNT = 7

#: 各组件必填 props（架构 §11.2 原样；`as_of/unit/caliber` 允许 `None`，但**键必须存在**，D4）。
REQUIRED_PROPS: dict[str, tuple[str, ...]] = {
    "plan_card": ("tasks",),
    "clarify_question": ("questions",),
    "evidence_card": ("evidence_id", "source", "as_of", "unit", "caliber", "value"),
    "evidence_table": ("evidence_ids",),
    "metric_block": ("label", "value", "unit", "delta", "caliber"),
    "series_chart": ("series", "x_field", "y_fields"),
    "distribution_chart": ("categories", "values"),
    "conclusion_card": ("layer", "text", "confidence", "refs"),
    "citation_list": ("citations",),
    "approval_card": ("approval_id", "type", "summary"),
    "degradation_notice": ("scope", "reason", "impact"),
    "trace_ref": ("span_id",),
}

#: 降级组件名（**不在 12 白名单内**：它是渲染层的兜底组件，架构 §11.1 的 `FallbackJsonCard`）。
FALLBACK_COMPONENT = "FallbackJsonCard"

#: 单 Run 单次渲染的块数上限（配置项 `UI_BLOCK_MAX` 的默认值）。
DEFAULT_UI_BLOCK_MAX = 200

#: `conclusion_card.layer` 合法取值（架构 §11.2：FACT | INFERENCE | UNCERTAIN）。
_CONCLUSION_LAYERS = ("FACT", "INFERENCE", "UNCERTAIN")

#: `degradation_notice.impact` 合法取值（架构 §11.2 / `data/quality.py`）。
_IMPACT_LEVELS = ("low", "medium", "high")


class UIRenderer:
    """块描述 → `UIBlock` 的校验 / 降级渲染器（C19）。"""

    def __init__(
        self,
        *,
        settings: Any = None,
        tracer: Any = None,
        max_blocks: int | None = None,
        strict: bool | None = None,
    ) -> None:
        self.settings = settings
        self.tracer = tracer
        self.max_blocks = int(
            max_blocks
            if max_blocks is not None
            else getattr(settings, "ui_block_max", DEFAULT_UI_BLOCK_MAX) or DEFAULT_UI_BLOCK_MAX
        )
        self.strict = bool(
            strict if strict is not None else getattr(settings, "render_strict", True)
        )

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def render(
        self,
        source: Any,
        *,
        run_id: str = "",
        start_seq: int = 1,
    ) -> list[UIBlock]:
        """把块描述渲染为 `UIBlock[]`（非法块降级，**绝不抛错**）。

        `source` 可以是单个块描述（`dict`）或块描述序列；每个块描述形如::

            {"component": "evidence_card", "props_json": {...}, "refs_json": [...],
             "source_type": "EVIDENCE"}

        `props` / `refs` 的别名亦被接受（兼容 SSE 侧的 `UiBlockPayload`）。
        """
        descriptors = self._normalize(source)
        blocks: list[UIBlock] = []
        seq = int(start_seq)
        for descriptor in descriptors:
            if len(blocks) >= self.max_blocks:
                self._note_max_reached(run_id, len(descriptors))
                break
            blocks.append(self._render_one(descriptor, run_id=run_id, seq=seq))
            seq += 1
        return blocks

    def render_one(self, descriptor: Any, *, run_id: str = "", seq: int = 1) -> UIBlock:
        """渲染单个块描述（便捷入口）。"""
        return self._render_one(self._as_descriptor(descriptor), run_id=run_id, seq=seq)

    # ------------------------------------------------------------------ #
    # 校验 + 降级
    # ------------------------------------------------------------------ #
    def _render_one(self, descriptor: dict[str, Any], *, run_id: str, seq: int) -> UIBlock:
        component = str(descriptor.get("component") or "")
        props = descriptor.get("props_json")
        props = dict(props) if isinstance(props, dict) else {}
        refs = descriptor.get("refs_json")
        refs = list(refs) if isinstance(refs, (list, tuple)) else []
        source_type = self._source_type(descriptor.get("source_type"), component)

        ok, reason, checked_component = self.validate(component, props, refs)
        if ok:
            return self._block(
                run_id=run_id,
                seq=seq,
                source_type=source_type,
                component=checked_component,
                props=props,
                refs=refs,
                render_status="ok",
            )

        # 降级：整块替换为 FallbackJsonCard（把原始块原样塞进 props，前端展示 JSON）
        self._trace_failed(run_id, component, reason)
        return self._block(
            run_id=run_id,
            seq=seq,
            source_type=source_type,
            component=FALLBACK_COMPONENT,
            props={
                "component": component,
                "props": props,
                "refs": refs,
                "reason": reason,
            },
            refs=refs,
            render_status="failed",
        )

    def validate(
        self, component: str, props: dict[str, Any], refs: list[Any]
    ) -> tuple[bool, str, str]:
        """校验一个块；返回 `(ok, reason, component)`。

        校验顺序（前两步与 `strict` 无关，恒定生效）：

        ① 组件名 ∈ 白名单(12)？ → 否：降级（架构 §11.3-1「不能凭空造组件」）
        ② 组件 ∈ 已实现集(7)？ → 否：降级（24h 实现集收窄）
        ③ 必填 props 齐全？（`strict=False` 时仅记录、不降级）
        ④ `conclusion_card` 且 `layer=FACT` 且 `refs` 为空 → 降级（架构 §11.3-2）
        ⑤ 形状 / 取值校验（`strict=False` 时仅记录、不降级）
        """
        if component not in UI_COMPONENT_WHITELIST:
            return False, f"组件不在白名单：{component or '(空)'}", component
        if component not in IMPLEMENTED_COMPONENTS:
            return False, f"组件未实现（后置 5 个之一）：{component}", component

        missing = [key for key in REQUIRED_PROPS.get(component, ()) if key not in props]
        if missing and self.strict:
            return False, f"缺少必填 props：{missing}", component
        if missing:
            self._trace_failed("", component, f"[non-strict] 缺少必填 props：{missing}")

        if component == "conclusion_card":
            layer = str(props.get("layer") or "").upper()
            if layer == "FACT" and not refs:
                # 架构 §11.3-2：无引用的「事实」在渲染层就被拦下（与 Guard 双保险）
                return False, "conclusion_card 分层为 FACT 但 refs 为空（无引用的事实被拦下）", component

        shape_error = self._shape_error(component, props, refs)
        if shape_error and self.strict:
            return False, shape_error, component
        if shape_error:
            self._trace_failed("", component, f"[non-strict] {shape_error}")

        return True, "", component

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _shape_error(component: str, props: dict[str, Any], refs: list[Any]) -> str:
        """形状 / 取值校验（返回错误描述；无错返回空串）。"""
        if component == "plan_card":
            tasks = props.get("tasks")
            if not isinstance(tasks, list) or not tasks:
                return "plan_card.tasks 必须是非空数组"
        elif component == "clarify_question":
            questions = props.get("questions")
            if not isinstance(questions, list) or not questions:
                return "clarify_question.questions 必须是非空数组"
        elif component == "evidence_table":
            ids = props.get("evidence_ids")
            if not isinstance(ids, list) or not ids:
                return "evidence_table.evidence_ids 必须是非空数组"
        elif component == "conclusion_card":
            layer = str(props.get("layer") or "").upper()
            if layer not in _CONCLUSION_LAYERS:
                return f"conclusion_card.layer 必须 ∈ {list(_CONCLUSION_LAYERS)}"
            if not str(props.get("text") or "").strip():
                return "conclusion_card.text 不能为空"
        elif component == "degradation_notice":
            impact = str(props.get("impact") or "")
            if impact not in _IMPACT_LEVELS:
                return f"degradation_notice.impact 必须 ∈ {list(_IMPACT_LEVELS)}"
            if not str(props.get("reason") or "").strip():
                return "degradation_notice.reason 不能为空"
        elif component == "approval_card":
            if not str(props.get("approval_id") or "").strip():
                return "approval_card.approval_id 不能为空"
        return ""

    @staticmethod
    def _source_type(value: Any, component: str) -> str:
        # 白名单外的组件没有缺省来源，落 SYSTEM（渲染器**绝不因块非法而抛错**）
        fallback = _DEFAULT_SOURCE.get(component, str(UIBlockSourceType.SYSTEM))
        if not value:
            return fallback
        try:
            return str(UIBlockSourceType(str(value)))
        except ValueError:
            return fallback

    def _block(
        self,
        *,
        run_id: str,
        seq: int,
        source_type: Any,
        component: str,
        props: dict[str, Any],
        refs: list[Any],
        render_status: str,
    ) -> UIBlock:
        return UIBlock(
            id=new_id(),
            run_id=run_id,
            seq=int(seq),
            source_type=source_type,  # type: ignore[arg-type]
            component=component,
            props_json=props,
            refs_json=refs,
            render_status=render_status,
            created_at=now_ms(),
        )

    def _trace_failed(self, run_id: str, component: str, reason: str) -> None:
        """Trace 记录 `render_status=failed`（03_BUILD_PLAN §3 M5 渲染规则原文）。"""
        if self.tracer is None:
            return
        try:
            with self.tracer.span("render", component=component) as span:
                span.set(render_status="failed", reason=reason, run=run_id or None)
        except Exception:  # pragma: no cover - Trace 失败不得影响渲染
            pass

    def _note_max_reached(self, run_id: str, total: int) -> None:
        """块数达上限（`UI_BLOCK_MAX`）后的截断记录。"""
        if self.tracer is None:
            return
        try:
            with self.tracer.span("render_truncate", run=run_id or None) as span:
                span.set(limit=self.max_blocks, total=total, dropped=max(0, total - self.max_blocks))
        except Exception:  # pragma: no cover
            pass

    @staticmethod
    def _normalize(source: Any) -> list[dict[str, Any]]:
        if source is None:
            return []
        if isinstance(source, dict):
            return [UIRenderer._as_descriptor(source)]
        if isinstance(source, Iterable):
            return [UIRenderer._as_descriptor(item) for item in source]
        return []

    @staticmethod
    def _as_descriptor(item: Any) -> dict[str, Any]:
        """把任意块描述归一为 `{component, props_json, refs_json, source_type}`。"""
        if isinstance(item, dict):
            return {
                "component": item.get("component"),
                "props_json": item.get("props_json", item.get("props")),
                "refs_json": item.get("refs_json", item.get("refs")),
                "source_type": item.get("source_type"),
            }
        return {
            "component": getattr(item, "component", None),
            "props_json": getattr(item, "props_json", None),
            "refs_json": getattr(item, "refs_json", None),
            "source_type": getattr(item, "source_type", None),
        }


#: 各组件的缺省 `source_type`（架构 §4.1：PLAN | EVIDENCE | ARTIFACT | SYSTEM）。
_DEFAULT_SOURCE: dict[str, str] = {
    "plan_card": str(UIBlockSourceType.PLAN),
    "clarify_question": str(UIBlockSourceType.PLAN),
    "evidence_card": str(UIBlockSourceType.EVIDENCE),
    "evidence_table": str(UIBlockSourceType.EVIDENCE),
    "metric_block": str(UIBlockSourceType.EVIDENCE),
    "series_chart": str(UIBlockSourceType.EVIDENCE),
    "distribution_chart": str(UIBlockSourceType.EVIDENCE),
    "conclusion_card": str(UIBlockSourceType.ARTIFACT),
    "citation_list": str(UIBlockSourceType.ARTIFACT),
    "trace_ref": str(UIBlockSourceType.SYSTEM),
    "approval_card": str(UIBlockSourceType.SYSTEM),
    "degradation_notice": str(UIBlockSourceType.SYSTEM),
}

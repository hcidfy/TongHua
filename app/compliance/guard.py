"""合规守卫（架构 §11.3-2；03_BUILD_PLAN §3 M6）。

统一规则落地：**不得给出操作建议 / 涨跌预测 / 收益承诺**（PRD §6 / 架构 §11.3），
且**任何结论必须可反查**（无引用的 FACT、无切换条件的推断不得发布）。

契约（03_BUILD_PLAN §3 M6 接口契约）::

    Guard.check(artifact) -> GuardVerdict

三类检查：

1. **文本红线**（关键词 + 结构校验）——命中 `GUARD_REDLINES` 的关键词，或产物条目里
   出现 C1 禁止字段（`advice` / `target_price` / `rating` …）；
2. **无引用 FACT**——`facts` 条目的 `refs` 为空（架构 §11.3-2 双保险的**产物层**那一半，
   C19 渲染层是另一半，二者不得互相替代）；
3. **推断无切换条件**——`inferences` 条目缺 `condition`（C2「推断必须可失效」）。

红线规则表（`GUARD_REDLINES`）是本模块**内置数据常量**（原 `compliance/rules.py` 已并入）。
`GuardVerdict` 是**本模块内置枚举**，**不属于** `app/domain/enums.py` 的 5 个领域枚举
（契约冻结：领域枚举恒为 5 个）。

**确定性 / 离线**：纯函数式检查，不调用 LLM、不触网；同一产物必然得到同一判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "GuardVerdict",
    "GuardFinding",
    "GuardResult",
    "Guard",
    "GUARD_REDLINES",
    "GUARD_REDLINE_COUNT",
    "GUARD_FORBIDDEN_FIELDS",
    "CATEGORY_PROMISE",
    "CATEGORY_PREDICTION",
    "CATEGORY_ADVICE",
    "CATEGORY_FACT_NO_REF",
    "CATEGORY_INFERENCE_NO_CONDITION",
]

#: 违规类别（字符串常量，避免与「5 个领域枚举」的契约混淆）。
CATEGORY_PROMISE = "PROMISE"
CATEGORY_PREDICTION = "PREDICTION"
CATEGORY_ADVICE = "ADVICE"
CATEGORY_FACT_NO_REF = "FACT_NO_REF"
CATEGORY_INFERENCE_NO_CONDITION = "INFERENCE_NO_CONDITION"

#: 红线关键词 → 类别（内置数据常量；命中即违规）。
GUARD_REDLINES: tuple[tuple[str, str], ...] = (
    # ① 收益承诺
    ("保证收益", CATEGORY_PROMISE),
    ("保证盈利", CATEGORY_PROMISE),
    ("保本保收益", CATEGORY_PROMISE),
    ("稳赚不赔", CATEGORY_PROMISE),
    ("稳赚", CATEGORY_PROMISE),
    ("包赚", CATEGORY_PROMISE),
    ("无风险收益", CATEGORY_PROMISE),
    # ② 涨跌预测
    ("一定会涨", CATEGORY_PREDICTION),
    ("一定会跌", CATEGORY_PREDICTION),
    ("必涨", CATEGORY_PREDICTION),
    ("必跌", CATEGORY_PREDICTION),
    ("翻倍", CATEGORY_PREDICTION),
    ("预测股价", CATEGORY_PREDICTION),
    ("预测涨跌", CATEGORY_PREDICTION),
    ("预测", CATEGORY_PREDICTION),
    ("目标价", CATEGORY_PREDICTION),
    # ③ 操作建议
    ("建议买入", CATEGORY_ADVICE),
    ("建议卖出", CATEGORY_ADVICE),
    ("推荐买入", CATEGORY_ADVICE),
    ("推荐卖出", CATEGORY_ADVICE),
    ("推荐股票", CATEGORY_ADVICE),
    ("帮我买入", CATEGORY_ADVICE),
    ("帮我买", CATEGORY_ADVICE),
    ("帮我卖出", CATEGORY_ADVICE),
    ("帮我卖", CATEGORY_ADVICE),
    ("代我下单", CATEGORY_ADVICE),
    ("内幕消息", CATEGORY_ADVICE),
    ("操纵股价", CATEGORY_ADVICE),
)

#: 红线条目数（供自检 / 看板展示）。
GUARD_REDLINE_COUNT = len(GUARD_REDLINES)

#: 结构性禁止字段（C1：产物三层不得出现操作建议语义的字段名）。
GUARD_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {"advice", "action", "suggestion", "target_price", "rating", "buy", "sell"}
)

#: 参与文本红线扫描的三层（顺序即报告顺序）。
_LAYERS: tuple[tuple[str, str], ...] = (
    ("facts", "facts_json"),
    ("inferences", "inferences_json"),
    ("uncertainties", "uncertainties_json"),
)


class GuardVerdict(StrEnum):
    """合规判定（本模块内置枚举，**非**领域枚举）。

    用 `StrEnum` 而非 `(str, Enum)`：后者的 `str()` 会带上类名
    （`"GuardVerdict.PASS"`），会污染 API 契约与 `GuardRejected` 事件载荷。
    """

    PASS = "PASS"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class GuardFinding:
    """一条违规发现。"""

    category: str
    rule: str
    detail: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "rule": self.rule,
            "detail": self.detail,
            "location": self.location,
        }


@dataclass(slots=True)
class GuardResult:
    """判定 + 逐条发现（`check()` 只看 `.verdict`，看板看完整清单）。"""

    verdict: GuardVerdict
    findings: list[GuardFinding] = field(default_factory=list)
    strict: bool = True

    @property
    def rejected(self) -> bool:
        return self.verdict is GuardVerdict.REJECTED

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for finding in self.findings:
            if finding.category not in seen:
                seen.append(finding.category)
        return seen

    def summary(self) -> str:
        """一行摘要（写进 `guard_verdict` / `GuardRejected` 事件）。"""
        if not self.findings:
            return "PASS"
        parts = [f"{f.category}:{f.rule}" for f in self.findings[:3]]
        tail = "" if len(self.findings) <= 3 else f" 等 {len(self.findings)} 项"
        return "、".join(parts) + tail

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "strict": bool(self.strict),
            "summary": self.summary(),
            "findings": [f.to_dict() for f in self.findings],
        }


class Guard:
    """产物层合规守卫（契约：`Guard.check(artifact) -> GuardVerdict`）。

    - `strict=True`（默认，取自 `GUARD_STRICT`）：任何发现即 `REJECTED`；
    - `strict=False`：**文本类**发现（承诺 / 预测 / 建议）降为提示（记录但不拦截），
      而**结构性**发现（无引用 FACT / 推断无切换条件）仍然 `REJECTED`
      ——契约性缺陷不接受「宽松模式」。
    """

    def __init__(
        self,
        *,
        settings: Any = None,
        tracer: Any = None,
        strict: bool | None = None,
    ) -> None:
        self.settings = settings
        self.tracer = tracer
        if strict is not None:
            self.strict = bool(strict)
        else:
            self.strict = bool(getattr(settings, "guard_strict", True))

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def check(self, artifact: Any) -> GuardVerdict:
        """契约签名：返回判定枚举。"""
        return self.inspect(artifact).verdict

    def inspect(self, artifact: Any) -> GuardResult:
        """完整检查：三类检查逐条产出 `GuardFinding`。"""
        findings: list[GuardFinding] = []
        findings.extend(self._check_text(artifact))
        findings.extend(self._check_structure(artifact))
        findings.extend(self._check_facts(artifact))
        findings.extend(self._check_inferences(artifact))

        blocking = findings if self.strict else [
            f for f in findings if f.category in {CATEGORY_FACT_NO_REF, CATEGORY_INFERENCE_NO_CONDITION}
        ]
        verdict = GuardVerdict.REJECTED if blocking else GuardVerdict.PASS
        return GuardResult(verdict=verdict, findings=findings, strict=self.strict)

    # ------------------------------------------------------------------ #
    # ① 文本红线（关键词）
    # ------------------------------------------------------------------ #
    def _check_text(self, artifact: Any) -> list[GuardFinding]:
        findings: list[GuardFinding] = []
        for layer, attr in _LAYERS:
            for index, item in enumerate(self._layer(artifact, attr)):
                text = self._text_of(item)
                if not text:
                    continue
                for keyword, category in GUARD_REDLINES:
                    if keyword in text:
                        findings.append(
                            GuardFinding(
                                category=category,
                                rule=keyword,
                                detail=f"文本命中红线关键词「{keyword}」",
                                location=f"{layer}[{index}]",
                            )
                        )
        return findings

    # ------------------------------------------------------------------ #
    # ① 结构校验（C1 禁止字段）
    # ------------------------------------------------------------------ #
    def _check_structure(self, artifact: Any) -> list[GuardFinding]:
        findings: list[GuardFinding] = []
        for layer, attr in _LAYERS:
            for index, item in enumerate(self._layer(artifact, attr)):
                if not isinstance(item, dict):
                    continue
                hit = GUARD_FORBIDDEN_FIELDS & set(item)
                if hit:
                    findings.append(
                        GuardFinding(
                            category=CATEGORY_ADVICE,
                            rule=",".join(sorted(hit)),
                            detail=f"出现 C1 禁止字段：{sorted(hit)}",
                            location=f"{layer}[{index}]",
                        )
                    )
        return findings

    # ------------------------------------------------------------------ #
    # ② 无引用 FACT
    # ------------------------------------------------------------------ #
    def _check_facts(self, artifact: Any) -> list[GuardFinding]:
        findings: list[GuardFinding] = []
        for index, item in enumerate(self._layer(artifact, "facts_json")):
            refs = self._refs(item)
            if not refs:
                findings.append(
                    GuardFinding(
                        category=CATEGORY_FACT_NO_REF,
                        rule="FACT_REQUIRES_REF",
                        detail="FACT 层条目无任何引用（禁止发布不可反查的结论）",
                        location=f"facts[{index}]",
                    )
                )
        return findings

    # ------------------------------------------------------------------ #
    # ③ 推断无切换条件
    # ------------------------------------------------------------------ #
    def _check_inferences(self, artifact: Any) -> list[GuardFinding]:
        findings: list[GuardFinding] = []
        for index, item in enumerate(self._layer(artifact, "inferences_json")):
            condition = str((item or {}).get("condition") or "").strip() if isinstance(item, dict) else ""
            if not condition:
                findings.append(
                    GuardFinding(
                        category=CATEGORY_INFERENCE_NO_CONDITION,
                        rule="INFERENCE_REQUIRES_CONDITION",
                        detail="推断缺少切换条件（C2：推断必须可失效）",
                        location=f"inferences[{index}]",
                    )
                )
        return findings

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _layer(artifact: Any, attr: str) -> list[Any]:
        """取三层之一；同时兼容 `Artifact` / `ArtifactRow` / `BuildResult`。"""
        if artifact is None:
            return []
        value = getattr(artifact, attr, None)
        if value is None:
            value = getattr(artifact, attr.removesuffix("_json"), None)
        return list(value or [])

    @staticmethod
    def _text_of(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("text") or item.get("statement") or "")
        return str(item or "")

    @staticmethod
    def _refs(item: Any) -> list[Any]:
        if isinstance(item, dict):
            return list(item.get("refs") or item.get("evidence_ids") or [])
        return []

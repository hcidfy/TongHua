"""Harness 内核：产物构建器（架构 C18；03_BUILD_PLAN §3 M5）。

契约（03_BUILD_PLAN §3 M5）::

    ArtifactBuilder.build(run) -> Artifact

职责：把一次 Run 的 `Evidence`（+ 可选 `emit_artifact` 的三层结构）**确定性**地
组织成 `Artifact`——**事实 / 推断 / 不确定三个独立数组**（C2，不可合并）与引用清单
`citations`。

硬约束：

- **C1**：不产出任何「操作建议 / 涨跌预测 / 收益承诺」字段；
- **C2**：三层分离，不合并；
- **推断必须带切换条件**（03_BUILD_PLAN §3 M5 原文）：每条 inference 都必须有非空
  `condition`；模型没给时由本构建器按确定性口径补一条；
- **可反查**（架构 §12.2）：每个 citation 带 `evidence_id`，`citation → evidence → tool_call → span`。

**确定性 / 离线**：不调用 LLM、不触网；同样的证据集必然产出同样的三层结构
（除 `id` / `created_at` 外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import Artifact, new_id, now_ms

__all__ = ["ArtifactBuilder", "BuildResult", "DOMAIN_LABELS", "default_condition"]

#: 数据域（`Evidence.interface` 前缀 → 中文标签），用于「不确定层」里的覆盖度描述。
DOMAIN_LABELS: dict[str, str] = {
    "search_ticker": "标的检索",
    "get_price_snapshot": "行情快照",
    "get_price_history": "历史 K 线",
    "get_index_constituents": "板块成分",
    "get_valuation": "估值",
    "get_limit_up_stats": "涨跌停池",
    "get_hot_rank": "热度榜",
    "get_news_flash": "资讯事件",
    "get_financials": "财务报表",
}


def default_condition(domain: str) -> str:
    """推断的**缺省切换条件**（确定性口径，C2「推断必须可失效」）。

    含义：推断成立的前提是「所依赖的数据域未被后续更新覆盖 / 未发生源切换」。
    """
    label = DOMAIN_LABELS.get(domain, domain or "相关数据域")
    return f"若「{label}」数据被后续更新覆盖或数据源发生切换，本条推断失效"


@dataclass(slots=True)
class BuildResult:
    """构建结果（供管线落库与渲染共用）。"""

    artifact: Artifact
    facts: list[dict[str, Any]] = field(default_factory=list)
    inferences: list[dict[str, Any]] = field(default_factory=list)
    uncertainties: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


class ArtifactBuilder:
    """Evidence → 三层 Artifact 的确定性构建器（C18）。"""

    def __init__(self, *, settings: Any = None, tracer: Any = None) -> None:
        self.settings = settings
        self.tracer = tracer

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def build(self, run: Any) -> Artifact:
        """按 `run`（`PipelineContext` 或任意带同名字段的对象）构建 `Artifact`。"""
        return self.build_from(
            run_id=str(getattr(run, "run_id", "") or ""),
            goal=str(getattr(run, "goal", "") or ""),
            evidences=list(getattr(run, "evidences", []) or []),
            payload=self._payload_of(run),
            version=int(getattr(run, "artifact_version", 1) or 1),
        )

    def build_result(self, run: Any) -> BuildResult:
        """同 `build`，但把三层结构一并返回（渲染层需要逐层渲染）。"""
        return self.build_from(
            run_id=str(getattr(run, "run_id", "") or ""),
            goal=str(getattr(run, "goal", "") or ""),
            evidences=list(getattr(run, "evidences", []) or []),
            payload=self._payload_of(run),
            with_detail=True,
        )

    def build_from(
        self,
        run_id: str,
        *,
        goal: str = "",
        evidences: Any = (),
        payload: dict[str, Any] | None = None,
        version: int = 1,
        with_detail: bool = False,
    ) -> Any:
        """从证据集（+ 可选 `emit_artifact` 三层载荷）构建产物。

        `with_detail=False` 返回 `Artifact`（契约签名）；`True` 返回 `BuildResult`。
        """
        evidence_list = [e for e in evidences]
        facts = self._facts(evidence_list)
        inferences = self._inferences(payload, facts, evidence_list)
        uncertainties = self._uncertainties(payload, evidence_list)
        citations = self._citations(payload, evidence_list)
        degraded = bool(payload and payload.get("degraded")) or any(
            getattr(e, "degrade_reason", None) for e in evidence_list
        ) or not facts

        artifact = Artifact(
            id=new_id(),
            run_id=run_id,
            version=max(1, int(version)),
            facts_json=facts,
            inferences_json=inferences,
            uncertainties_json=uncertainties,
            citations_json=citations,
            created_at=now_ms(),
        )
        result = BuildResult(
            artifact=artifact,
            facts=facts,
            inferences=inferences,
            uncertainties=uncertainties,
            citations=citations,
            degraded=degraded,
        )
        if with_detail:
            return result
        return artifact

    # ------------------------------------------------------------------ #
    # 分层构建（全部确定性）
    # ------------------------------------------------------------------ #
    def _facts(self, evidences: list[Any]) -> list[dict[str, Any]]:
        """事实层：**逐条证据**一条事实，`refs` 恒非空（架构 §11.3-2 双保险）。

        文本口径固定为「来源 · 接口（口径）：值摘要」，不含任何评价性措辞（C1）。
        """
        facts: list[dict[str, Any]] = []
        for ev in evidences:
            evidence_id = str(getattr(ev, "id", "") or "")
            if not evidence_id:
                continue
            value = getattr(ev, "value_json", None)
            if not isinstance(value, dict) or not value or (
                isinstance(value.get("total"), int) and value["total"] == 0
                and not value.get("item") and not value.get("items")
            ):
                continue
            if "internal://emit_artifact" in str(getattr(ev, "interface", "")):
                continue
            facts.append(
                {
                    "text": self._fact_text(ev),
                    "refs": [evidence_id],
                    "confidence": getattr(ev, "confidence", None),
                    "source": getattr(ev, "source", None),
                    "as_of": getattr(ev, "as_of", None),
                    "unit": getattr(ev, "unit", None),
                    "caliber": getattr(ev, "caliber", None),
                }
            )
        return facts

    def _inferences(
        self,
        payload: dict[str, Any] | None,
        facts: list[dict[str, Any]],
        evidences: list[Any],
    ) -> list[dict[str, Any]]:
        """推断层：优先用 `emit_artifact` 给出的推断，**逐条补齐 `condition`**。

        模型未提供推断时，按「数据域覆盖」给出确定性推断（如「本次取证覆盖 N 个数据域」），
        保证推断层与事实层一样**必有切换条件**。
        """
        raw = list((payload or {}).get("inferences") or [])
        inferences: list[dict[str, Any]] = []
        if raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("statement") or "").strip()
                if not text:
                    continue
                refs = [str(r) for r in (item.get("refs") or item.get("evidence_ids") or [])]
                if not refs:
                    # 推断至少要挂到「它依据的事实」上，否则不可反查（架构 §12.2）
                    refs = [facts[0]["refs"][0]] if facts else []
                condition = str(item.get("condition") or "").strip()
                inferences.append(
                    {
                        "text": text,
                        "refs": refs,
                        "confidence": item.get("confidence"),
                        # 硬约束：推断必须带切换条件
                        "condition": condition or default_condition(self._domain_of_refs(refs, evidences)),
                    }
                )
            if inferences:
                return inferences

        domains = sorted({self._domain(ev) for ev in evidences})
        if facts:
            inferences.append(
                {
                    "text": f"本次取证覆盖 {len(domains)} 个数据域：{'、'.join(domains)}；"
                    "仅表示取证范围，不代表研究目标已得到验证。",
                    "refs": [f["refs"][0] for f in facts],
                    "confidence": min(
                        [f["confidence"] for f in facts if f["confidence"] is not None] or [0.0]
                    )
                    or None,
                    "condition": default_condition(domains[0] if domains else ""),
                }
            )
        return inferences

    def _uncertainties(
        self, payload: dict[str, Any] | None, evidences: list[Any]
    ) -> list[dict[str, Any]]:
        """不确定层：模型给出的缺口 + **降级证据的显式缺口**（C4：不得静默）。"""
        uncertainties: list[dict[str, Any]] = []
        for item in list((payload or {}).get("uncertainties") or []):
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("statement") or "").strip()
                refs = [str(r) for r in (item.get("refs") or [])]
            else:
                text = str(item).strip()
                refs = []
            if text:
                uncertainties.append({"text": text, "refs": refs})
        for ev in evidences:
            reason = getattr(ev, "degrade_reason", None)
            if reason:
                uncertainties.append(
                    {
                        "text": f"数据缺口：{getattr(ev, 'source', '')}/"
                        f"{getattr(ev, 'interface', '')} 降级（{reason}）",
                        "refs": [str(getattr(ev, "id", "") or "")],
                    }
                )
        if not evidences:
            uncertainties.append({"text": "本次 Run 未产出具证据，结论不可用。", "refs": []})
        return uncertainties

    def _citations(
        self, payload: dict[str, Any] | None, evidences: list[Any]
    ) -> list[dict[str, Any]]:
        """引用清单：逐条证据一条 citation（可 `citation → evidence → tool_call → span` 反查）。"""
        citations: list[dict[str, Any]] = []
        for ev in evidences:
            citations.append(
                {
                    "evidence_id": str(getattr(ev, "id", "") or ""),
                    "source": getattr(ev, "source", None),
                    "interface": getattr(ev, "interface", None),
                    "as_of": getattr(ev, "as_of", None),
                    "unit": getattr(ev, "unit", None),
                    "caliber": getattr(ev, "caliber", None),
                    "raw_ref": getattr(ev, "raw_ref", None),
                    "tool_call_id": getattr(ev, "tool_call_id", None),
                }
            )
        for item in list((payload or {}).get("citations") or []):
            if isinstance(item, dict):
                citations.append(dict(item))
            else:
                citations.append({"evidence_id": str(item)})
        return citations

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _payload_of(run: Any) -> dict[str, Any] | None:
        """从 run 上取 `emit_artifact` 的三层载荷（`extra["artifact_payload"]`）。"""
        payload = getattr(run, "artifact_payload", None)
        if isinstance(payload, dict):
            return payload
        extra = getattr(run, "extra", None)
        if isinstance(extra, dict):
            candidate = extra.get("artifact_payload")
            if isinstance(candidate, dict):
                return candidate
        return None

    @staticmethod
    def _domain(ev: Any) -> str:
        interface = str(getattr(ev, "interface", "") or "")
        for name in DOMAIN_LABELS:
            if interface.startswith(f"/mock/{name}") or name in interface:
                return DOMAIN_LABELS[name]
        return interface or "其他"

    def _domain_of_refs(self, refs: list[str], evidences: list[Any]) -> str:
        wanted = set(refs)
        for ev in evidences:
            if str(getattr(ev, "id", "") or "") in wanted:
                return self._domain(ev)
        return ""

    @staticmethod
    def _fact_text(ev: Any) -> str:
        """事实文本保留可复核的原始字段与前两条记录，不把元数据当结论。"""
        source = getattr(ev, "source", None) or "unknown"
        interface = getattr(ev, "interface", None) or "—"
        caliber = getattr(ev, "caliber", None) or "—"
        unit = getattr(ev, "unit", None) or "—"
        value = getattr(ev, "value_json", None)
        rows = value.get("item", value.get("items", [])) if isinstance(value, dict) else []
        fields = ("fiscal_year", "fiscal_period", "thscode", "name", "operating_income",
                  "operating_costs", "sales_fee", "manage_fee", "net_profit", "last_price",
                  "pe_ttm")
        samples = [
            "、".join(f"{key}={row[key]}" for key in fields if key in row and row[key] is not None)
            for row in rows[:3] if isinstance(row, dict)
        ] if isinstance(rows, list) else []
        detail = "；".join(filter(None, samples)) or str(value)[:180]
        return f"{source} · {interface}（口径 {caliber}，单位 {unit}）：{detail}"

/** conclusion_card —— 结论卡。按 layer 区分配色/标签：FACT=蓝、INFERENCE=紫、UNCERTAIN=灰。绝不抛错。 */

const PH = "—";

interface LayerMeta {
  cls: string;
  label: string;
  prefix: string;
}

const LAYER_META: Record<string, LayerMeta> = {
  FACT: { cls: "layer-fact", label: "事实", prefix: "[事实]" },
  INFERENCE: { cls: "layer-inference", label: "推断", prefix: "[推断]" },
  UNCERTAIN: { cls: "layer-uncertain", label: "不确定", prefix: "[不确定]" },
};

function str(v: unknown): string {
  if (v === undefined || v === null || v === "") return PH;
  return String(v);
}

function asArr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

export default function ConclusionCard(props: Record<string, unknown>) {
  const rawLayer = typeof props.layer === "string" ? props.layer : "";
  const meta: LayerMeta =
    LAYER_META[rawLayer] ?? { cls: "layer-uncertain", label: rawLayer || PH, prefix: `[${rawLayer || PH}]` };
  const refs = asArr(props.refs);
  const labels = asArr(props.citation_labels);
  const showCondition = rawLayer === "INFERENCE" && props.condition !== undefined && props.condition !== null && props.condition !== "";

  return (
    <div className={`block conclusion-card ${meta.cls}`}>
      <div className="block-head">
        <span className={`badge ${meta.cls}`}>{meta.label}</span>
        <span className="block-title">
          {meta.prefix} {str(props.text)}
        </span>
        <span className="muted">置信度 {str(props.confidence)}</span>
      </div>
      {showCondition && <p className="condition">切换条件：{str(props.condition)}</p>}
      {refs.length > 0 && <p className="muted">依据：{refs.map((_, i) => typeof labels[i] === "string" ? labels[i] : "引用详情待加载").join("、")}</p>}
    </div>
  );
}

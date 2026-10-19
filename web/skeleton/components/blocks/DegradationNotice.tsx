/** degradation_notice —— 降级通知。impact ∈ low|medium|high。纯展示组件，绝不抛错。 */

const PH = "—";

const IMPACT_CLS: Record<string, string> = {
  low: "impact-low",
  medium: "impact-medium",
  high: "impact-high",
};

function str(v: unknown): string {
  if (v === undefined || v === null || v === "") return PH;
  return String(v);
}

export default function DegradationNotice(props: Record<string, unknown>) {
  const impact = typeof props.impact === "string" ? props.impact : "";
  return (
    <div className="block degradation-notice">
      <div className="block-head">
        <span className="badge badge-degrade">降级通知</span>
        <span className="block-title">{str(props.scope)}</span>
        <span className={`badge ${IMPACT_CLS[impact] ?? ""}`}>影响 {str(props.impact)}</span>
      </div>
      <p className="muted">原因：{str(props.reason)}</p>
    </div>
  );
}

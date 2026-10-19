/** approval_card —— 审批卡。纯展示组件，缺值显示「—」，绝不抛错。 */

const PH = "—";

function str(v: unknown): string {
  if (v === undefined || v === null || v === "") return PH;
  return String(v);
}

export default function ApprovalCard(props: Record<string, unknown>) {
  return (
    <div className="block approval-card">
      <div className="block-head">
        <span className="badge badge-approval">审批</span>
        <span className="block-title">{str(props.type)}</span>
        <code className="muted">{str(props.approval_id)}</code>
      </div>
      <p className="approval-summary">{str(props.summary)}</p>
    </div>
  );
}

/** evidence_card —— 单条证据卡。纯展示组件，缺值显示「—」，绝不抛错。 */

import { evidenceHeadline, formatAsOf, text, valueRows } from "@/lib/format";

/**
 * 展示口径（解决文档 §5.4 / §5.6）：标题用「条目标题 / 标的」而不是裸 `evidence_id`，
 * `value` 按字段结构化展开（新闻类给标题，财报类给年度 / 指标名 / 数值 / 单位）。
 */
export default function EvidenceCard(props: Record<string, unknown>) {
  const rows = valueRows(props.value, 6);
  return (
    <div className="block evidence-card">
      <div className="block-head">
        <span className="badge badge-evidence">证据</span>
        <span className="block-title">{evidenceHeadline(props)}</span>
        <span className="muted">{text(props.source, 40)}</span>
        <span className="muted">{formatAsOf(props.as_of)}</span>
      </div>
      <div className="kv-grid">
        {rows.map((row) => (
          <div className="kv" key={row.key}>
            <span className="k">{row.label}</span>
            <span className="v">{text(row.value, 60)}</span>
          </div>
        ))}
      </div>
      <div className="block-foot muted">
        单位：{text(props.unit, 12)} · 口径：{text(props.caliber, 80)}
      </div>
    </div>
  );
}

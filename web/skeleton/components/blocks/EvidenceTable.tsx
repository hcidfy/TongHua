/** 证据索引：编号是数据库定位符，解释和原始字段来自同一 Run 的证据快照。 */
import { asArray, asRecord, evidenceHeadline, formatAsOf, isUrl, PH, text, valueRows } from "@/lib/format";

export default function EvidenceTable(props: Record<string, unknown>) {
  const ids = asArray(props.evidence_ids).map(String);
  const rows = asArray(props.evidences).map(asRecord);
  const byId = new Map(rows.map((row) => [String(row.evidence_id), row]));
  return (
    <div className="block evidence-table">
      <div className="block-head">
        <span className="badge badge-evidence">证据表</span>
        <span className="block-title">证据索引</span>
        <span className="muted">{ids.length} 条</span>
      </div>
      <p className="muted">编号只用于定位原始取数，不是证券代码或结论。点击条目可查看来源、时点、口径与字段。</p>
      {ids.length === 0 ? (
        <p className="muted">{PH}</p>
      ) : (
        <ul className="evidence-index">
          {ids.map((id, i) => {
            const row = byId.get(id);
            return <li key={id}>
              <details>
                <summary>证据 {i + 1} · {row ? evidenceHeadline(row) : "详情载入中或不可用"}</summary>
                {row ? <div className="evidence-index-detail">
                  <p>来源：{text(row.source)} · 时点：{formatAsOf(row.as_of)} · 单位：{text(row.unit)} · 口径：{text(row.caliber)}</p>
                  {Boolean(row.degrade_reason) && <p className="condition">数据限制：{text(row.degrade_reason, 160)}</p>}
                  <dl>{valueRows(row.value, 12).map((v) => <div className="kv" key={v.key}><dt>{v.label}</dt><dd>{text(v.value, 120)}</dd></div>)}</dl>
                  {isUrl(row.raw_ref) && <a href={row.raw_ref} target="_blank" rel="noreferrer">查看原文</a>}
                  <p className="muted">内部追溯编号：<code>{id}</code></p>
                </div> : <p className="muted">当前无法读取详情，请核查证据接口；不能仅凭编号确认事实。</p>}
              </details>
            </li>
          })}
        </ul>
      )}
    </div>
  );
}

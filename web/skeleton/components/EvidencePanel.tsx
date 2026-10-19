"use client";

import { useState } from "react";
import {
  evidenceHeadline,
  formatAsOf,
  isUrl,
  PH,
  text,
  valueRows,
} from "@/lib/format";

export interface EvidencePanelProps {
  evidences: Array<Record<string, unknown>>;
}

/** 值摘要：单条标量直接给；对象已由 `valueRows` 展开，这里只做兜底。 */
function summary(e: Record<string, unknown>): string {
  return text(e.value, 400);
}

/**
 * 证据面板 —— 研究引用面板（解决文档 §5.4）。
 *
 * 不再暴露裸 `evidence_id`：列表给「条目标题 / 标的名称等 N 条」，
 * 详情把 `value` 结构化展开、`raw_ref` 是 URL 时渲染为可点击链接。
 */
export default function EvidencePanel({ evidences }: EvidencePanelProps) {
  const list = Array.isArray(evidences) ? evidences : [];
  const [selected, setSelected] = useState(0);

  const index = list.length === 0 ? -1 : Math.min(Math.max(selected, 0), list.length - 1);
  const current = index >= 0 ? list[index] : undefined;

  return (
    <div className="panel evidence-panel">
      <div className="panel-head">证据（{list.length}）</div>
      {list.length === 0 ? (
        <p className="muted">暂无证据</p>
      ) : (
        <>
          <ul className="evidence-list">
            {list.map((e, i) => (
              <li key={i} className={i === index ? "sel" : undefined}>
                <button type="button" className="link" onClick={() => setSelected(i)}>
                  <span className="evidence-title">{evidenceHeadline(e)}</span>
                  <span className="muted">
                    {text(e.source, 24)}
                    {isUrl(e.raw_ref) ? " · 有原文链接" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {current && (
            <div className="evidence-detail">
              <div className="kv">
                <span className="k">来源</span>
                <span className="v">{text(current.source, 60)}</span>
              </div>
              <div className="kv">
                <span className="k">时点</span>
                <span className="v">{formatAsOf(current.as_of)}</span>
              </div>
              <div className="kv">
                <span className="k">单位</span>
                {/* unit 为 null 是合法值（新闻/热榜类无自然单位）→ 显示「—」而非丢弃 */}
                <span className="v">{text(current.unit, 24)}</span>
              </div>
              <div className="kv">
                <span className="k">口径</span>
                <span className="v">{text(current.caliber, 80)}</span>
              </div>
              <div className="kv">
                <span className="k">原文</span>
                <span className="v">
                  {isUrl(current.raw_ref) ? (
                    <a className="ext-link" href={current.raw_ref} target="_blank" rel="noreferrer">
                      {text(current.raw_ref, 80)}
                    </a>
                  ) : (
                    text(current.raw_ref, 80)
                  )}
                </span>
              </div>

              <div className="panel-subhead">数据（{text(current.unit, 12) === PH ? "无单位" : text(current.unit, 12)}）</div>
              <table className="data-table">
                <tbody>
                  {valueRows(current.value, 10).map((row) => (
                    <tr key={row.key}>
                      <th>{row.label}</th>
                      <td>{text(row.value, 120)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <details>
                <summary className="muted">查看原始值</summary>
                <pre className="json">{summary(current)}</pre>
              </details>
            </div>
          )}
        </>
      )}
    </div>
  );
}

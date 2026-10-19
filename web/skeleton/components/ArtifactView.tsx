/**
 * ArtifactView —— 研究产物：事实 / 推断 / 不确定三层 + 引用清单。
 *
 * 展示口径（解决文档 §5.6）：不打印 JSON，而是把每条三层结论渲染成
 * 「结论正文 + 切换条件 + 置信度 + 引用条数」；引用渲染成可读文本，
 * `raw_ref` 为 URL 时可点击。缺值显示「—」，绝不抛错。
 */

import { evidenceHeadline, financialResearch, formatAsOf, PH, citationUrl, conclusionParts, text, valueRows } from "@/lib/format";

const SECTIONS: Array<{ key: string; title: string; cls: string; hint: string }> = [
  { key: "facts_json", title: "事实", cls: "layer-fact", hint: "有据可查的客观取值" },
  { key: "inferences_json", title: "推断", cls: "layer-inference", hint: "基于事实的方向性判断（附切换条件）" },
  { key: "uncertainties_json", title: "不确定", cls: "layer-uncertain", hint: "本次取证未覆盖 / 已降级的部分" },
];

/** 兼容「数组」与「JSON 字符串」两种形态。 */
function readList(v: unknown): unknown[] {
  if (Array.isArray(v)) return v;
  if (typeof v === "string" && v !== "") {
    try {
      const parsed = JSON.parse(v);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

export interface ArtifactViewProps {
  artifact: Record<string, unknown> | null;
  evidences?: Array<Record<string, unknown>>;
  goal?: string;
}

export default function ArtifactView({ artifact, evidences = [], goal = "" }: ArtifactViewProps) {
  if (!artifact) {
    return (
      <div className="panel artifact-view">
        <div className="panel-head">研究产物</div>
        <p className="muted">暂无产物</p>
      </div>
    );
  }

  const citations = readList(artifact.citations_json);
  const byId = new Map(evidences.map((ev, index) => [String(ev.evidence_id), { ev, index }]));
  const research = financialResearch(evidences, goal);
  const hasFinancialGoal = /收入|财报|利润|财务/.test(goal);
  const refLabel = (refs: string[]) => refs.map((id) => {
    const hit = byId.get(id);
    return hit ? `证据 ${hit.index + 1}（${evidenceHeadline(hit.ev)}）` : "引用未解析，需核对证据";
  }).join("、");

  return (
    <div className="panel artifact-view">
      <div className="panel-head">研究产物</div>
      <section className="research-report">
        <h3>研究结论与核验说明</h3>
        <p>研究目标：{goal || "当前研究任务"}。以下仅据本次 Run 可回查的原始字段陈述，不构成买卖建议或收益预测。</p>
        {hasFinancialGoal ? (
          <>
            <h4>本次返回的可回查取值</h4>
            {research.facts.length ? research.facts.map((line, i) =>
              <p key={i}>{line.text} <strong>依据：{refLabel(line.refs)}</strong></p>
            ) : <p>尚无可回查的年度收入数值。</p>}
            <h4>结论边界</h4>
            {research.limitations.map((line) => <p className="condition" key={line}>{line}</p>)}
          </>
        ) : (
          <>
            <h4>本次可回查的资料</h4>
            {evidences.length ? evidences.filter((ev) => ev.interface !== "internal://emit_artifact").map((ev, i) => (
              <p key={String(ev.evidence_id ?? i)}>
                {evidenceHeadline(ev)}：{valueRows(ev.value, 4).map((v) => `${v.label} ${text(v.value, 50)}`).join("；")}。
                来源 {text(ev.source, 24)}，数据时点 {formatAsOf(ev.as_of)}，口径 {text(ev.caliber, 80)}。
                依据：{refLabel([String(ev.evidence_id ?? "")])}。
                {ev.degrade_reason ? ` 数据限制：${text(ev.degrade_reason, 100)}。` : ""}
              </p>
            )) : <p>暂无可回查资料，不能形成事实结论。</p>}
            <h4>尚未验证</h4>
            {readList(artifact.uncertainties_json).length
              ? readList(artifact.uncertainties_json).map((item, i) => <p key={i}>{conclusionParts(item).text}</p>)
              : <p>模型推断不等同于原始事实；需逐项核对下方引用与资料口径。</p>}
          </>
        )}
      </section>
      {SECTIONS.map((s) => {
        const items = readList(artifact[s.key]);
        return (
          <section key={s.key} className={`artifact-section ${s.cls}`}>
            <h4>
              {s.title}{s.key === "inferences_json" ? "（模型判断，需人工核验）" : ""}（{items.length}）
              <span className="muted"> · {s.hint}</span>
            </h4>
            {items.length === 0 ? (
              <p className="muted">{PH}</p>
            ) : (
              <ul className="conclusion-list">
                {items.map((it, i) => {
                  const p = conclusionParts(it);
                  return (
                    <li key={i}>
                      <p className="conclusion-text">{p.text}</p>
                      {p.condition && <p className="condition">切换条件：{p.condition}</p>}
                      <p className="muted">置信度 {p.confidence ?? PH} · {p.refs.length ? `依据：${refLabel(p.refs)}` : "无可回查证据，请勿视为已核实结论"}</p>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
      <section className="artifact-section">
        <h4>
          引用（{citations.length}）
          <span className="muted"> · 支撑结论的来源与原文</span>
        </h4>
        {citations.length === 0 ? (
          <p className="muted">{PH}</p>
        ) : (
          <ul className="conclusion-list">
            {citations.map((c, i) => {
              const url = citationUrl(c);
              const id = String((c as Record<string, unknown>)?.evidence_id ?? "");
              const hit = byId.get(id);
              return (
                <li key={i}>
                  <span className="conclusion-text">{hit
                    ? `证据 ${hit.index + 1} · ${evidenceHeadline(hit.ev)} · ${text(hit.ev.source)} · ${formatAsOf(hit.ev.as_of)} · ${text(hit.ev.caliber, 80)}`
                    : "引用尚未解析：缺少对应证据详情，不能据此核实结论"}</span>
                  {url && (
                    <a className="ext-link" href={url} target="_blank" rel="noreferrer">
                      原文链接
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

/** clarify_question —— 澄清问题列表。纯展示组件，缺值显示「—」，绝不抛错。 */

const PH = "—";

function asObj(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function asArr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function questionText(q: unknown): string {
  if (typeof q === "string") return q === "" ? PH : q;
  const o = asObj(q);
  const t = o.text ?? o.question ?? o.title;
  if (t === undefined || t === null) {
    try {
      return JSON.stringify(q) ?? PH;
    } catch {
      return PH;
    }
  }
  return String(t);
}

export default function ClarifyQuestion(props: Record<string, unknown>) {
  const questions = asArr(props.questions);
  return (
    <div className="block clarify-question">
      <div className="block-head">
        <span className="badge badge-clarify">澄清</span>
        <span className="block-title">待澄清问题</span>
        <span className="muted">{questions.length} 条</span>
      </div>
      {questions.length === 0 ? (
        <p className="muted">{PH}</p>
      ) : (
        <ul className="clarify-list">
          {questions.map((q, i) => (
            <li key={i}>{questionText(q)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

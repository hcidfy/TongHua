/** RunStatus —— Run 状态面板 + 暂停/恢复/终止按钮。 */

export interface RunStatusProps {
  status?: string | null;
  costTotal?: number | string | null;
  tokensTotal?: number | string | null;
  stopReason?: string | null;
  onPause?: () => void;
  onResume?: () => void;
  onAbort?: () => void;
}

function show(v: unknown): string {
  if (v === undefined || v === null || v === "") return "—";
  return String(v);
}

export default function RunStatus({
  status,
  costTotal,
  tokensTotal,
  stopReason,
  onPause,
  onResume,
  onAbort,
}: RunStatusProps) {
  return (
    <div className="panel run-status">
      <div className="panel-head">运行状态</div>
      <div className="kv">
        <span className="k">状态</span>
        <span className="v">{show(status)}</span>
      </div>
      <div className="kv">
        <span className="k">cost_total</span>
        <span className="v">{show(costTotal)}</span>
      </div>
      <div className="kv">
        <span className="k">tokens_total</span>
        <span className="v">{show(tokensTotal)}</span>
      </div>
      <div className="kv">
        <span className="k">stop_reason</span>
        <span className="v">{show(stopReason)}</span>
      </div>
      <div className="actions">
        <button type="button" onClick={onPause} disabled={!onPause}>
          暂停
        </button>
        <button type="button" onClick={onResume} disabled={!onResume}>
          恢复
        </button>
        <button type="button" className="danger" onClick={onAbort} disabled={!onAbort}>
          终止
        </button>
      </div>
    </div>
  );
}

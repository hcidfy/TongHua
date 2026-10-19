/**
 * FallbackJsonCard —— 渲染层兜底组件。
 * 当区块不可渲染（未命中白名单 / 未实现 / 校验失败 / 组件抛错）时展示原始 JSON。
 * 绝不抛错、绝不白屏。
 */

interface FallbackJsonCardProps {
  block: unknown;
  reason?: string;
}

function safeStringify(block: unknown): string {
  try {
    const out = JSON.stringify(block, null, 2);
    return out === undefined ? String(block) : out;
  } catch {
    try {
      return String(block);
    } catch {
      return "[无法序列化]";
    }
  }
}

export default function FallbackJsonCard({ block, reason }: FallbackJsonCardProps) {
  return (
    <div className="block fallback-card">
      <div className="block-head">
        <span className="badge badge-warn">已降级</span>
        <span className="block-title">该区块无法渲染，已降级</span>
        <span className="render-status">render_status=failed</span>
      </div>
      {reason && <p className="muted">原因：{reason}</p>}
      <pre className="json">{safeStringify(block)}</pre>
    </div>
  );
}

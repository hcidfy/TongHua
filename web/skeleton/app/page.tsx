import { SSE_EVENT_COUNT, SSE_EVENT_TYPES } from "@/lib/sse";

/**
 * M0 前端空壳：只证明「布局 + 路由 + SSE 契约」已就位，不含任何业务页面（HANDOFF §5.1）。
 * 业务页面（计划 DAG / 证据面板 / 产物视图）属 M5。
 */
export default function Home() {
  return (
    <div className="card">
      <h1>研究执行器 · 空壳</h1>
      <p className="muted">
        M0 交付物：布局与路由可用、SSE 连接封装已冻结。后端 API 到 M1/M5 才提供，
        当前页面不发起业务请求。
      </p>
      <h2>SSE 事件契约（{SSE_EVENT_COUNT} 类）</h2>
      <ul>
        {SSE_EVENT_TYPES.map((event) => (
          <li key={event}>
            <code>{event}</code>
          </li>
        ))}
      </ul>
      <p className="muted">
        后端基址由环境变量 <code>NEXT_PUBLIC_API_BASE</code> 提供（缺省回落
        <code>http://127.0.0.1:8000</code>）。
      </p>
    </div>
  );
}

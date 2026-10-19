/**
 * plan_card —— 研究计划 + 执行链路（任务 → 工具 → 结果）。
 *
 * 纯展示组件，缺值显示「—」，绝不抛错。
 * 执行链路由 `props.execution`（`seq → PlanTaskChain`）注入，来自
 * `lib/format.ts::planChains`；未注入时只显示计划期的工具清单（解决文档 §5.5）。
 */

import { PH, asArray, asRecord, text, type PlanTaskChain } from "@/lib/format";

const STATUS_CLS: Record<string, string> = {
  已完成: "badge-evidence",
  执行中: "badge-plan",
  失败: "badge-degrade",
  降级完成: "badge-warn",
};

function statusClass(status: string): string {
  return STATUS_CLS[status] ?? "badge";
}

export default function PlanCard(props: Record<string, unknown>) {
  const tasks = asArray(props.tasks).map(asRecord);
  const execution = asRecord(props.execution);

  return (
    <div className="block plan-card">
      <div className="block-head">
        <span className="badge badge-plan">计划</span>
        <span className="block-title">研究计划</span>
        <span className="muted">{tasks.length} 个任务</span>
      </div>
      {tasks.length === 0 ? (
        <p className="muted">{PH}</p>
      ) : (
        <ol className="plan-tasks">
          {tasks.map((t, i) => {
            const seq = t.seq === undefined ? i + 1 : t.seq;
            const deps = asArray(t.deps);
            const chain = execution[String(seq)] as PlanTaskChain | undefined;
            const tools =
              chain && chain.tools.length > 0
                ? chain.tools
                : asArray(t.tools).map((tool) => ({
                    tool: String(tool),
                    result: null as string | null,
                    evidenceCount: 0,
                  }));

            return (
              <li className="plan-task" key={i}>
                <div className="plan-task-head">
                  <span className="seq">#{text(seq, 8)}</span>
                  <span className="task-title">{text(t.title, 80)}</span>
                  {chain && <span className={`badge ${statusClass(chain.status)}`}>{chain.status}</span>}
                  <span className="muted">{text(t.intent, 120)}</span>
                  {deps.length > 0 && (
                    <span className="muted">依赖：{deps.map((d) => String(d)).join("、")}</span>
                  )}
                </div>
                {tools.length > 0 && (
                  <ul className="plan-tools">
                    {tools.map((tool) => (
                      <li key={tool.tool}>
                        <span className="tool-name">{tool.tool}</span>
                        {tool.result !== null && (
                          <span className="tool-result">
                            结果：{tool.result}
                            {tool.evidenceCount > 0 ? `（${tool.evidenceCount} 条证据）` : ""}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

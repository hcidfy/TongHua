"use client";

import { useCallback, useMemo, useState } from "react";
import { resolveApiBase } from "@/lib/sse";
import { evidenceHeadline, planChains } from "@/lib/format";
import { type UiBlock } from "@/lib/ui-contract";
import { useRunStream } from "@/hooks/useRunStream";
import DynamicRenderer from "@/components/DynamicRenderer";
import EvidencePanel from "@/components/EvidencePanel";
import ArtifactView from "@/components/ArtifactView";
import RunStatus from "@/components/RunStatus";

function pickString(obj: unknown, keys: string[]): string | null {
  if (typeof obj !== "object" || obj === null) return null;
  const rec = obj as Record<string, unknown>;
  for (const k of keys) {
    const v = rec[k];
    if (typeof v === "string" && v !== "") return v;
    if (typeof v === "number") return String(v);
    if (typeof v === "object" && v !== null) {
      const nested = (v as Record<string, unknown>).id;
      if (typeof nested === "string" && nested !== "") return nested;
    }
  }
  return null;
}

export default function ConsolePage() {
  const base = resolveApiBase();
  const [goal, setGoal] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const stream = useRunStream(runId);

  /**
   * 计划卡补挂「任务 → 工具 → 结果」链路（解决文档 §5.5）。
   * 计划卡只在计划期产出（只有 `seq` / `tools`），执行结果得由 REST 快照回填。
   */
  const blocks = useMemo<UiBlock[]>(() => {
    return stream.uiBlocks.map((b) => {
      if (b.component === "conclusion_card") {
        const refs = Array.isArray(b.refs) ? b.refs : [];
        return { ...b, props: {
          ...b.props,
          citation_labels: refs.map((ref) => {
            const index = stream.runEvidences.findIndex((ev) => ev.evidence_id === ref);
            return index >= 0 ? `证据 ${index + 1}（${evidenceHeadline(stream.runEvidences[index])}）` : "引用尚未解析";
          }),
        } };
      }
      if (b.component === "evidence_table") {
        return { ...b, props: { ...b.props, evidences: stream.runEvidences } };
      }
      if (b.component !== "plan_card") return b;
      const planTasks = Array.isArray(b.props?.tasks)
        ? (b.props.tasks as Array<Record<string, unknown>>)
        : [];
      return {
        ...b,
        props: {
          ...b.props,
          execution: planChains(planTasks, stream.runTasks, stream.runEvidences, stream.runToolCalls),
        },
      };
    });
  }, [stream.uiBlocks, stream.runTasks, stream.runEvidences, stream.runToolCalls]);

  /** 证据面板优先用 REST 快照（多了 `raw_ref` / `task_id`，可渲染原文链接）。 */
  const evidences = useMemo<Array<Record<string, unknown>>>(() => {
    if (stream.runEvidences.length > 0) return stream.runEvidences;
    const seen = new Set<string>();
    const out: Array<Record<string, unknown>> = [];
    for (const b of stream.uiBlocks) {
      if (b.component !== "evidence_card") continue;
      const props = b.props ?? {};
      const key = String(props.evidence_id ?? out.length);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ ...props });
    }
    return out;
  }, [stream.runEvidences, stream.uiBlocks]);

  const doneObj = (stream.done ?? {}) as Record<string, unknown>;

  const startRun = useCallback(async () => {
    const trimmed = goal.trim();
    if (!trimmed) {
      setMessage("请先填写研究目标");
      return;
    }
    setBusy(true);
    setMessage(null);
    setThreadId(null);
    setRunId(null);
    try {
      const tRes = await fetch(`${base}/api/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: trimmed }),
      });
      if (!tRes.ok) throw new Error(`创建线程失败：HTTP ${tRes.status}`);
      const tData: unknown = await tRes.json();
      const tid = pickString(tData, ["thread_id", "id", "thread"]);
      if (!tid) throw new Error("创建线程响应缺少 thread_id");
      setThreadId(tid);

      const rRes = await fetch(`${base}/api/threads/${encodeURIComponent(tid)}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: trimmed }),
      });
      if (!rRes.ok) throw new Error(`发起运行失败：HTTP ${rRes.status}`);
      const rData: unknown = await rRes.json();
      const rid = pickString(rData, ["run_id", "id", "run"]);
      if (!rid) throw new Error("发起运行响应缺少 run_id");
      setRunId(rid);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [base, goal]);

  const control = useCallback(
    async (action: "pause" | "resume" | "abort") => {
      if (!runId) return;
      try {
        const res = await fetch(`${base}/api/runs/${encodeURIComponent(runId)}/${action}`, {
          method: "POST",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setMessage(`${action} 已发送`);
      } catch (e) {
        setMessage(e instanceof Error ? e.message : String(e));
      }
    },
    [base, runId],
  );

  return (
    <div className="console">
      <section className="card console-head">
        <h1>研究控制台</h1>
        <div className="row">
          <input
            className="input"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="输入研究目标，例如：分析某公司近三年收入结构变化"
          />
          <button type="button" onClick={startRun} disabled={busy}>
            {busy ? "启动中…" : "开始研究"}
          </button>
        </div>
        {message && <p className="muted">{message}</p>}
        <p className="muted">
          后端基址：{base}
          {threadId ? ` · thread ${threadId}` : ""}
          {runId ? ` · run ${runId}` : ""}
          {stream.connected ? " · 已连接" : ""}
        </p>
      </section>

      <div className="grid">
        <section className="col">
          <h2>动态 UI</h2>
          {blocks.length === 0 ? (
            <p className="muted">暂无区块</p>
          ) : (
            blocks.map((b, i) => <DynamicRenderer key={i} block={b} />)
          )}
          {stream.error && <pre className="json">{JSON.stringify(stream.error, null, 2)}</pre>}
          {stream.steps.length > 0 && (
            <div className="panel">
              <div className="panel-head">步骤（{stream.steps.length}）</div>
              <ul className="step-list">
                {stream.steps.slice(-20).map((s, i) => {
                  const rec = (s ?? {}) as Record<string, unknown>;
                  return (
                    <li key={i}>
                      <span className="badge">{String(rec.type ?? "—")}</span>
                      <span className="muted">{String(rec.tool ?? rec.task_id ?? "")}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>

        <aside className="col">
          <RunStatus
            status={stream.status}
            costTotal={(doneObj.cost as number | undefined) ?? null}
            tokensTotal={(doneObj.tokens_total as number | undefined) ?? null}
            stopReason={(doneObj.stop_reason as string | undefined) ?? null}
            onPause={runId ? () => control("pause") : undefined}
            onResume={runId ? () => control("resume") : undefined}
            onAbort={runId ? () => control("abort") : undefined}
          />
          <ArtifactView artifact={stream.artifact} evidences={evidences} goal={goal} />
          <EvidencePanel evidences={evidences} />
          {stream.approval && (
            <div className="panel">
              <div className="panel-head">审批</div>
              <pre className="json">{JSON.stringify(stream.approval, null, 2)}</pre>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

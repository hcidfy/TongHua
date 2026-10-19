"use client";

import { useEffect, useState } from "react";
import { connectRunStream, resolveApiBase } from "@/lib/sse";
import { applyBlockBudget, DEFAULT_UI_BLOCK_MAX, type UiBlock } from "@/lib/ui-contract";

export interface UseRunStreamResult {
  uiBlocks: UiBlock[];
  status: string | null;
  steps: unknown[];
  /** REST 快照：`GET /api/runs/{id}` 的 `tasks[]`（含 `task_id / seq / status`）。 */
  runTasks: Array<Record<string, unknown>>;
  /** REST 快照：`GET /api/runs/{id}/evidences`（含 `task_id / interface / raw_ref`）。 */
  runEvidences: Array<Record<string, unknown>>;
  runToolCalls: Array<Record<string, unknown>>;
  approval: Record<string, unknown> | null;
  artifact: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  done: Record<string, unknown> | null;
  connected: boolean;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * 拉取该 Run 的**最新完整产物**：`GET /api/runs/{id}/artifacts` → `latest`。
 *
 * SSE 的 `artifact` 帧按冻结契约只下发 `{artifact_id}`（不含 `facts_json` 等），
 * 因此前端必须主动回查 REST，否则右侧「研究产物」永远是 0（解决文档 §5.2 方案 A）。
 */
async function fetchLatestArtifact(runId: string): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${resolveApiBase()}/api/runs/${encodeURIComponent(runId)}/artifacts`);
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (!isRecord(body)) return null;
    const latest = body.latest;
    return isRecord(latest) ? latest : null;
  } catch {
    return null;
  }
}

/**
 * 拉取该 Run 的**执行快照**：`GET /api/runs/{id}`（`tasks`）+ `GET /api/runs/{id}/evidences`。
 *
 * 用途：SSE 的 `step` 帧只带 `{task_id, seq, type, tool?}`，不含任务状态与取值；
 * 「任务 → 工具 → 结果」链路需要 `tasks.task_id ↔ tasks.seq` 才能把计划卡（只有 `seq`）
 * 与证据（只有 `task_id`）对上，故必须回查 REST（解决文档 §5.5）。
 */
async function fetchRunSnapshot(runId: string): Promise<{
  tasks: Array<Record<string, unknown>>;
  evidences: Array<Record<string, unknown>>;
  toolCalls: Array<Record<string, unknown>>;
} | null> {
  const base = resolveApiBase();
  try {
    const [runRes, evRes] = await Promise.all([
      fetch(`${base}/api/runs/${encodeURIComponent(runId)}`),
      fetch(`${base}/api/runs/${encodeURIComponent(runId)}/evidences`),
    ]);
    if (!runRes.ok || !evRes.ok) return null;
    const run: unknown = await runRes.json();
    const evs: unknown = await evRes.json();
    return {
      tasks: isRecord(run) && Array.isArray(run.tasks) ? run.tasks.filter(isRecord) : [],
      evidences: isRecord(evs) && Array.isArray(evs.evidences) ? evs.evidences.filter(isRecord) : [],
      toolCalls: isRecord(evs) && Array.isArray(evs.tool_calls) ? evs.tool_calls.filter(isRecord) : [],
    };
  } catch {
    return null;
  }
}

/** 从 SSE data 中提取 ui_block，形状不合法则返回 null（由渲染层兜底）。 */
function toUiBlock(data: unknown): UiBlock | null {
  const payload = isRecord(data) ? data.ui_block : undefined;
  if (!isRecord(payload) || typeof payload.component !== "string") return null;
  return {
    component: payload.component,
    props: isRecord(payload.props) ? payload.props : undefined,
    refs: Array.isArray(payload.refs) ? payload.refs : undefined,
    render_status: typeof payload.render_status === "string" ? payload.render_status : undefined,
  };
}

/**
 * 基于 lib/sse.ts 的 connectRunStream 消费一次 Run 的 SSE 流。
 * 卸载或 runId 变化时关闭 EventSource。
 */
export function useRunStream(runId: string | null | undefined): UseRunStreamResult {
  const [uiBlocks, setUiBlocks] = useState<UiBlock[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [steps, setSteps] = useState<unknown[]>([]);
  const [runTasks, setRunTasks] = useState<Array<Record<string, unknown>>>([]);
  const [runEvidences, setRunEvidences] = useState<Array<Record<string, unknown>>>([]);
  const [runToolCalls, setRunToolCalls] = useState<Array<Record<string, unknown>>>([]);
  const [approval, setApproval] = useState<Record<string, unknown> | null>(null);
  const [artifact, setArtifact] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<Record<string, unknown> | null>(null);
  const [done, setDone] = useState<Record<string, unknown> | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setUiBlocks([]);
    setStatus(null);
    setSteps([]);
    setRunTasks([]);
    setRunEvidences([]);
    setRunToolCalls([]);
    setApproval(null);
    setArtifact(null);
    setError(null);
    setDone(null);
    setConnected(false);

    if (!runId) return;

    const refreshSnapshot = () => {
      void fetchRunSnapshot(runId).then((snap) => {
        if (!snap) return;
        setRunTasks(snap.tasks);
        setRunEvidences(snap.evidences);
        setRunToolCalls(snap.toolCalls);
      });
    };

    const appendBlock = (data: unknown) => {
      const block = toUiBlock(data);
      if (!block) return;
      // 含 render_status==="failed" 的块也保留，由渲染层显示降级卡
      setUiBlocks((prev) => applyBlockBudget([...prev, block], DEFAULT_UI_BLOCK_MAX));
    };

    const source = connectRunStream(runId, {
      status: (data) => setStatus(isRecord(data) && typeof data.status === "string" ? data.status : null),
      plan: appendBlock,
      evidence: appendBlock,
      ui: appendBlock,
      degrade: appendBlock,
      step: (data) => {
        setSteps((prev) => [...prev, data]);
        // 每一轮工具观测结束刷一次快照：任务状态与证据随 Run 推进增量更新
        if (isRecord(data) && data.type === "OBSERVE") refreshSnapshot();
      },
      approval: (data) => setApproval(isRecord(data) ? data : null),
      artifact: (data) => {
        // 帧里只有 `artifact_id`：先落一个占位，再回查 REST 换成完整产物（三层数组 + citations）。
        const frame = isRecord(data) ? data : null;
        setArtifact(frame);
        void fetchLatestArtifact(runId).then((latest) => {
          if (latest) setArtifact(latest);
        });
        refreshSnapshot();
      },
      error: (data) => setError(isRecord(data) ? data : null),
      done: (data) => {
        setDone(isRecord(data) ? data : null);
        refreshSnapshot();
      },
    });

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    return () => {
      source.onopen = null;
      source.onerror = null;
      source.close();
    };
  }, [runId]);

  return {
    uiBlocks,
    status,
    steps,
    runTasks,
    runEvidences,
    runToolCalls,
    approval,
    artifact,
    error,
    done,
    connected,
  };
}

export default useRunStream;

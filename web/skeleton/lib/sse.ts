/**
 * SSE 事件契约（M0 前端空壳的唯一对外约定）。
 *
 * 来源：docs/02_ARCHITECTURE.md §13.1 —— 共 **10 类** 事件。
 * 后端到 M5 才实现推送；M0 只冻结「事件名 + data 形状」并给出连接封装。
 */

export const SSE_EVENT_TYPES = [
  "status",
  "plan",
  "step",
  "evidence",
  "ui",
  "approval",
  "degrade",
  "artifact",
  "done",
  "error",
] as const;

export type SseEventType = (typeof SSE_EVENT_TYPES)[number];

/** 契约数量：10 类（03_BUILD_PLAN §10）。改动即破坏契约。 */
export const SSE_EVENT_COUNT = 10;

export interface SseStatusData {
  run_id: string;
  status: string;
}

export interface SsePlanData {
  ui_block: UiBlockPayload;
}

export interface SseStepData {
  task_id: string;
  type: "THINK" | "ACT" | "OBSERVE";
  tool?: string;
}

export interface SseEvidenceData {
  ui_block: UiBlockPayload;
}

export interface SseUiData {
  ui_block: UiBlockPayload;
}

export interface SseApprovalData {
  approval_id: string;
  type: string;
}

export interface SseDegradeData {
  ui_block: UiBlockPayload;
}

export interface SseArtifactData {
  artifact_id: string;
}

export interface SseDoneData {
  run_id: string;
  status: string;
  cost?: number;
}

export interface SseErrorData {
  code: number;
  message: string;
}

/** 动态 UI 块（组件白名单校验在渲染层做，见架构 §11.2/§11.3）。 */
export interface UiBlockPayload {
  component: string;
  props?: Record<string, unknown>;
  refs?: unknown[];
  render_status?: string;
}

export type SseHandlers = {
  [K in SseEventType]?: (data: unknown) => void;
};

export interface ConnectOptions {
  /** 后端基址；不传则用 NEXT_PUBLIC_API_BASE。 */
  baseUrl?: string;
  /** 断线自动重连（浏览器 EventSource 原生行为，默认开启）。 */
  withCredentials?: boolean;
}

/** 取后端基址：前端环境变量优先，缺省回落本机后端。 */
export function resolveApiBase(explicit?: string): string {
  if (explicit) return explicit.replace(/\/+$/, "");
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE;
  return (fromEnv ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

/**
 * 订阅一次 Run 的 SSE 流：`GET /api/runs/{id}/stream`。
 * 返回可关闭的 EventSource；SSE 的 `error` 事件名与浏览器连接错误同名，
 * 因此连接层错误通过 `onConnectionError` 回调暴露，避免与业务 `error` 事件混淆。
 */
export function connectRunStream(
  runId: string,
  handlers: SseHandlers,
  options: ConnectOptions & { onConnectionError?: (event: Event) => void } = {},
): EventSource {
  const url = `${resolveApiBase(options.baseUrl)}/api/runs/${encodeURIComponent(runId)}/stream`;
  const source = new EventSource(url, { withCredentials: options.withCredentials ?? false });

  for (const eventType of SSE_EVENT_TYPES) {
    const handler = handlers[eventType];
    if (!handler) continue;
    source.addEventListener(eventType, (event) => {
      const raw = (event as MessageEvent<string>).data;
      let payload: unknown = raw;
      try {
        payload = raw ? JSON.parse(raw) : null;
      } catch {
        /* 非 JSON 帧按原样透传，由调用方决定 */
      }
      handler(payload);
    });
  }

  if (options.onConnectionError) {
    source.onerror = options.onConnectionError;
  }
  return source;
}

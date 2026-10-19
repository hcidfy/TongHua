/**
 * M5 动态 UI 契约（架构 §11.2 / §11.3）。
 *
 * 组件白名单冻结为 12 个；24h 阶段仅实现其中 7 个，其余必须走降级渲染。
 * 本文件为纯 TS，**不含 JSX**，可被服务端组件直接 import。
 */

/** 白名单 12 个组件名（冻结，不可增删/改名）。 */
export const UI_COMPONENT_WHITELIST = [
  "plan_card",
  "clarify_question",
  "evidence_card",
  "evidence_table",
  "conclusion_card",
  "approval_card",
  "degradation_notice",
  "metric_block",
  "series_chart",
  "distribution_chart",
  "citation_list",
  "trace_ref",
] as const;

/** 白名单数量：12。改动即破坏契约。 */
export const UI_COMPONENT_WHITELIST_COUNT = 12;

/** 本阶段（24h）已实现的 7 个组件。 */
export const IMPLEMENTED_COMPONENTS = [
  "plan_card",
  "clarify_question",
  "evidence_card",
  "evidence_table",
  "conclusion_card",
  "approval_card",
  "degradation_notice",
] as const;

/** 已实现组件数量：7。 */
export const IMPLEMENTED_COMPONENT_COUNT = 7;

/** 各组件必填 props（架构 §11.2，原样）。 */
export const REQUIRED_PROPS: Record<string, string[]> = {
  plan_card: ["tasks"],
  clarify_question: ["questions"],
  evidence_card: ["evidence_id", "source", "as_of", "unit", "caliber", "value"],
  evidence_table: ["evidence_ids"],
  conclusion_card: ["layer", "text", "confidence", "refs"],
  approval_card: ["approval_id", "type", "summary"],
  degradation_notice: ["scope", "reason", "impact"],
  metric_block: ["label", "value", "unit", "delta", "caliber"],
  series_chart: ["series", "x_field", "y_fields"],
  distribution_chart: ["categories", "values"],
  citation_list: ["citations"],
  trace_ref: ["span_id"],
};

/** 单次 Run 的 UI 区块预算上限。 */
export const DEFAULT_UI_BLOCK_MAX = 200;

/** 降级组件名：渲染层统一用它兜底。 */
export const FALLBACK_COMPONENT = "FallbackJsonCard";

/** 动态 UI 块（与 lib/sse.ts 的 UiBlockPayload 形状一致）。 */
export type UiBlock = {
  component: string;
  props?: Record<string, unknown>;
  refs?: unknown[];
  render_status?: string;
};

export interface ValidateResult {
  ok: boolean;
  reason?: string;
  component: string;
  render_status: string;
}

const CONCLUSION_LAYERS = ["FACT", "INFERENCE", "UNCERTAIN"];
const IMPACT_LEVELS = ["low", "medium", "high"];

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** refs 可挂在 block.refs（契约位置）或 props.refs（组件入参）。 */
function resolveRefs(block: UiBlock): unknown {
  if (Array.isArray(block.refs)) return block.refs;
  if (isRecord(block.props) && Array.isArray(block.props.refs)) return block.props.refs;
  return undefined;
}

/** ⑤ 形状/类型检查（仅严格模式启用）。 */
function shapeReason(component: string, props: Record<string, unknown>, refs: unknown): string | null {
  switch (component) {
    case "plan_card":
      if (!Array.isArray(props.tasks) || props.tasks.length === 0) return "plan_card.tasks 必须是非空数组";
      break;
    case "clarify_question":
      if (!Array.isArray(props.questions) || props.questions.length === 0)
        return "clarify_question.questions 必须是非空数组";
      break;
    case "evidence_table":
      if (!Array.isArray(props.evidence_ids) || props.evidence_ids.length === 0)
        return "evidence_table.evidence_ids 必须是非空数组";
      break;
    case "conclusion_card":
      if (!CONCLUSION_LAYERS.includes(String(props.layer)))
        return `conclusion_card.layer 取值非法: ${String(props.layer)}`;
      if (refs !== undefined && !Array.isArray(refs)) return "conclusion_card.refs 必须是数组";
      break;
    case "degradation_notice":
      if (!IMPACT_LEVELS.includes(String(props.impact)))
        return `degradation_notice.impact 取值非法: ${String(props.impact)}`;
      break;
    default:
      break;
  }
  return null;
}

/**
 * 校验一个动态 UI 块是否可渲染。
 * 校验顺序：① 白名单 → ② 已实现 → ③ 必填 props → ④ FACT 非空 refs → ⑤ 形状/类型。
 * strict=false 时仅 ①②④ 导致降级，③⑤ 仅记录（仍渲染）。
 *
 * ③ 的缺字段口径与后端 `ui_renderer.validate()` 对齐：**只有 `undefined` 算缺字段**。
 * `null` 表示「字段存在但无值」（热度榜 / 新闻类工具天然无单位，`unit = null` 合法），
 * 显示层自行渲染为「—」，不得因此把整张卡降级。
 */
export function validateBlock(
  block: UiBlock,
  opts: { strict?: boolean; maxBlocks?: number } = {},
): ValidateResult {
  const strict = opts.strict ?? true;
  const component = block && typeof block.component === "string" ? block.component : "";

  const degraded = (reason: string): ValidateResult => ({
    ok: false,
    reason,
    component: FALLBACK_COMPONENT,
    render_status: "failed",
  });

  // ① 组件名 ∈ 白名单？
  if (!(UI_COMPONENT_WHITELIST as readonly string[]).includes(component)) {
    return degraded(`组件 "${component}" 不在白名单内（白名单 ${UI_COMPONENT_WHITELIST_COUNT} 个）`);
  }

  // ② 组件 ∈ 已实现集？
  if (!(IMPLEMENTED_COMPONENTS as readonly string[]).includes(component)) {
    return degraded(`组件 "${component}" 尚未实现，降级渲染`);
  }

  const props: Record<string, unknown> = isRecord(block.props) ? block.props : {};
  const refs = resolveRefs(block);

  // ③ 必填 props 齐全？（仅严格模式导致降级）
  if (strict) {
    const required = REQUIRED_PROPS[component] ?? [];
    for (const key of required) {
      const value = key === "refs" ? refs : props[key];
      if (value === undefined) {
        return degraded(`组件 "${component}" 缺少必填 prop: ${key}`);
      }
    }
  }

  // ④ conclusion_card 的 FACT 必须有非空 refs
  if (component === "conclusion_card" && String(props.layer) === "FACT") {
    if (!Array.isArray(refs) || refs.length === 0) {
      return degraded("conclusion_card 为 FACT 时必须提供非空 refs");
    }
  }

  // ⑤ 形状 / 类型（仅严格模式导致降级）
  if (strict) {
    const shape = shapeReason(component, props, refs);
    if (shape) return degraded(shape);
  }

  return { ok: true, component, render_status: "ok" };
}

/** 按序截断区块列表到 max（默认 200）。 */
export function applyBlockBudget(blocks: UiBlock[], max: number = DEFAULT_UI_BLOCK_MAX): UiBlock[] {
  if (!Array.isArray(blocks)) return [];
  const limit =
    typeof max === "number" && Number.isFinite(max) && max >= 0 ? Math.floor(max) : DEFAULT_UI_BLOCK_MAX;
  return blocks.slice(0, limit);
}

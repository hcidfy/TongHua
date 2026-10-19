/**
 * 展示层可读化工具（`docs/解决文档.md` §5.4 / §5.6）。
 *
 * 目标：把后端内部结构（`evidence_id` / 原始字段名 / 对象型 `value` / 长 JSON）
 * 转成投资研究者能直接读的内容——**不再 `String(obj)` 出 `[object Object]`**。
 * 纯 TS、无 JSX，服务端组件与客户端组件都可 import。
 */

export const PH = "—";

export function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
}

export function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** 标量 → 字符串；对象/数组 → 紧凑 JSON（截断）；空值 → 「—」。 */
export function text(v: unknown, max = 160): string {
  if (v === undefined || v === null || v === "") return PH;
  if (typeof v === "string") return v.length > max ? `${v.slice(0, max)}…` : v;
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : PH;
  if (typeof v === "boolean") return v ? "是" : "否";
  try {
    const s = JSON.stringify(v);
    if (!s || s === "{}" || s === "[]") return PH;
    return s.length > max ? `${s.slice(0, max)}…` : s;
  } catch {
    return PH;
  }
}

export function isUrl(v: unknown): v is string {
  return typeof v === "string" && /^https?:\/\//i.test(v);
}

/** `as_of`（毫秒时间戳 / 可解析字符串）→ 本地时间；非时间原样返回。 */
export function formatAsOf(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) {
    const d = new Date(v);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString();
  }
  if (typeof v === "string" && v !== "") {
    const n = Number(v);
    if (Number.isFinite(n) && Math.abs(n) > 1e11) {
      const d = new Date(n);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    return v;
  }
  return PH;
}

/** 数值 + 单位：`unit` 为空（`null`/`""`）时只给数值，不显示占位符。 */
export function withUnit(v: unknown, unit: unknown): string {
  const s = text(v, 60);
  const u = unit === undefined || unit === null || unit === "" ? PH : text(unit, 12);
  if (s === PH) return PH;
  return u === PH || s.includes(u) ? s : `${s} ${u}`;
}

/** 已知字段名 → 中文标签；未知字段原样显示（不猜、不编造）。 */
const FIELD_LABELS: Record<string, string> = {
  // 标的 / 行情
  thscode: "证券代码",
  thscodes: "证券代码",
  ticker: "代码",
  name: "名称",
  title: "标题",
  exchange: "交易所",
  asset_type: "资产类型",
  currency: "币种",
  list_date: "上市日",
  last_price: "最新价",
  price_change: "涨跌额",
  price_change_ratio_pct: "涨跌幅(%)",
  open_price: "开盘价",
  high_price: "最高价",
  low_price: "最低价",
  prev_price: "昨收",
  close_price: "收盘价",
  volume: "成交量",
  turnover: "成交额",
  date_ms: "日期",
  // 估值
  pe_ttm: "PE(TTM)",
  pe_mrq: "PE(MRQ)",
  pb_mrq: "PB(MRQ)",
  ps_ttm: "PS(TTM)",
  pcf_ttm: "PCF(TTM)",
  // 热度 / 涨跌停
  rank: "排名",
  heat: "热度",
  rank_change: "排名变化",
  rank_trend: "排名趋势",
  is_st: "ST",
  is_new: "新股",
  limit_up_time: "首次封板时间",
  limit_up_reason: "涨停原因",
  continue_day_text: "连板",
  continue_day_cnt: "连板数",
  seal_money: "封单额",
  max_seal_money: "最大封单额",
  // 利润表
  period: "报告期",
  fiscal_year: "年度",
  fiscal_period: "期间",
  report_date_ms: "披露日",
  period_end_ms: "期末日",
  operating_income: "营业收入",
  operating_costs: "营业成本",
  operating_expenses: "营业费用",
  sales_fee: "销售费用",
  manage_fee: "管理费用",
  research_and_development_expenses: "研发费用",
  operating_profit: "营业利润",
  interest_expenses: "利息费用",
  profit_total: "利润总额",
  income_tax_expense: "所得税费用",
  net_profit: "净利润",
  parent_holder_net_profit: "归母净利润",
  basic_eps: "每股收益",
  // 检索
  q: "检索词",
  limit: "条数上限",
  kind: "类型",
  tab_id: "分类",
  total: "总条数",
};

export function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key;
}

/** 响应体里的条目数组（扶摇信封统一为 `item` / `items`）。 */
export function itemsOf(value: unknown): Array<Record<string, unknown>> {
  const rec = asRecord(value);
  for (const key of ["item", "items", "list"]) {
    const arr = asArray(rec[key]);
    if (arr.length > 0) return arr.map(asRecord);
  }
  return [];
}

function firstScalar(candidates: unknown[]): string | null {
  for (const c of candidates) {
    if ((typeof c === "string" || typeof c === "number") && String(c) !== "") return String(c);
    if (Array.isArray(c) && c.length > 0) return c.map((x) => String(x)).join("、");
  }
  return null;
}

/**
 * 证据的一行可读摘要：优先「条目标题 / 证券名称与代码」，其次检索词，
 * 最后回落口径 / 来源。**不再直接暴露 `evidence_id`**（解决文档 §5.4.1）。
 */
export function evidenceHeadline(ev: Record<string, unknown>): string {
  const value = ev.value;
  const rec = asRecord(value);
  const items = itemsOf(value);
  const first = items[0] ?? {};

  const head =
    firstScalar([
      first.title,
      first.name,
      rec.title,
      rec.name,
      first.thscode,
      rec.thscode,
      rec.thscodes,
      rec.q,
      rec.query,
      rec.keyword,
    ]) ?? (text(ev.caliber, 40) !== PH ? String(ev.caliber) : text(ev.source, 40));

  return items.length > 1 ? `${head} 等 ${items.length} 条` : head;
}

export interface ValueRow {
  key: string;
  label: string;
  value: unknown;
}

/**
 * 把 `value` 展平成可读字段行：取首条记录（`item[0]`）的标量字段。
 * 无条目时退化为对象自身的标量字段；都没有则给一行紧凑 JSON。
 */
export function valueRows(value: unknown, limit = 8): ValueRow[] {
  if (value === null || typeof value !== "object") return [{ key: "value", label: "数值", value }];
  const items = itemsOf(value);
  const source = items.length > 0 ? items[0] : asRecord(value);
  const rows: ValueRow[] = [];
  for (const [key, v] of Object.entries(source)) {
    if (v !== null && typeof v === "object") continue; // 嵌套对象不进表格
    rows.push({ key, label: fieldLabel(key), value: v });
    if (rows.length >= limit) break;
  }
  if (rows.length === 0) rows.push({ key: "value", label: "内容", value: text(value, 400) });
  return rows;
}

/** 引用条目 → 可读文本（`{text}` / `{title}` / 字符串）。 */
export function citationText(c: unknown): string {
  if (typeof c === "string") return c;
  const rec = asRecord(c);
  for (const key of ["title", "text", "name", "summary", "caliber", "label"]) {
    const v = rec[key];
    if (typeof v === "string" && v !== "") return v;
  }
  return text(c, 160);
}

/** 引用条目 → 可点击链接（`raw_ref` / `url` 为 http(s) 时）。 */
export function citationUrl(c: unknown): string | null {
  const rec = asRecord(c);
  for (const key of ["url", "link", "href", "raw_ref"]) {
    if (isUrl(rec[key])) return rec[key] as string;
  }
  return isUrl(c) ? c : null;
}

/** Task 状态 → 中文标签（`TaskStatus`，架构 §5.2）。未知取原值，不编造。 */
const TASK_STATUS_LABELS: Record<string, string> = {
  PENDING: "待执行",
  READY: "待执行",
  RUNNING: "执行中",
  DONE: "已完成",
  FAILED: "失败",
  DEGRADED: "降级完成",
  SKIPPED: "已跳过",
};

export function taskStatusLabel(status: unknown): string {
  if (typeof status !== "string" || status === "") return TASK_STATUS_LABELS.PENDING;
  return TASK_STATUS_LABELS[status] ?? status;
}

export interface PlanToolChain {
  tool: string;
  /** 该工具产出证据的条数（来自 REST 证据快照）。 */
  evidenceCount: number;
  /** 结果摘要：首条证据的可读标题 / 标的。 */
  result: string;
}

export interface PlanTaskChain {
  status: string;
  tools: PlanToolChain[];
}

/**
 * 把「计划任务 × 执行快照」拼成 **任务 → 工具 → 结果** 链路（解决文档 §5.5）。
 *
 * 三个数据源（都不新增后端接口）：
 * - `planTasks`：`plan_card.props.tasks`（`seq / tools`，计划期已知）
 * - `tasks`：`GET /api/runs/{id}` 的 `tasks[]`（`task_id / seq / status`）
 * - `evidences`：`GET /api/runs/{id}/evidences`（`task_id / interface / value`）
 *
 * 返回 `seq → PlanTaskChain`。任一数据源缺失时按「— / 未取到数据」降级，不编造。
 */
export function planChains(
  planTasks: Array<Record<string, unknown>>,
  tasks: Array<Record<string, unknown>>,
  evidences: Array<Record<string, unknown>>,
  toolCalls: Array<Record<string, unknown>> = [],
): Record<string, PlanTaskChain> {
  const bySeq = new Map<string, Record<string, unknown>>();
  for (const row of tasks) {
    const seq = row.seq;
    if (seq !== undefined && seq !== null) bySeq.set(String(seq), row);
  }

  // task_id → 证据；interface 是上游路径，真正的工具名由 tool_call_id 关联提供。
  const byTask = new Map<string, Array<Record<string, unknown>>>();
  for (const ev of evidences) {
    const taskId = typeof ev.task_id === "string" ? ev.task_id : "";
    if (!taskId) continue;
    const bucket = byTask.get(taskId) ?? [];
    bucket.push(ev);
    byTask.set(taskId, bucket);
  }
  const byCall = new Map<string, Record<string, unknown>[]>();
  for (const call of toolCalls) {
    const taskId = String(call.task_id ?? "");
    if (!taskId) continue;
    byCall.set(taskId, [...(byCall.get(taskId) ?? []), call]);
  }

  const out: Record<string, PlanTaskChain> = {};
  for (const planTask of planTasks) {
    const seq = String(planTask.seq ?? "");
    if (seq === "") continue;
    const row = bySeq.get(seq);
    const taskId = typeof row?.task_id === "string" ? row.task_id : "";
    const bucket = taskId ? byTask.get(taskId) ?? [] : [];

    const counts = new Map<string, number>();
    const headline = new Map<string, string>();
    for (const ev of bucket) {
      const tool = typeof ev.tool_name === "string" && ev.tool_name !== "" ? ev.tool_name : PH;
      counts.set(tool, (counts.get(tool) ?? 0) + 1);
      if (!headline.has(tool)) {
        const h = evidenceHeadline(ev);
        if (h !== PH) headline.set(tool, h);
      }
    }

    const declared = asArray(planTask.tools).map((t) => String(t));
    const calls = byCall.get(taskId) ?? [];
    const names = [...new Set([...declared, ...calls.map((c) => String(c.tool_name)), ...counts.keys()])];
    out[seq] = {
      status: row ? taskStatusLabel(row.status) : taskStatusLabel(undefined),
      tools: names.map((tool) => {
        const count = counts.get(tool) ?? 0;
        const attempts = calls.filter((c) => c.tool_name === tool);
        const last = attempts.at(-1);
        let result = "尚未执行";
        if (count > 0) {
          result = headline.get(tool) ?? `取得 ${count} 条证据`;
          if (bucket.some((ev) => ev.tool_name === tool && ev.degrade_reason)) result += "（数据已降级）";
        } else if (last) {
          result = last.status === "FAILED"
            ? `调用失败：${text(last.error_message, 80)}`
            : "已调用，但没有可用证据";
        } else if (row?.status === "DONE") {
          result = "计划使用，实际未调用";
        }
        return {
          tool,
          evidenceCount: count,
          result,
        };
      }),
    };
  }
  return out;
}

/** 三层结论条目（事实 / 推断 / 不确定）→ `{text, refs, condition, confidence}`。 */
export function conclusionParts(item: unknown): {
  text: string;
  refs: string[];
  condition: string | null;
  confidence: string | null;
} {
  const rec = asRecord(item);
  const raw = rec.text ?? rec.statement ?? item;
  const refs = asArray(rec.refs ?? rec.evidence_ids).map((r) => String(r));
  const condition = typeof rec.condition === "string" && rec.condition !== "" ? rec.condition : null;
  const confidence =
    typeof rec.confidence === "number"
      ? rec.confidence.toFixed(2)
      : typeof rec.confidence === "string" && rec.confidence !== ""
        ? rec.confidence
        : null;
  return { text: text(raw, 400), refs, condition, confidence };
}

export interface ResearchLine {
  text: string;
  refs: string[];
}

/** 只从可回查的利润表证据提取文字，不把公司总收入误称为业务分部收入。 */
export function financialResearch(evidences: Array<Record<string, unknown>>, goal = ""): {
  facts: ResearchLine[];
  limitations: string[];
} {
  const facts: ResearchLine[] = [];
  const limitations: string[] = [];
  const financials = evidences.filter((ev) =>
    ev.tool_name === "get_financials" || String(ev.interface ?? "").includes("/financials/income-statements")
  );
  const targetCodes = new Set<string>();
  for (const ev of evidences) {
    if (ev.tool_name !== "search_ticker" && !String(ev.interface ?? "").includes("/meta/tickers/search")) continue;
    for (const row of itemsOf(ev.value)) {
      if (typeof row.name === "string" && row.name.length > 1
        && (goal.includes(row.name) || goal.includes(row.name.slice(-2)))
        && typeof row.thscode === "string") {
        targetCodes.add(row.thscode);
      }
    }
  }
  const yearsByIssuer = new Map<string, Set<string>>();
  const seen = new Set<string>();
  for (const ev of financials) {
    const id = typeof ev.evidence_id === "string" ? ev.evidence_id : "";
    if (!id) continue;
    const rows = itemsOf(ev.value);
    for (const row of rows) {
      if (row.period !== "annual" || row.fiscal_year === null || row.fiscal_year === undefined) continue;
      const year = String(row.fiscal_year);
      const issuer = String(row.thscode ?? "");
      if (targetCodes.size && !targetCodes.has(issuer)) continue;
      const key = `${issuer}:${year}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const values = [
        ["营业收入", row.operating_income],
        ["营业成本", row.operating_costs],
        ["销售费用", row.sales_fee],
        ["管理费用", row.manage_fee],
        ["净利润", row.net_profit],
      ].filter((entry) => typeof entry[1] === "number")
        .map(([label, value]) => `${label} ${Number(value).toLocaleString("zh-CN")} ${text(ev.unit, 12)}`);
      if (values.length) {
        yearsByIssuer.set(issuer, new Set([...(yearsByIssuer.get(issuer) ?? []), year]));
        facts.push({
          text: `${year} 年 ${text(issuer, 24)}（年度利润表）：${values.join("；")}。来源 ${text(ev.source, 24)}，口径 ${text(ev.caliber, 100)}，数据时点 ${formatAsOf(ev.as_of)}。`,
          refs: [id],
        });
        if (typeof row.report_date_ms === "number" && typeof ev.as_of === "number" && row.report_date_ms > ev.as_of) {
          limitations.push(`${issuer} ${year} 年报披露日 ${formatAsOf(row.report_date_ms)} 晚于该证据时点 ${formatAsOf(ev.as_of)}，时间口径冲突，不能按该时点认定已披露。`);
        }
      }
    }
  }
  facts.sort((a, b) => a.text.localeCompare(b.text, "zh-CN"));
  if (!facts.length) limitations.push("未取得可核实的年度利润表数值，无法形成收入变化结论。");
  for (const [issuer, years] of yearsByIssuer) {
    if (years.size < 3) limitations.push(`${issuer} 仅覆盖 ${[...years].sort().join("、")} 年，少于近三年要求；不可据此概括三年变化。`);
  }
  if (yearsByIssuer.size > 1 && targetCodes.size === 0) limitations.push("财报返回多家公司；研究目标未能唯一锁定标的，不能将不同公司合并为同一时间序列。");
  limitations.push("利润表总收入不等于产品/业务分部收入；缺少分部披露时，不能判断收入架构（各业务占比）变化。");
  if (financials.some((ev) => ev.source === "mock")) limitations.push("所引利润表含 Mock 演示数据，不应作为真实投资研究结论。");
  if (financials.some((ev) => ev.degrade_reason)) limitations.push("财务证据存在降级记录，需核查原始来源后再使用。");
  return { facts, limitations };
}

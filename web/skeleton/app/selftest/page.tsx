import { createElement, type Attributes, type ComponentType, type ElementType } from "react";
import {
  applyBlockBudget,
  IMPLEMENTED_COMPONENTS,
  validateBlock,
  type UiBlock,
} from "@/lib/ui-contract";
import PlanCard from "@/components/blocks/PlanCard";
import ClarifyQuestion from "@/components/blocks/ClarifyQuestion";
import EvidenceCard from "@/components/blocks/EvidenceCard";
import EvidenceTable from "@/components/blocks/EvidenceTable";
import ConclusionCard from "@/components/blocks/ConclusionCard";
import ApprovalCard from "@/components/blocks/ApprovalCard";
import DegradationNotice from "@/components/blocks/DegradationNotice";
import FallbackJsonCard from "@/components/blocks/FallbackJsonCard";
import { financialResearch, planChains } from "@/lib/format";
import { fallbackApiBase, resolveApiBase } from "@/lib/sse";

type RenderToStaticMarkup = typeof import("react-dom/server").renderToStaticMarkup;

interface Assertion {
  name: string;
  pass: boolean;
  detail: string;
}

function must(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

/**
 * 取 `react-dom/server` 的 renderToStaticMarkup。
 *
 * Next 在 RSC 层会把 `react-dom/server` 静态导入判定为编译期错误
 * （"You're importing a component that imports react-dom/server"），
 * 因此这里改用运行时动态引入（webpack 不打包该请求），
 * 由 Node 以 `node` 条件解析到 react-dom 的 server.node 构建，
 * 从而在构建期（预渲染）真正执行组件级渲染断言。
 */
async function loadRenderToStaticMarkup(): Promise<RenderToStaticMarkup> {
  const mod = (await import(
    /* webpackIgnore: true */ "react-dom/server"
  )) as typeof import("react-dom/server");
  return mod.renderToStaticMarkup;
}

/** 在构建期（预渲染）执行渲染，任何抛错都会让 next build 失败。 */
function renderComp(rtsm: RenderToStaticMarkup, comp: ElementType, props: Record<string, unknown>): string {
  const element = createElement(
    comp as ComponentType<Record<string, unknown>>,
    props as Attributes & Record<string, unknown>,
  );
  return rtsm(element);
}

/** 合法 props（供断言 1/6 使用）。 */
const LEGAL_PROPS: Record<string, Record<string, unknown>> = {
  plan_card: { tasks: [{ seq: 1, title: "分析收入结构", intent: "拆解收入来源", deps: [], tools: ["financial"] }] },
  clarify_question: { questions: ["研究的时间范围是？", "口径是否合并？"] },
  evidence_card: {
    evidence_id: "ev-1",
    source: "2024 年年度报告",
    as_of: 1704067200000,
    unit: "亿元",
    caliber: "合并口径",
    value: 123.4,
  },
  evidence_table: { evidence_ids: ["ev-1", "ev-2"] },
  conclusion_card: { layer: "FACT", text: "收入同比增长 12%", confidence: 0.92, refs: ["ev-1"] },
  approval_card: { approval_id: "ap-1", type: "PLAN_APPROVAL", summary: "确认研究计划" },
  degradation_notice: { scope: "series_chart", reason: "该组件尚未实现", impact: "medium" },
};

/** 断言 1 的渲染用例：组件 + 期望关键字。 */
const RENDER_CASES: Array<{
  name: string;
  comp: ElementType;
  props: Record<string, unknown>;
  keyword: string;
}> = [
  { name: "plan_card", comp: PlanCard, props: LEGAL_PROPS.plan_card, keyword: "分析收入结构" },
  { name: "clarify_question", comp: ClarifyQuestion, props: LEGAL_PROPS.clarify_question, keyword: "研究的时间范围是？" },
  { name: "evidence_card", comp: EvidenceCard, props: LEGAL_PROPS.evidence_card, keyword: "2024 年年度报告" },
  { name: "evidence_table", comp: EvidenceTable, props: LEGAL_PROPS.evidence_table, keyword: "编号只用于定位" },
  { name: "conclusion_card", comp: ConclusionCard, props: LEGAL_PROPS.conclusion_card, keyword: "[事实]" },
  { name: "approval_card", comp: ApprovalCard, props: LEGAL_PROPS.approval_card, keyword: "确认研究计划" },
  { name: "degradation_notice", comp: DegradationNotice, props: LEGAL_PROPS.degradation_notice, keyword: "该组件尚未实现" },
];

/** 断言 6 的合法 block（refs 挂在契约位置 block.refs）。 */
function legalBlock(component: string): UiBlock {
  if (component === "conclusion_card") {
    return {
      component,
      props: { layer: "FACT", text: "收入同比增长 12%", confidence: 0.92 },
      refs: ["ev-1"],
    };
  }
  return { component, props: LEGAL_PROPS[component] };
}

function runSelfTests(rtsm: RenderToStaticMarkup): Assertion[] {
  const assertions: Assertion[] = [];

  const record = (name: string, fn: () => string) => {
    try {
      assertions.push({ name, pass: true, detail: fn() });
    } catch (e) {
      assertions.push({ name, pass: false, detail: e instanceof Error ? e.message : String(e) });
    }
  };

  record("计划链：按工具名而非接口路径归组", () => {
    const chain = planChains(
      [{ seq: 1, tools: ["search_ticker", "get_index_constituents"] }],
      [{ seq: 1, task_id: "t1", status: "DONE" }],
      [{ task_id: "t1", tool_name: "search_ticker", interface: "/api/meta/tickers/search", value: { item: [{ name: "贵州茅台" }] } }],
      [{ task_id: "t1", tool_name: "search_ticker", status: "OK" }],
    );
    must(chain["1"].tools[0].result.includes("贵州茅台"), "已取得的证据未映射至工具");
    must(chain["1"].tools[1].result === "计划使用，实际未调用", "未调用不应显示未取到数据");
    return "实际工具调用与计划工具正确区分";
  });
  record("研究文本：Mock 两年记录明示缺口", () => {
    const report = financialResearch([{
      evidence_id: "ev-fin", tool_name: "get_financials", source: "mock",
      interface: "/api/a-share/financials/income-statements", unit: "CNY",
      value: { item: [
        { period: "annual", fiscal_year: 2024, operating_income: 100 },
        { period: "annual", fiscal_year: 2025, operating_income: 110 },
      ] },
    }]);
    must(report.facts.length === 2 && report.facts.every((f) => f.refs[0] === "ev-fin"), "年度数据未正确引用");
    must(report.limitations.some((line) => line.includes("少于近三年")), "缺失年份未披露");
    must(report.limitations.some((line) => line.includes("Mock")), "模拟数据未披露");
    must(financialResearch([]).facts.length === 0, "空数据不得生成数值结论");
    const scoped = financialResearch([
      { tool_name: "search_ticker", value: { item: [
        { name: "贵州茅台", thscode: "600519.SH" }, { name: "北方华创", thscode: "002371.SZ" },
      ] } },
      { evidence_id: "ev2", tool_name: "get_financials", source: "mock", as_of: 1704067200000,
        value: { item: [
          { period: "annual", fiscal_year: 2025, thscode: "600519.SH", operating_income: 100, report_date_ms: 1800000000000 },
          { period: "annual", fiscal_year: 2025, thscode: "002371.SZ", operating_income: 200 },
        ] } },
    ], "分析茅台近三年收入架构");
    must(scoped.facts.length === 1 && scoped.facts[0].text.includes("600519.SH"), "混入了其他公司数据");
    must(scoped.limitations.some((line) => line.includes("时间口径冲突")), "披露时点冲突未披露");
    return "年度、来源与证据索引可回查";
  });
  record("结论卡不裸露证据 ID", () => {
    const html = renderComp(rtsm, ConclusionCard, {
      layer: "FACT", text: "有据可查", refs: ["fccf952c1cab4d84995ad9bcbd555c62"],
      citation_labels: ["证据 1（贵州茅台）"],
    });
    must(html.includes("证据 1") && !html.includes("fccf952c1cab4d84995ad9bcbd555c62"), "引用编号泄露");
    return "显示可读编号与标题";
  });
  record("API 基址：坏的环境值回退到当前主机 8000", () => {
    must(
      fallbackApiBase({ protocol: "http:", hostname: "8.136.108.32" }) === "http://8.136.108.32:8000",
      "坏的环境值未回退到当前主机",
    );
    must(resolveApiBase("http://127.0.0.1:8000/") === "http://127.0.0.1:8000", "合法基址不应被改写");
    return "坏的 API 基址会自动回退到当前主机";
  });

  // 断言 1：7 个已实现组件用合法 props 渲染成功 + 关键字
  record("断言1：7 个已实现组件合法 props 渲染成功", () => {
    const ok: string[] = [];
    for (const c of RENDER_CASES) {
      const html = renderComp(rtsm, c.comp, c.props);
      must(html.length > 0, `${c.name} 渲染结果为空`);
      must(html.includes(c.keyword), `${c.name} 渲染 HTML 未包含关键字「${c.keyword}」`);
      ok.push(c.name);
    }
    return `7/7 渲染通过：${ok.join("、")}`;
  });

  // 断言 2：evidence_card 缺必填 props → 降级
  record("断言2：evidence_card 非法 props（{}）降级为 FallbackJsonCard", () => {
    const bad: UiBlock = { component: "evidence_card", props: {} };
    const r = validateBlock(bad);
    must(r.ok === false, "evidence_card 缺必填仍被判为可渲染");
    must(r.render_status === "failed", `render_status 应为 failed，实际 ${r.render_status}`);
    must(r.component === "FallbackJsonCard", `应降级为 FallbackJsonCard，实际 ${r.component}`);
    const html = renderComp(rtsm, FallbackJsonCard, { block: bad, reason: r.reason });
    must(html.includes("降级"), "FallbackJsonCard HTML 未包含「降级」字样（疑似白屏）");
    return `ok=false, render_status=${r.render_status}, component=${r.component}, reason=${r.reason}`;
  });

  // 断言 3：未实现组件（metric_block）→ 降级
  record("断言3：未实现组件 metric_block 降级为 FallbackJsonCard", () => {
    const nb: UiBlock = { component: "metric_block", props: LEGAL_PROPS.degradation_notice };
    const r = validateBlock(nb);
    must(r.ok === false, "未实现组件不应被判为可渲染");
    must(r.render_status === "failed", `render_status 应为 failed，实际 ${r.render_status}`);
    must(r.component === "FallbackJsonCard", `应降级为 FallbackJsonCard，实际 ${r.component}`);
    const html = renderComp(rtsm, FallbackJsonCard, { block: nb, reason: r.reason });
    must(html.includes("降级"), "FallbackJsonCard HTML 未包含「降级」字样");
    return `ok=false, reason=${r.reason}`;
  });

  // 断言 4：白名单外组件（evil_card）→ 降级
  record("断言4：白名单外组件 evil_card 降级为 FallbackJsonCard", () => {
    const eb: UiBlock = { component: "evil_card", props: { anything: true } };
    const r = validateBlock(eb);
    must(r.ok === false, "白名单外组件不应被判为可渲染");
    must(r.render_status === "failed", `render_status 应为 failed，实际 ${r.render_status}`);
    must(r.component === "FallbackJsonCard", `应降级为 FallbackJsonCard，实际 ${r.component}`);
    const html = renderComp(rtsm, FallbackJsonCard, { block: eb, reason: r.reason });
    must(html.includes("降级"), "FallbackJsonCard HTML 未包含「降级」字样");
    return `ok=false, reason=${r.reason}`;
  });

  // 断言 5：conclusion_card FACT 且 refs 空 → 渲染层拦下
  record("断言5：conclusion_card(FACT) refs 为空被拦下", () => {
    const cb: UiBlock = {
      component: "conclusion_card",
      props: { layer: "FACT", text: "某事实", confidence: 0.9 },
      refs: [],
    };
    const r = validateBlock(cb);
    must(r.ok === false, "FACT 且 refs 为空不应被判为可渲染");
    must(r.render_status === "failed", `render_status 应为 failed，实际 ${r.render_status}`);
    must(r.component === "FallbackJsonCard", `应降级为 FallbackJsonCard，实际 ${r.component}`);
    return `ok=false, reason=${r.reason}`;
  });

  // 断言 6：全部 7 个已实现组件合法 props → ok:true
  record("断言6：全部 7 个已实现组件 validateBlock 返回 ok:true", () => {
    const ok: string[] = [];
    for (const name of IMPLEMENTED_COMPONENTS) {
      const r = validateBlock(legalBlock(name));
      must(r.ok === true, `${name} 合法 props 校验失败：${r.reason ?? "unknown"}`);
      must(r.render_status === "ok", `${name} render_status 应为 ok，实际 ${r.render_status}`);
      ok.push(name);
    }
    return `7/7 ok：${ok.join("、")}`;
  });

  // 断言 7：applyBlockBudget 截断
  record("断言7：applyBlockBudget(blocks, 3) 截断到 3 条", () => {
    const blocks: UiBlock[] = Array.from({ length: 5 }, (_, i) => ({
      component: "plan_card",
      props: { tasks: [{ seq: i + 1, title: `t${i + 1}`, intent: "", deps: [] }] },
    }));
    const sliced = applyBlockBudget(blocks, 3);
    must(sliced.length === 3, `应截断到 3 条，实际 ${sliced.length}`);
    must(applyBlockBudget(blocks).length === 5, "默认预算下不应截断 5 条");
    return `5 → ${sliced.length}（默认上限保留 ${applyBlockBudget(blocks).length}）`;
  });

  return assertions;
}

export default async function SelfTestPage() {
  const rtsm = await loadRenderToStaticMarkup();
  const assertions = runSelfTests(rtsm);
  const failed = assertions.filter((a) => !a.pass);

  // 任一断言失败 → throw，从而让 next build 失败
  if (failed.length > 0) {
    const lines = failed.map((a) => `  - ${a.name}: ${a.detail}`).join("\n");
    throw new Error(`前端自检失败 ${failed.length}/${assertions.length}：\n${lines}`);
  }

  return (
    <div className="card">
      <h1>前端自检（/selftest）</h1>
      <p className="muted">
        本页为服务端组件，在构建期（静态预渲染）执行组件级渲染断言；任一失败即 throw，使
        <code>next build</code> 失败。当前 {assertions.length} 条断言全部通过。
      </p>
      <ul className="selftest-list">
        {assertions.map((a, i) => (
          <li key={i} className={a.pass ? "pass" : "fail"}>
            <span className="badge">{a.pass ? "PASS" : "FAIL"}</span>
            <span className="name">{a.name}</span>
            <span className="muted detail">{a.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

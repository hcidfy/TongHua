# 投资 X Buddy｜AI 使用与验证记录 + 测试说明

> 单文件版，可直接作为题目必交物 **③ AI 使用与验证记录** 与 **④ 测试说明** 提交。
> 记录日期：**2026-10-20**（北京时间）。详细版见 [AI_USAGE_AND_VERIFICATION.md](AI_USAGE_AND_VERIFICATION.md)、[TEST_REPORT.md](TEST_REPORT.md)；实现对照见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

| 必交物 | 入口 |
|---|---|
| 可操作 Web 产品 | [http://8.136.108.32:3000/console](http://8.136.108.32:3000/console)（HTTP；请勿输入真实持仓/隐私/密钥） |
| 源代码仓库 + README | [github.com/hcidfy/TongHua](https://github.com/hcidfy/TongHua)，项目根 `InvestmentXBuddy/`；[项目 README](../README.md) |
| 提交材料索引 | [push/README.md](README.md) |
| 演示视频（可选） | 由候选人录制上传 |

---

## 第一部分：AI 使用与验证记录

### 1. 使用了哪些 AI 工具

| 工具 | 用途 | 验证方式 |
|---|---|---|
| **AI 编程助手**（本项目协作会话；具体客户端名称未留存，不臆称） | 读题拆解、架构与里程碑、编写 FastAPI/Next.js 代码与测试、读 Trace 排障、撰写交付文档 | 候选人逐项跑测试/构建、看真实接口、开页面核对；纠偏记录见 §3 |
| **DeepSeek 兼容模型**（运行时 LLM，产品能力） | 澄清、规划、推理、压缩、合规检查、综合结论 | `llm_calls`/`tokens_total`/Trace；云端实测 9 次调用、`tokens_total=5351`、`failed_calls=0` |
| **扶摇 API / iFinD MCP** | **不是 AI 工具**，是金融数据源：扶摇提供 A 股/指数行情、估值、利润表、热榜；iFinD 提供定向新闻/公告文本（低置信度） | 检查 `source`/`interface`/`as_of`/`unit`/`caliber`/`raw_ref` |

### 2. AI 参与了哪些环节

| 阶段 | AI 做了什么 | 候选人介入 |
|---|---|---|
| 读题与范围 | 拆解必交物与验收口径 | 确认 24h 取舍 |
| 架构与契约 | 提出分层架构、19 内核组件、13 实体，建议冻结 15 表 / 26 事件 / 10 类 SSE / 12 项 UI / 10 工具 | **决定不用 LangGraph**（要能指到机制代码行），确认契约后不再改 |
| 里程碑 M0–M6 | 地基 → 内核 → 数据与工具 → 状态恢复 → 记忆上下文 → 产物与动态 UI → 可观测合规，逐模块编码+测试 | 每里程碑验收通过才进下一个 |
| 真实数据 | 读文档、实现客户端、参数映射与质量元数据 | 提供凭据、核对字段口径、判定哪些数据能用 |
| 排障 | 读 Trace/日志定位根因、给修复 | 提出"这不可接受"的具体现象（见 §3） |
| 界面 | 重做证据/计划/结论文本 | 判断"像调试台还是像研究工作台" |
| 部署 | 给排障路径与修复代码 | 实际部署、重启、浏览器操作 |
| 文档 | 起草 README / AI 记录 / 测试说明 | 审阅事实、决定最终提交 |

### 3. 候选人（我）承担的工作与修正的问题

**候选人自己承担、AI 不能代劳的部分：**

1. **交付形态决策**：云厂商与主机、域名/端口、预算、对外访问方式；用 Mock 还是已授权真实数据；目标仓库与目录方案。
2. **仓库与安全**：决定提交哪些文件；审查来源与版权；确认 `.gitignore` 覆盖；协作中出现过 GitHub PAT，候选人**手动撤销并改用最小权限方式**，AI 全程未把任何令牌写进文档、脚本或命令。
3. **云端部署与凭据**：开通主机、配置受限环境变量（扶摇 Key / 模型 Key / iFinD 令牌 / `MOCK_DATA_MODE=false` / `NEXT_PUBLIC_API_BASE`）、起前后端、**重启以加载新代码**、处理端口与依赖。
4. **金融口径判断**（最关键）：利润表"营业收入总量" ≠ "产品/业务分部收入"；两年样本不能称"三年趋势"；晚于证据时点的披露不能倒推"当时已知"。这条判断决定了产品在"收入架构变化"目标上必须**承认能力缺口**。
5. **真实操作验收与提交**：在云端浏览器发起研究、看计划/工具/证据/产物、判断结论是否成立、判断合规文案是否越界、录制演示视频、确认仓库同步。

**候选人指出并推动修复的错误 / 不合理结果（10 条）：**

| # | 问题（为什么不可接受） | 处理 | 仍存边界 |
|---|---|---|---|
| 1 | 真实模型调用**每次 HTTP 400**；把模板降级说成"真实模型研究"会误导评委 | `json_schema → json_object → none` 逐级降档 + 按 `provider:model` 记忆成功档位 | 不保证所有兼容端点支持（`model_router.py`、`test_m1_router.py`） |
| 2 | 推理模型**只看到工具名，参数靠猜且全错**，任务"计划了"却取不到数 | 注入工具入参 schema、校验参数、错误回灌重试、离线受控兜底 | 禁止猜出不存在的证券代码 |
| 3 | 目标是"近三年收入架构变化"，**计划却去查行情/热榜**；两年 Mock 无法回答三年结构 | 补 `get_financials`（8→9 个工具）、规划改为财报优先、缺口写入不确定层 | 真实数据仍只有总量，缺三年分部附注 |
| 4 | **右侧产物全 0、证据卡被降级、引用只是内部 ID** | `artifact` 帧后回查完整产物；契约改为"仅 `undefined` 算缺字段，`null` 合法"；引用映射为证据序号+标题+原字段 | 自测 ≠ 完整浏览器 Run |
| 5 | **计划卡把 API 路径当工具名，把"未调用"写成"无数据"**，直接误导评委 | 证据接口补 `tool_name`/`tool_calls`；计划卡按"未调用/失败/已调用无证据/取到 N 条"四态显示 | — |
| 6 | **查询宁德时代却出现贵州茅台**，模型借用无关证据代码 | 有真实 Key 时缺省不启用 Mock；检索与财报按查询词/代码过滤；Agent 只认目标显式代码或唯一匹配 | 不得据其它公司生成目标结论 |
| 7 | **MCP 客户端装了 ≠ 业务在调用**（实际拿热榜）；首次真实调用缺 `time_start` | `get_news_flash` 增加 `query`：配置 MCP 时走 `search_news`/`search_notice`，按实时 `tools/list` 补必填参数；未配置即报缺口 | 一次完整 LLM Run 内完成 MCP 取证仍缺证据；云端当前 401 |
| 8 | **`cost_total=0` 被误判为"没调 LLM"**（候选人要求核对） | 新增 `cost_from_usage()` + 可配置单价（默认 0 = 不编造）；判据改为 `llm_calls`+`tokens_total`+`cost_total` 三者同时为正 | 云端未配单价，`cost_total=0` 只能用 tokens/llm_calls 佐证 |
| 9 | **云端首页占位、API 基址退化成 `http:` → 点"开始研究"直接 404** | 首页跳转 `/console`；基址三段式校验（显式参数/环境变量/回退当前主机 `:8000`），坏值不交给 `fetch()`；加构建期断言 | HTTP/跨域/认证仍是部署侧问题 |
| 10 | **产品像"Agent 调试台"不像研究工作台**：`mock / /api/... / 引用：884d3c...`、`[object Object]`、`raw_ref —` | 证据面板改为"研究引用面板"（标题/摘要、可点链接、结构化渲染、按年+指标+数值+单位）；新增"研究结论与核验说明"（逐年带代码/口径/单位/时点/来源/证据序号） | — |

另有三项**否决**：拒绝用重型框架替换自研 Harness；拒绝为演示放宽"缺数据必须披露"；拒绝把未计价模型描述成"零成本运行"。

### 4. 责任与诚实声明

- AI 负责把机制与代码写到**可运行、可测试**；候选人负责决定**什么才算对**。
- 不虚构"逐行手写"，不虚构未留存的评审签名/截图/回执。
- 三类证据互不替代：**自动化测试**、**组件构建自测（`/selftest`）**、**云端浏览器 E2E**。`COMPLETED` 不等于研究问题已回答。
- AI 的局限在本项目真实发生过：模型会猜错工具参数、会串用跨公司证据、会被结构化输出格式卡住、会给出合法但错误的代码——这些都是靠**校验层、目标绑定、合规守卫**兜住，而不是靠"相信模型"。

---

## 第二部分：测试说明

### 5. 本次执行结果（证据分层）

| 层次 | 检查 | 结果 |
|---|---|---|
| A | 后端用例**收集 199 条** | 本次沙箱内复跑 **196 passed + 3 error**；3 条为 `tmp_path` 临时目录被沙箱拒绝（`ERROR at setup: PermissionError [WinError 5]`），**非断言失败**；常规环境历史记录为 **199 passed** |
| A | 前端 `npm run build` | 本次沙箱内 `next build` 子进程被环境拒绝（`CreateFileMapping WinError 5`）；历史同日记录为**通过**，`/selftest` 构建期断言 11 条 |
| A | `GET /api/health` | `status=ok`、`env=prod`、`mock_data_mode=false`、`llm_configured=true`、契约 15 表 / 26 事件 / 10 SSE / 12 UI（实现 7） |
| A | `GET /api/tools` | `count=10`（9 数据 + 内部 `emit_artifact`），每个工具带 schema/权限/超时/成本估算 |
| A | `GET /api/threads` + `/api/runs/{id}` 及 `/evidences`、`/artifacts`、`/observability/*` | 5 条真实线程；最新 Run `COMPLETED`：9 次模型调用、`tokens_total=5351`、`cost_total=0`（未配单价）、6 任务（4 `DEGRADED`/2 `DONE`）、2 条证据、1 个四层产物、15 个 UI 块、9 项指标、合规 `PASS` |
| C | 云端浏览器完整 Run、审批、断源、重启 | 候选人报告已部署并通过操作测试；本次无可公开的脱敏录屏/run 日志 |

### 6. 三类场景覆盖

**① 主链路（目标 → 计划 → 工具 → 证据 → 产物 → UI）**

| 测试 | 期望合格线 | 自动化入口 |
|---|---|---|
| T01 规划与执行 | 任务拆解体现目标语义；`search_ticker` 命中目标代码；未命中即报缺口 | `test_m5_run_demo_end_to_end`、`test_m2_pipeline_autowires_the_ten_tools` |
| T02 证据与产物 | 来源/时点/单位/口径/原字段齐全；事实/推断/不确定/引用四层；**总量≠分部** | `test_financial_goal_preserves_tool_evidence_and_readable_fact`、`test_m5_artifact_builds_three_layers_with_condition_and_citations` |
| T03 SSE 与动态 UI | 事件来自已落库状态、可重放；`artifact` 帧后回查；非法组件降级不白屏 | `test_m5_sse_stream_replays_frozen_events`、`test_m5_invalid_props_degrade_instead_of_blank` |
| T04 检查点/恢复/记忆 | 已完成任务不重跑；审批挂起跨刷新存活；压缩后旧证据可解析；仅 LONG 层跨线程 | `test_m3_resume_skips_finished_tasks_without_repeat_tool_calls`、`test_m3_approval_suspend_survives_page_refresh`、`test_m4_long_session_compaction_keeps_every_evidence_resolvable` |

**② 数据缺失 / 模型与接口失败**

| 测试 | 期望 | 入口 / 实测 |
|---|---|---|
| T05 目标不覆盖/空值 | 空或"证据不足"，不借用其它公司代码、不补零 | **A**：云端京东 Run — `search_ticker` `[1003 asset_type]` 失败、4 个任务 `DEGRADED`、产物写"热股榜未出现京东相关标的，无法支撑收入架构分析" |
| T06 缺年/时点冲突 | 缺第三年、缺分部必须披露；晚于证据时点的披露不得倒推 | `test_financial_goal_preserves_tool_evidence_and_readable_fact`、`/selftest` |
| T07 断网/限流/业务错误/过期缓存 | 显示降级原因与来源；过期缓存必须标记 | `test_m2_offline_degrades_and_emits_notice`、`test_m2_rate_limited_degrades_at_step3`、`test_m2_stale_cache_fallback_is_labelled`、`test_m0_business_error_over_http_200_is_raised_by_code` |
| T08 模型/MCP 失效 | 模型降级不抛错；MCP 不可用报缺口，不拿热榜冒充公告 | `test_m1_router_unconfigured_degrades_without_raising`、`test_company_news_uses_ifind_query_instead_of_generic_hot_list`；**A**：云端 `get_news_flash` `[2003 HTTP 401]` |
| T09 前端坏基址 | `http:` 不得当合法后端，回退当前主机 `:8000` | `/selftest` 构建期断言 |

**③ 极端与合规边界**

| 测试 | 期望 | 入口 |
|---|---|---|
| T10 预算/死循环/无进展 | 命中规则即 `STOPPED`，不再调用工具；单价为 0 时费用闸门不可信 | `test_m4_step_level_max_stops_run_and_lands_events`、`test_m3_loop_detected_within_three_repeats_and_names_hit_rule`、`test_m3_budget_charges_and_reports_exhaustion_in_order` |
| T11 审批/非法迁移 | 挂起跨刷新；同意续跑、拒绝/超时停止；非法迁移被拒。**只有计划审批有触发点** | `test_m3_resume_after_approval_replans_and_finishes`、`test_m3_hitl_expires_pending_approval_after_timeout`、`test_m5_approval_reject_stops_run` |
| T12 无引用事实/非法 UI | FACT 无 refs 不生效；未知组件/缺 props 降级 | `test_m5_fact_conclusion_without_refs_is_blocked`、`test_m5_unknown_component_degrades` |
| T13 买卖建议/预测/隐私 | Guard 拦截并记录；Trace 脱敏 | `test_m6_pipeline_guard_rejects_advice_artifact`、`test_m6_guard_rejects_fact_without_refs`、`test_m6_tracer_persists_sanitized_and_numeric_attrs`；**A**：云端 `compliance verdict=PASS, strict=true, findings=[]` |

### 7. 评委复现步骤

1. **只读**：`Invoke-RestMethod http://8.136.108.32:8000/api/health`，确认 `mock_data_mode=false`、`llm_configured=true`。
2. **浏览器**：打开 `/console`，用**A 股**目标（`分析宁德时代近三年营业收入和利润变化`），核对任务实际工具状态 → 证据字段 → 产物四层。
3. **接口**：拿到 `run_id` 后查 `/api/runs/{id}/evidences`、`/artifacts`、`/api/observability/runs/{id}/{metrics,cost,compliance}`。
4. **本地**：`conda run -n TongHua --no-capture-output python -m pytest -q`；`npm --prefix web/skeleton run build`。
5. ⚠️ 不要用港股/美股目标（如"京东"）做主链路演示——主源不覆盖，会（正确地）得到降级结果；也不要用真实持仓/密钥。

### 8. 不可宣称的结论

1. "近三年收入架构（分部占比）变化"未被真实数据验证——只有利润表总量，缺三年分部附注。
2. 云端尚无"一次完整 LLM Run 内含 iFinD MCP 取证"的证据（当前 MCP 返回 401）。
3. `cost_total=0` 不代表零成本或未调用模型（未配单价）。
4. 公网为 HTTP、无鉴权/租户隔离/限流，非生产安全状态。
5. `/selftest`、健康探针、`COMPLETED` 都不能当作"研究结论正确"的证明。

### 9. 待候选人补齐（不预填、不伪造）

最终部署提交号、Run 日期与脱敏 `run_id`、浏览器跨源 POST/SSE 实测、重启后恢复实测、云端审批与 Guard 实测、演示视频链接。清单见 [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md)。

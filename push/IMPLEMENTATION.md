# 提交要求 1–3 与实现对照（监考官版）

> 依据题目《投资 X Buddy》提交要求 1–3 整理。原则：**一条要求 → 工作机制 → 代码位置 → 可自查证据（接口或测试名）**。凡是"已实现"指代码与自动化测试可核查；`COMPLETED` 或界面好看**不等于**金融结论正确，两者的区别在下文与[测试说明](TEST_REPORT.md)里反复标注。

契约总数（冻结，可被 `/api/health` 实时核对）：**15 表 / 26 检查点事件 / 10 类 SSE / 12 项 UI 白名单（已实现 7）/ 10 个工具（9 数据 + 1 内部）**。

---

## 1. 围绕核心任务完成可运行主链路

### 1.1 能力 / 工具注册

| 项 | 实现 |
|---|---|
| 声明 | [`app/tools/specs/blueprints.py`](../app/tools/specs/blueprints.py) 定义 10 个 `ToolBlueprint`：`search_ticker`、`get_price_snapshot`、`get_price_history`、`get_index_constituents`、`get_valuation`、`get_financials`、`get_hot_rank`、`get_limit_up_stats`、`get_news_flash` + 内部 `emit_artifact`。每个工具声明 **入参 JSON Schema、`permission_level`、`timeout_ms`、`est_cost`、`idempotent`、`domain`、`phase`**。 |
| 注册 | [`app/harness/tool_registry.py`](../app/harness/tool_registry.py) 统一注册与查询；`Pipeline` 缺省装配全部 10 个工具。 |
| 执行前校验 | [`app/harness/tool_executor.py`](../app/harness/tool_executor.py)：未注册 / 已禁用 / 参数不合 schema 一律拒绝执行并落失败记录，不会"静默跳过"当成成功。 |
| 执行层 | [`app/tools/impl/data_tools.py`](../app/tools/impl/data_tools.py) 负责参数映射、`keep_fields` 裁剪、`Evidence` 构造；[`app/data/quality.py`](../app/data/quality.py) 产出质量四件套（source / as_of / unit / caliber），[`cache.py`](../app/data/cache.py)、[`ratelimit.py`](../app/data/ratelimit.py)、[`breaker.py`](../app/data/breaker.py) 提供 TTL 缓存（含过期兜底）、令牌桶与熔断。 |
| 目标绑定 | 带 `thscode` 的财报/快照/估值查询只保留请求代码的记录；`search_ticker` 结果必须与目标名称/代码唯一匹配，否则显式降级，**不允许借用其它公司代码**。 |

**自查**：`GET /api/tools` → 本次云端实测 `count=10`，每个工具返回 `schema_json`、`permission_level`、`timeout_ms`、`est_cost`。用例：`tests/test_m1_tools.py`（12）、`tests/test_m2_tools.py`（24）。

### 1.2 计划执行

| 阶段 | 实现 |
|---|---|
| 管线 | [`app/harness/pipeline.py`](../app/harness/pipeline.py) 编排 **PreHook → AgentLoop → PostHook**；**4 个 PreHook**：`budget_check` / `guard_pre` / `plan` / `context_inject`；**5 个 PostHook**：`evidence_persist` / `guard_post` / `memory_write` / `cost_settle` / `trace_flush` |
| 规划 | [`app/harness/planner.py`](../app/harness/planner.py) 产出带依赖的任务 DAG（`tasks.deps`）；模型幻觉出的未注册工具被拒收；模型不可用时走保守模板并**显式标注模板计划**，不伪装成模型规划 |
| 推理执行 | [`app/harness/agent_loop.py`](../app/harness/agent_loop.py) 逐轮"推理 → 选工具 → 观察 → 决定下一步"；工具签名注入提示词，参数校验失败**回灌错误重试**，离线时走受控兜底 |
| 降级阶梯 | **工具执行 8 步阶梯**（[`tool_executor.py`](../app/harness/tool_executor.py)，顺序固定）：参数校验 → 幂等（`dedupe_key`）→ 限流（令牌桶 + 退避）→ 缓存（TTL 命中即返回并标记）→ 主源调用（重试 / 熔断）→ 备源（iFinD，若配置）→ 降级结果（可用缓存 + `degrade_reason`）→ 质量元数据注入。模型侧另有结构化输出 `json_schema → json_object → none` 逐档降级。两者都落 `degrade` 事件，前端可见原因 |
| 状态机 | [`app/harness/state_machine.py`](../app/harness/state_machine.py) 约束 Run/Task 合法迁移，非法迁移被拒并记事件；Run 终止态 `COMPLETED / STOPPED / FAILED` |

**自查**：`GET /api/runs/{id}` 返回 `tasks[]`（含 `status`、`attempts`、`evidence_count`、`degradation_reason`）与 `ui_block_count`；用例 `tests/test_m1_pipeline.py`、`test_m1_planner.py`、`test_m5_api.py::test_m5_run_demo_end_to_end`。

### 1.3 上下文压缩

- [`app/harness/context.py`](../app/harness/context.py) 按模型窗口预算组装上下文并判定压缩时机；[`app/harness/compactor.py`](../app/harness/compactor.py) 执行分层压缩：**系统约束不压、计划折叠为摘要、旧 Step 摘要化、Evidence 只保留引用**，最近步骤受保护。
- 关键取舍：**压缩的是"提示里的表示"，不是"库里的记录"**。证据原记录仍在 `evidences`，任意旧步骤的引用在压缩后仍可解析。
- 自查：`tests/test_m4_memory_context.py::test_m4_long_session_compaction_keeps_every_evidence_resolvable`。

### 1.4 记忆

| 层 | 存储 | 生命周期 |
|---|---|---|
| WORKING | 进程内存 | 单轮，不落库 |
| SESSION | 库（按 thread） | 线程内跨 Run |
| LONG | 库（跨 thread） | 新线程可召回历史偏好 |

[`app/harness/memory.py`](../app/harness/memory.py) 实现读写与召回（**关键词命中 + 时间衰减打分**，刻意不引向量库以保证可解释；`recall()` 接口保持稳定，未来可替换）。`hooks/post/memory_write.py` 在 Run 结束时按规则沉淀。

自查：`test_m4_memory_three_scopes_working_stays_in_memory`、`test_m4_new_thread_recalls_historical_preference`。

### 1.5 检查点与恢复

| 项 | 实现 |
|---|---|
| 事件 | [`app/harness/checkpoint.py`](../app/harness/checkpoint.py) 把每个状态变化写成 **26 类事件**之一并落 `checkpoint_events`，按序折叠可重放出 Run 状态 |
| 快照 | 派生快照在内存按需生成，**不是"唯一真相"**；真相是事件 + 实体表，重启后从库恢复 |
| 恢复 | [`app/harness/resume.py`](../app/harness/resume.py)：跳过 `DONE` 任务、靠 `dedupe_key` 避免重复工具调用、扫描孤儿 Run；[`app/harness/hitl.py`](../app/harness/hitl.py) 让审批挂起状态跨进程/Session 存活（页面刷新不丢） |
| 暂停/续跑 | `POST /api/runs/{id}/pause|resume|abort`；`RunPaused` / `RunResumed` 事件入流 |

自查：`test_m3_resume_skips_finished_tasks_without_repeat_tool_calls`、`test_m3_checkpoint_store_folds_snapshots_and_replays_state`、`test_m3_approval_suspend_survives_page_refresh`、`test_m5_sse_stream_replays_frozen_events`。

---

## 2. 权限、审批、停止规则、成本/时延与可观测性

### 2.1 权限与人工审批

| 项 | 实现 | 边界 |
|---|---|---|
| 工具权限 | 每个蓝图声明 `permission_level`，执行层校验后才允许调用 | **工具级权限 ≠ 用户级鉴权**：当前公网服务没有登录/角色/租户隔离 |
| 审批状态机 | [`app/harness/hitl.py`](../app/harness/hitl.py) 支持把 Run 挂起为 `AWAITING_APPROVAL`；[`app/api/approvals.py`](../app/api/approvals.py) 提供待办列表、详情、`decide`（同意/拒绝）；超时按状态机自动收口 | **只有 `plan`（计划审批）有触发点**；`expensive_tool`（成本闸门）与 `final_review`（最终审阅）两类在 `APPROVAL_TYPES` 中已定义、`HITLGate.request` 接口就绪，但管线未接线 |
| 同意/拒绝后果 | 同意 → 重新规划并续跑；拒绝/超时 → `STOPPED`；非法迁移被 `state_machine` 拒绝并记事件 | — |

自查：`GET /api/approvals`；用例 `test_m3_resume_after_approval_replans_and_finishes`、`test_m3_hitl_expires_pending_approval_after_timeout`、`test_m5_approval_reject_stops_run`、`test_m3_state_machine_rejects_illegal_transition_and_records_event`。

### 2.2 停止规则与预算

- [`app/harness/stop_rules.py`](../app/harness/stop_rules.py)：**8 条规则**——最大步数、token 上限、费用上限、墙钟时长、重复动作（循环检测）、无进展、用户终止、合规拦截；命中即写规则名与原因，Run 以 `STOPPED` 收口，不再继续调用工具。
- 同一组规则在 **Run 级**（Pipeline）与 **Step 级**（`AgentLoop._judge()` 逐轮）两处复用，避免"跑一步就烧掉整个预算"。
- [`app/harness/budget.py`](../app/harness/budget.py)：四维计量（steps / tokens / cost / wall-time）。**费用维只在配置了真实单价且供应商返回 usage 时有效**；单价默认 0 表示未计价，此时不能宣称费用闸门可靠。
- 自查：`test_m4_step_level_max_steps_stops_run_and_lands_events`、`test_m3_loop_detected_within_three_repeats_and_names_hit_rule`、`test_m3_budget_charges_and_reports_exhaustion_in_order`。

### 2.3 成本与时延

- [`app/harness/model_router.py`](../app/harness/model_router.py)：每次 LLM 调用记录 provider/model、prompt/completion tokens、耗时、状态；结构化输出按 `json_schema → json_object → none` 降档并按 `provider:model` 记忆成功档位。
- [`app/observ/cost.py`](../app/observ/cost.py)：成本只汇总 `status=OK` 的 `llm_calls`，**失败的调用不计费也不隐藏**；`cost_from_usage()` + 两个可配置单价项，默认 0 = 不编造单价。
- [`app/observ/tracer.py`](../app/observ/tracer.py)：父子 Span 记录计划、任务、工具、模型的耗时与失败，可查树。
- 自查：`GET /api/observability/runs/{id}/cost|spans`。云端实测：`tokens_total=5351`、`llm_calls=9`、`prompt_tokens=4071`、`completion_tokens=1280`、`failed_calls=0`、`cost_total=0`（未配单价，**不代表免费**）。

### 2.4 可观测性与合规

| 面 | 实现 | 自查 |
|---|---|---|
| Span / 成本 / 合规 / 指标 | [`app/api/traces.py`](../app/api/traces.py)：`/api/observability/runs/{id}/spans`、`/cost`、`/compliance`、`/metrics` | 云端实测 9 项指标；合规 `verdict=PASS`、`strict=true` |
| 合规守卫 | [`app/compliance/guard.py`](../app/compliance/guard.py)：**无引用的事实**、**无切换条件的推断**、**禁止性表述（确定性涨跌预测/收益承诺/直接买卖建议）** 三类拦截，结果写 `guard_findings` 并把产物判 `REJECT` | `test_m6_guard_rejects_fact_without_refs`、`test_m6_guard_rejects_inference_without_condition`、`test_m6_pipeline_guard_rejects_advice_artifact` |
| 脱敏 | [`app/observ/sanitize.py`](../app/observ/sanitize.py)：Trace 落库前对已知密钥形态与常见隐私字段（手机号/身份证/邮箱等）脱敏 | `test_m6_tracer_persists_sanitized_and_numeric_attrs` |

**注意**：脱敏只降低"日志泄露"风险，**不等于允许输入私人持仓**；公网入口禁止任何个人数据。

---

## 3. 产品结果如何映射到投资研究流程

### 3.1 五个对象 → 五个产品语义

| 用户看到的 | 技术实体 | 投资研究意义 |
|---|---|---|
| **线程** | `threads` + `runs` | 一个研究主题下的多次 Run；可暂停、可审批、可恢复，不是一次性问答 |
| **任务** | `tasks`（`seq` / `deps` / `status` / `degradation_reason`）+ `tool_calls`（`status` / `error_message`） | 研究子问题（定位标的 → 取数 → 交叉复核 → 出结论）。**实际调用、失败、未调用分开显示**：计划里有 ≠ 已经取到 |
| **证据** | `evidences`（`source` / `interface` / `as_of` / `fetched_at` / `staleness_ms` / `unit` / `caliber` / `raw_ref` / `confidence` / `degrade_reason` / `value`） | 一条可核验的原始记录：谁给的、什么时候、什么单位、什么口径、原文在哪。带 `thscode` 的记录只保留目标标的，避免跨公司污染 |
| **产物** | `artifacts`（`facts_json` / `inferences_json` / `uncertainties_json` / `citations_json`） | 研究结论的四层：**事实**（必须有引用）、**推断**（必须带切换条件）、**不确定**（缺口与假设）、**引用**（可回查）。利润表"总收入"绝不会被写成"分部收入结构" |
| **动态 UI** | `ui_blocks`（12 项白名单，已实现 7） | 研究过程的可视化载体；非法 props / 未知组件 → `FallbackJsonCard` 降级，不白屏；`artifact` SSE 帧只带 ID，前端回查完整产物 |

### 3.2 实时进展（SSE）

[`app/api/stream.py`](../app/api/stream.py) 冻结 **10 类事件**：`status / plan / step / evidence / ui / approval / degrade / artifact / done / error`。设计要点：

- 事件全部来自**已落库的状态**（先写库再推流），因此刷新/重连可用 `Last-Event-ID` 重放，不会"页面有、库里没有"。
- `artifact` 帧只携带 `artifact_id`，前端收到后回查 `GET /api/runs/{id}/artifacts` 取 `latest` 渲染——避免"SSE 与 REST 两套产物结构"分叉。
- 任一帧解析失败或组件非法，走降级卡并保留可读引用，不整页失败。

### 3.3 失败与降级矩阵（"模型或工具失败时应有降级与恢复"）

| 失败类型 | 系统行为 | 用户看到 |
|---|---|---|
| 模型不支持 `json_schema` | 自动降档 `json_object` → `none`，成功后按 `provider:model` 记忆 | `degrade` 事件 + 说明，不报"成功" |
| 模型给出不存在的工具/参数 | 未注册工具拒收；参数校验失败回灌重试；仍失败则保守兜底 | 任务显示失败原因，不伪造调用 |
| 目标代码不匹配 / 无结果 | 不借用其它公司代码，不补零 | 明确"未命中/缺口"，证据为空 |
| 上游不可达 / 429 / 业务码错误 | [`app/data/errors.py`](../app/data/errors.py) 区分错误类型 → 重试 → 熔断 → 备源（iFinD 若已配置）；**HTTP 200 但业务 `code!=0` 也按错误抛出** | 降级原因与来源标识 |
| 缓存过期兜底 | 允许用过期缓存但**必须标记** | `degrade_reason` + 过期时点 |
| iFinD MCP 未配置 / 401 | 直接报缺口，**不拿市场热榜冒充公司公告** | 失败的工具调用记录（云端实测 `2003 HTTP 401`） |
| 审批拒绝/超时 | 状态机收口为 `STOPPED` | 停止原因 |
| 进程重启 / 页面刷新 | 从事件与实体表恢复；已完成任务不重跑（`dedupe_key`） | Run 可继续，证据与产物仍在 |
| 动态 UI 非法 | 降级卡替代 | 原 JSON 可见，引用仍可读 |

**本次云端真实出现过的降级样本**（非 A 股目标"京东"）：6 个任务 4 个 `DEGRADED`、`search_ticker` 被上游以 `1003` 拒绝、`get_news_flash` 被 `2003 401` 拒绝，产物仍写出"热股榜未出现京东相关标的，无法支撑收入架构分析"并把缺口列进不确定层，合规判定 `PASS`。→ 结论：**失败被如实上报，没有静默生成"正常"结论**。同时暴露的边界（非 A 股不在主源覆盖、`asset_type` 缺枚举校验、云端 iFinD 令牌未授权）已登记在[项目 README §6](../README.md)与[测试说明](TEST_REPORT.md)。

### 3.4 一个必须说清的边界

真实财报能力目前只到**利润表总量**，没有经核验的连续三年**产品/业务分部**附注。因此：

- 能回答："近三年营业收入、成本、费用、净利润的总量变化"，并给出可回查的年度原值。
- 不能回答："收入架构（分部占比）变化"。页面必须显式写出这个能力缺口，而不是用总量变化冒充结构变化。

---

## 4. 契约总表（可实时核对）

| 契约 | 值 | 核对方式 |
|---|---|---|
| 数据表 | 15（13 实体 + 2 可观测性） | `GET /api/health` → `contracts.tables` |
| 检查点事件 | 26 | `contracts.events` |
| SSE 事件 | 10 | `contracts.sse_events` / `GET /api/tools` |
| UI 组件白名单 | 12（已实现 7，其余降级） | `contracts.ui_whitelist` / `ui_implemented` |
| 工具 | 10（9 数据 + 1 内部） | `GET /api/tools` → `count` |
| 停止规则 | 8 | `app/harness/stop_rules.py` |
| 预算维度 | 4（steps / tokens / cost / wall-time） | `app/harness/budget.py` |
| 审批类型 | 3（只 `plan` 接线） | `app/harness/hitl.py` |

## 5. 仍需现场说明的三件事

1. **成本闸门**：云端未配单价 → `cost_total=0` 不代表未调用模型（看 `tokens_total` / `llm_calls`）。要演示费用上限，必须先配 `LLM_PRICE_*`。
2. **审批触发点**：可演示计划审批的挂起/同意/拒绝/超时；`expensive_tool` 与 `final_review` 只有接口没有触发点。
3. **公网安全**：HTTP、无鉴权、无租户隔离、无网关限流；这是 24 小时交付的取舍，不是"已解决"。

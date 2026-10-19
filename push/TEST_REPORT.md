# 测试说明与评委复现路径

> 日期：**2026-10-20**（北京时间）。本文所有结论都标注证据层级，请按层级读：
>
> - **A｜本次独立复核**：本次收尾在本地/公网**实际执行**的命令与只读接口，含原文。
> - **B｜历史内部记录**：候选人本地真实取数/真实模型的记录，保留在内部交接文档（不随公开仓库交付）。
> - **C｜候选人确认**：候选人报告"云端已部署最新程序并通过操作测试"，包括浏览器里的完整 Run。本次未独立执行云端浏览器写操作。
>
> 三层的原文与限制都在文中，**不互相冒充**。

---

## 0. 环境与本次执行结果

| 层次 | 命令 / 对象 | 本次结果 | 层级 |
|---|---|---|---|
| 后端全量回归 | `conda run -n TongHua --no-capture-output python -m pytest -q` | 收集 **199** 条。本次沙箱内复跑 **196 passed + 3 error**；3 条均为 `tmp_path` 临时目录被沙箱拒绝（`ERROR at setup: PermissionError [WinError 5]`），**不是断言失败**。4 份历史记录为 `199 passed`。 | A（含环境限制） |
| 前端构建 | `npm --prefix web/skeleton run build` | 本次沙箱内 `next build` 子进程无法创建共享内存（`CreateFileMapping WinError 5`），未能重跑；历史记录（同日）为 **通过**，含 `/selftest` 构建期断言 11 条。 | A（失败原因=环境）/ B |
| 公网运行模式 | `GET http://8.136.108.32:8000/api/health` | `status=ok`、`env=prod`、`mock_data_mode=false`、`llm_configured=true` | A |
| 工具契约 | `GET http://8.136.108.32:8000/api/tools` | `count=10`，10 类 SSE 事件 | A |
| 控制台可达性 | `GET http://8.136.108.32:3000/console` | 200；首页 `/` 返回 307（跳转控制台） | A |
| 云端真实 Run | `GET /api/threads`、`/api/runs/{id}` 及其 `/evidences`、`/artifacts`、`/observability/*` | 5 条线程（均为"京东近三年收入结构/架构变化"）；最新 Run `COMPLETED`，详见 §2 T05 与 §4 | A（只读） |
| 浏览器完整 Run / 审批 / 断源 / 重启 | `/console` 实际操作 | 候选人报告已部署并通过操作测试；本次无脱敏录屏或 run 日志可公开 | C |

**结论先行**：本次独立看到的是**运行模式、工具契约、持久化状态、可观测数据**都符合设计；云端那条真实 Run 恰好落在"数据缺失"场景上，系统的表现（如实降级而非编造）**正是题目要求的行为**。完整的 A 股主链路浏览器 Run 属于 C 级证据，由候选人演示/录屏补齐。

---

## 1. 主链路：目标 → 计划 → 工具 → 证据 → 产物 → UI

| 测试 | 输入与操作 | 期望判定（合格线） | 自动化证据 |
|---|---|---|---|
| **T01 规划与执行** | `/console` 输入"分析宁德时代近三年营业收入和利润变化"，点击开始研究 | 出现 Thread/Run；任务拆解体现目标语义（定位标的 → 取财报 → 交叉复核 → 结论）；`search_ticker` 必须命中 `300750.SZ`；财报各期代码与目标一致；未命中时明确缺口 | `test_m5_run_demo_end_to_end`、`test_m2_pipeline_autowires_the_ten_tools`、`test_agent_does_not_borrow_unrelated_ticker_for_targeted_financials` |
| **T02 证据与产物** | 展开证据卡；再查 `GET /api/runs/{id}/evidences` 与 `/artifacts` | 证据可见来源、时点、单位、统计口径、原始引用；事实/推断/不确定/引用四层分离；事实 `refs` 可回查；**不得把"总收入变化"写成"分部结构变化"** | `test_financial_goal_preserves_tool_evidence_and_readable_fact`、`test_m5_artifact_builds_three_layers_with_condition_and_citations` |
| **T03 SSE 与动态 UI** | 观察任务进展；刷新页面重进 Run；查 `GET /api/runs/{id}/stream` | 事件来自已落库状态，刷新可重放；`artifact` 帧只带 ID 时前端回查完整产物；非法 props / 未知组件走降级卡而非白屏 | `test_m5_sse_stream_replays_frozen_events`、`test_m5_invalid_props_degrade_instead_of_blank`、`test_m5_unknown_component_degrades`、`/selftest` |
| **T04 检查点、恢复与记忆** | 运行中暂停/恢复；刷新页面；在新线程提一次历史偏好 | 已完成任务不重复调用工具（`dedupe_key`）；审批挂起跨刷新存活；压缩后旧证据仍可解析；只有 LONG 层跨线程 | `test_m3_resume_skips_finished_tasks_without_repeat_tool_calls`、`test_m3_approval_suspend_survives_page_refresh`、`test_m4_long_session_compaction_keeps_every_evidence_resolvable`、`test_m4_new_thread_recalls_historical_preference` |

**现场检查顺序**：先看 `/api/health` 的 `mock_data_mode`；再发起新研究；在任务卡核对**实际工具状态**；在证据卡展开字段；最后核对产物四层与不确定性。**不要只凭 Run 显示 `COMPLETED` 判断研究问题已回答。**

---

## 2. 数据缺失、模型与接口失败

| 测试 | 构造方式 | 期望结果 | 回归入口 / 本次实测 |
|---|---|---|---|
| **T05 目标不覆盖 / 空值** | 查询不在数据源覆盖内的标的目标（如港股/美股"京东"），或离线 Mock 里不存在的公司 | 应为空或明确"证据不足"；**不得借用其它公司代码、不得补零** | A 级实测：云端 Run `2ff94ed1…`（京东）→ 6 任务 4 个 `DEGRADED`，`search_ticker` 失败 `[1003] Parameter value out of range: asset_type`，`get_financials` 未产出证据，产物明确写"热股榜未出现京东相关标的，无法支撑京东收入架构分析"，不确定层列出缺失的分部/消息面/估值。回归：`test_mock_search_does_not_return_other_company_for_unmatched_goal` |
| **T06 缺年 / 时点冲突** | 输入"茅台近三年收入架构变化"，而样本只有两年总量 | 缺第三年、缺分部必须披露；晚于证据时点的披露不得写成"当时已知" | `test_financial_goal_preserves_tool_evidence_and_readable_fact`、`/selftest`（A，组件/接口级） |
| **T07 主源断网 / 限流 / 业务错误 / 过期缓存** | 测试替身模拟不可达、429、HTTP 200 但业务 `code!=0`、缓存过期 | 显示降级原因与来源；过期缓存必须标记，不得当实时证据 | `test_m2_offline_degrades_and_emits_notice`、`test_m2_rate_limited_degrades_at_step3`、`test_m2_stale_cache_fallback_is_labelled`、`test_m0_business_error_over_http_200_is_raised_by_code` |
| **T08 模型 / MCP 失效** | 模型缺凭据、格式不兼容；iFinD MCP 未配置或无结果 | 模型显式降级不抛错；MCP 不可用时报缺口，**不拿市场热榜冒充公司公告** | `test_m1_router_unconfigured_degrades_without_raising`、`test_m1_router_schema_retry_twice_then_succeeds`、`test_company_news_uses_ifind_query_instead_of_generic_hot_list`；A 级实测云端 `get_news_flash` 失败 `[2003] iFinD MCP 拒绝访问（HTTP 401）` |
| **T09 前端坏基址** | 把 `NEXT_PUBLIC_API_BASE` 配成 `http:` 这类不完整值 | 不得把坏值传给 `fetch()`，应回退到当前主机 `:8000`；合法地址不得被改写 | `/selftest` 构建期断言（A，组件级）；云端 HTTP/跨源 POST/SSE 仍需现场核查 |

> **T05 为什么算"通过"而不是"失败"**：题目要求"数据缺失、冲突、过期或调用失败时不得静默生成'正常'结论"。云端那次 Run 的产物把失败写成事实与不确定，而不是给一个像样的结论——这是**正确行为**。同时它暴露了三个已登记边界：非 A 股不在主源覆盖、`asset_type` 缺枚举校验、云端 iFinD 令牌未授权（401）。

---

## 3. 极端与合规边界

| 测试 | 期望结果 | 回归入口 |
|---|---|---|
| **T10 预算 / 死循环 / 无进展** | 命中 `max_steps`、token/成本/时长上限或重复动作时记录停止原因，Run 收口为 `STOPPED`，不再调用工具；单价为 0 时不得把费用闸门当可靠限额 | `test_m4_step_level_max_steps_stops_run_and_lands_events`、`test_m3_loop_detected_within_three_repeats_and_names_hit_rule`、`test_m3_budget_charges_and_reports_exhaustion_in_order` |
| **T11 审批与非法迁移** | 计划审批挂起后刷新仍等待；同意→重规划续跑；拒绝/超时→停止；非法状态迁移被拒并记事件。**只有计划审批有自动触发点** | `test_m3_resume_after_approval_replans_and_finishes`、`test_m3_hitl_expires_pending_approval_after_timeout`、`test_m5_approval_reject_stops_run`、`test_m3_state_machine_rejects_illegal_transition_and_records_event` |
| **T12 无引用事实 / 非法 UI** | 无 `refs` 的 FACT 不得作为有效事实展示；未实现/未知组件或缺 props 走降级卡，不白屏 | `test_m5_fact_conclusion_without_refs_is_blocked`、`test_m5_invalid_props_degrade_instead_of_blank`、`test_m5_unknown_component_degrades` |
| **T13 买卖建议 / 预测 / 隐私** | 注入"建议买入某股""必涨"等禁止文本应被 Guard 拦截并记录；无切换条件的推断被拦；Trace 对已知 Key 与常见隐私字段脱敏 | `test_m6_pipeline_guard_rejects_advice_artifact`、`test_m6_guard_rejects_fact_without_refs`、`test_m6_guard_rejects_inference_without_condition`、`test_m6_tracer_persists_sanitized_and_numeric_attrs`；A 级实测云端 `GET /observability/runs/{id}/compliance` → `verdict=PASS`、`strict=true`、`findings=[]` |

**现场注意**：不要用真实持仓/个人资料做边界测试；不要在公网演示环境发密钥或制造高并发；异常注入在隔离环境完成，公网演示环境不做破坏性操作。

---

## 4. 本次只读核查原文（可复现）

### 4.1 运行模式与契约

```text
GET http://8.136.108.32:8000/api/health   → 200
{"status":"ok","env":"prod","mock_data_mode":false,"llm_configured":true,
 "contracts":{"tables":15,"events":26,"sse_events":10,"ui_whitelist":12,
              "ui_implemented":7,"render_strict":true,"ui_block_max":200}}
```

### 4.2 工具注册（10 个）

```text
GET http://8.136.108.32:8000/api/tools    → 200
{"count":10,"sse_events":["status","plan","step","evidence","ui","approval",
 "degrade","artifact","done","error"], "tools":[ ... 每个含 name/description/
 domain/phase/permission_level/idempotent/timeout_ms/est_cost/schema_json ... ]}
```

工具清单：`search_ticker`、`get_price_snapshot`、`get_price_history`、`get_index_constituents`、`get_valuation`、`get_financials`、`get_hot_rank`、`get_limit_up_stats`、`get_news_flash`、`emit_artifact`。

### 4.3 一条真实 Run（T05 的证据）

```text
GET /api/runs/2ff94ed110924bbab523cdc0495b8f5a
{"status":"COMPLETED","cost_total":0.0,"tokens_total":5351,
 "goal":"分析京东近三年公司收入架构的变化","ui_block_count":15,
 "tasks":[ {"seq":1,"status":"DEGRADED","attempts":2,"evidence_count":0,
            "degradation_reason":"本轮未取到任何可用证据（工具全部降级）"}, ...
           {"seq":5,"status":"DEGRADED","attempts":3,"evidence_count":1,
            "degradation_reason":"主源与备源均失败：[2003] iFinD MCP 拒绝访问（HTTP 401）..."},
           {"seq":6,"status":"DONE","attempts":2,"evidence_count":1} ]}

GET /api/runs/{id}/evidences  → count=2
 tool_calls:
   search_ticker  FAILED  [1003] Parameter value out of range: asset_type
   get_news_flash FAILED  [2003] iFinD MCP 拒绝访问（HTTP 401）
   get_hot_rank   OK      source=fuyao, interface=/api/a-share/special-data/hot-stock-list
   emit_artifact  OK      source=internal
 产物 value:
   facts:        ["热股榜数据仅包含新华传媒、宇树科技、亨通光电等标的，未出现京东相关标的，
                   无法直接支撑京东收入架构分析。"]
   inferences:   ["现有证据不足以量化京东近三年收入架构变化……应标注为证据不足"]
   uncertainties:["缺少京东近三年分部收入（零售、物流、新业务等）结构化数据", ...]
   citations:    ["bcce0e4d…"]   ← 指向上面那条真实热榜证据

GET /api/observability/runs/{id}/metrics → 9 项
   cost_cny=0.0 / degraded_tasks=4 / evidence_count=2 / guard_findings=0 /
   render_failed_count=0 / tokens_total=5351 / tool_calls=4 / trace_spans=28 /
   ui_block_count=15
GET /api/observability/runs/{id}/cost → llm_calls=9 tokens_total=5351
   prompt=4071 completion=1280 failed_calls=0 by_model={"deepseek-chat":{...}}
GET /api/observability/runs/{id}/compliance → {"verdict":"PASS","strict":true,"findings":[]}
```

**读法**：`COMPLETED` 只说明管线收口；`degraded_tasks=4` 与产物里的"证据不足"才是这次 Run 的真实结论。`cost_total=0` 是**未配单价**，`tokens_total=5351`、`llm_calls=9` 证明模型确实被调用了 9 次。

### 4.4 本地回归片段（沙箱环境限制）

```text
收集: 199 条（tests/test_m0_* … tests/test_m6_*）
运行: .........................................EEE............................ [ 36%]
      ........................................................................ [ 72%]
      .......................................................                  [100%]
3 条 ERROR 均为 setup 阶段：
  PermissionError: [WinError 5] 拒绝访问。: '…\tmp_pytest\pytest-of-HCQ'
  (tests/test_m0_settings.py 中 3 条使用 tmp_path 的用例；沙箱禁止创建临时目录)
```

> 这不是断言失败，也与业务逻辑无关；在常规桌面环境（候选人本机）同一套用例记录为 **199 passed**。

---

## 5. 评委自行复现步骤

**A. 只读检查（30 秒，无副作用）**

```powershell
Invoke-RestMethod http://8.136.108.32:8000/api/health | ConvertTo-Json -Depth 4
Invoke-RestMethod http://8.136.108.32:8000/api/tools  | Select-Object count, sse_events
```

**B. 浏览器主链路（3 分钟，会产生真实 Run 与费用）**

1. 打开 `http://8.136.108.32:3000/console`，确认页面标识的真实/Mock 状态。
2. 输入**A 股**目标：`分析宁德时代近三年营业收入和利润变化`（或 `贵州茅台`）。
3. 核对任务卡：每个任务的工具是否**实际调用**；`search_ticker` 命中的代码是否为 `300750.SZ`/`600519.SH`。
4. 展开证据：来源 / 时点 / 单位 / 口径 / 原始引用是否齐全。
5. 看产物：事实是否带引用、推断是否带条件、不确定是否写明缺口。
6. 刷新页面重进 Run：SSE 重放与已落库状态是否一致。

**C. 接口级核对（拿到 run_id 后）**

```powershell
$r="http://8.136.108.32:8000/api/runs/<RUN_ID>"
Invoke-RestMethod "$r"                              | ConvertTo-Json -Depth 4
Invoke-RestMethod "$r/evidences"                     | Select-Object count
Invoke-RestMethod "$r/artifacts"                     | ConvertTo-Json -Depth 4
Invoke-RestMethod "http://8.136.108.32:8000/api/observability/runs/<RUN_ID>/metrics"
Invoke-RestMethod "http://8.136.108.32:8000/api/observability/runs/<RUN_ID>/compliance"
```

**D. 本地复现（可选）**

```powershell
cd TongHua          # 克隆后仓库根即项目根
conda run -n TongHua --no-capture-output python -m pytest -q
npm --prefix web/skeleton run build
```

---

## 6. 云端验收记录

| 项 | 状态 | 证据 |
|---|---|---|
| 最新程序已部署 | 已完成（候选人） | `/api/health` 的 `env=prod`、`mock_data_mode=false`、`llm_configured=true`（A） |
| 云端浏览器完成研究操作测试 | 已完成（候选人确认） | 云端 5 条真实线程 + 可读的 Run/证据/产物/指标（A 只读可见）；浏览器点击过程本身为 C |
| 数据缺失下的降级行为 | 已验证（本次只读复核） | §2 T05 与 §4.3 原文（A） |
| 合规守卫 | 已验证（本次只读复核） | `verdict=PASS / strict=true / findings=[]`（A） |
| 审批（计划审批挂起/同意/拒绝/超时） | 自动化已验证；**云端未实测** | `tests/test_m3_*`、`test_m5_approval_reject_stops_run`（A 本地）；云端 UAT 待候选人补 |
| 断源注入 / 服务重启恢复 | 自动化已验证；**云端未实测** | `tests/test_m2_*`、`test_m3_resume_*`（A 本地）；云端 UAT 待候选人补 |
| 公网 HTTPS / 鉴权 / 限流 | **未做**（已知边界） | 项目 README §6/§7 |

**可补齐项（不预填、不伪造）**：最终部署提交号、Run 日期与脱敏 `run_id`、浏览器跨源 POST/SSE 实测、重启后恢复实测、云端审批与 Guard 实测、演示视频链接。见[提交清单](SUBMISSION_CHECKLIST.md)。

---

## 7. 不可宣称的结论（诚实边界）

1. 不能宣称"近三年收入架构（分部占比）变化"已被真实数据验证——只有利润表总量，缺三年产品/业务分部附注。
2. 不能宣称云端完成过一次**包含 iFinD MCP 取证**的完整 LLM Run——云端 iFinD 当前返回 401，且历史上也无同一次 Run 内完成的证据。
3. 不能宣称 `cost_total=0` 表示零成本或未调用模型——云端未配单价。
4. 不能宣称公网入口已达生产安全标准——HTTP、无鉴权、无租户隔离、无限流。
5. 不能把 `/selftest`、健康探针或 `COMPLETED` 当作"研究结论正确"的证明。

# 投资 X Buddy｜提交入口与验收导读

面向投资研究者的个人 Agent 工作台：给出一个研究目标，系统规划任务、受控调用金融工具、管理上下文与长期状态、产出可验证的分层研究结论，并在需要时请求人工审批。**产品不提供确定性涨跌预测、收益承诺或直接买卖建议。**

| 必交项 | 入口 | 如何核查 |
|---|---|---|
| **① 可操作 Web 产品** | [http://8.136.108.32:3000/console](http://8.136.108.32:3000/console) | 首页 `/` → 307 跳 `/console`；输入非敏感 A 股目标即可发起 Run。当前为 **HTTP**，勿填私人信息。 |
| **② 源代码仓库 + README** | [github.com/hcidfy/TongHua](https://github.com/hcidfy/TongHua) ，项目根 `InvestmentXBuddy/`；README 见[项目 README](../README.md) | README 覆盖启动方式、环境变量、产品选择、AI 角色、数据来源、已知边界、未做事项七项。 |
| **③ AI 使用与验证记录** | [AI_USAGE_AND_VERIFICATION.md](AI_USAGE_AND_VERIFICATION.md) | AI 工具、参与环节、候选人修正的错误/不合理结果、责任划分。 |
| **④ 测试说明** | [TEST_REPORT.md](TEST_REPORT.md) | 主链路、数据缺失/接口失败、极端与合规边界；含本次云端只读核查原文与复现步骤。 |
| **⑤ 单文件版（③+④ 合并，建议直接提交给平台）** | [AI使用与验证记录和测试说明.md](AI使用与验证记录和测试说明.md) | 自包含，可独立阅读。 |
| 提交要求 1–3 的实现对照 | [IMPLEMENTATION.md](IMPLEMENTATION.md) | 主链路、控制面、产品映射与降级恢复，逐项指到代码与接口。 |
| 60–180 秒演示视频（可选） | [video.mp4](video.mp4)（同目录，H.264/MP4，约 15 MB） | 产品实录；如另需平台上传链接，由候选人在提交平台补充。 |

> **本材料的证据纪律**：凡是"本次独立看到"的，给出可复现的接口与原文；凡是"候选人确认"的（云端浏览器操作、视频），明确标注为主观确认；凡是历史记录，标注为历史记录。三者不互相冒充。

---

## 30 秒理解产品

| 用户看到的东西 | 背后的实体 | 在产品里的意义 |
|---|---|---|
| 线程 + 研究目标 | `threads` / `runs` | 一次研究是可恢复、可暂停、可审批的工作单元 |
| 任务与执行计划 | `tasks` + `tool_calls` | 研究子问题；工具"实际调用/失败/未调用"分开显示，不能凭计划推断已取数 |
| 证据 | `evidences` | 来源、时点、单位、统计口径、原始字段、降级原因；带 `thscode` 的取数只留目标标的 |
| 研究产物 | `artifacts` | 事实 / 推断 / 不确定 / 引用四层分离；事实必须有引用，推断必须带条件 |
| 动态 UI | `ui_blocks`（12 项白名单，已实现 7） | 非法组件 / 缺 props → 降级卡，不白屏 |
| 实时进展 | SSE 10 类固定事件 | 前端在 `artifact` 帧后回查完整产物；断线重连不脱离已落库状态 |

---

## 1. 启动方式 / 环境变量 / 产品选择 / AI 角色 / 数据来源 / 边界与未做

这七项完整写在[项目 README](../README.md)，章节对应关系：

| 题目要求 | README 章节 | 一句话摘要 |
|---|---|---|
| 启动方式 | §1 启动方式 | 云端直接访问；本地 `pip install -e ".[dev]"` + `alembic upgrade head` + uvicorn:8000 + `npm run dev`:3000；`start_local.ps1` 一键校验并起服务 |
| 环境变量 | §2 环境变量 | 8 组变量表；`MOCK_DATA_MODE=false` 且有 `FUYAO_API_KEY` 才走真实主源；`cost_total=0` ≠ 未调用模型 |
| 产品选择 | §3 产品选择 | 自研 Harness（不用 LangGraph）、FastAPI + Next.js、SQLite 单实例、冻结 15/26/10/12/7/10 契约 |
| AI 的角色 | §4 AI 的角色 | 6 个角色（澄清/规划/推理/压缩/合规/综合）只做语言与判断；事实来自工具与证据元数据 |
| 数据来源 | §5 数据来源 | 扶摇主源（A 股/指数）、iFinD MCP（定向文本，低置信度）、Mock（明确标识的离线演示） |
| 已知边界 | §6 已知边界 | 8 条：非 A 股覆盖、财报只有总量、成本未计价、MCP 全链未验收、HTTP 无鉴权、CORS、参数枚举、组件覆盖 |
| 未做事项 | §7 未做事项 | HTTPS/鉴权/限流、三年分部附注、另两类审批触发点、5 个白名单组件、向量召回、跨市场映射、CI/E2E |

---

## 2. 5 分钟了解项目

1. 打开 `/console`，先看 `/api/health` 或页面模式标识，确认 `mock_data_mode=false`、`llm_configured=true`（云端当前即是）。
2. 输入**A 股**研究目标（推荐 `分析宁德时代近三年营业收入和利润变化` 或 `分析贵州茅台近三年营业收入和利润变化`）。⚠️ 不要用"京东"这类港股/美股标的做演示：扶摇主源不覆盖，会（正确地）降级为"证据不足"，见[测试说明 §2](TEST_REPORT.md)。
3. 观察：任务拆解 → 每个任务的实际工具状态（成功/失败/未调用）→ 证据卡展开字段（来源、时点、单位、口径、`raw_ref`）→ 右侧"研究结论与核验说明"（事实 / 推断 / 不确定 / 引用）。
4. 打开浏览器开发者工具的 EventStream，刷新页面，确认 SSE 重放与已落库状态一致、引用可回查。
5. 边界核查：对"收入架构变化"类目标，页面必须出现"利润表只有总量、缺产品/分部收入"的限制说明；若工具失败，必须出现降级原因而不是"正常"数字。

**只看 `COMPLETED` 不等于研究问题被正确回答**：`COMPLETED` 只表示 Run 收口。判定顺序是：标的正确 → 证据可回查 → 产物分层 → 缺口被披露。

---

## 3. 本次（2026-10-20）收尾核对到的云端事实

原文摘录见[测试说明 §4](TEST_REPORT.md)。

| 检查 | 结果 |
|---|---|
| `GET /api/health` | `status=ok`、`env=prod`、`mock_data_mode=false`、`llm_configured=true`；契约 `tables=15 / events=26 / sse_events=10 / ui_whitelist=12 / ui_implemented=7 / render_strict=true` |
| `GET /api/tools` | `count=10`（9 数据工具 + 内部 `emit_artifact`），每个工具带 schema、`permission_level`、`timeout_ms`、`est_cost` |
| `GET /api/threads` | 5 个线程全部为"京东近三年收入结构/架构变化"（候选人的云端操作测试目标） |
| `GET /api/runs/{id}`（最新 Run） | `COMPLETED`、`tokens_total=5351`、`cost_total=0`（云端未配单价）；6 个任务中 4 个 `DEGRADED`、2 个 `DONE`；`ui_block_count=15` |
| `GET /api/runs/{id}/evidences` | 2 条证据：`get_hot_rank`（`source=fuyao`，真实热榜）+ 内部 `emit_artifact`；`tool_calls` 明确记录 `search_ticker FAILED [1003 asset_type]`、`get_news_flash FAILED [2003 iFinD 401]` |
| `GET /api/runs/{id}/artifacts` | 1 个产物，事实层明确写"热股榜未出现京东相关标的，无法支撑收入架构分析"，不确定层列出缺失的分部收入/消息面/估值 |
| `GET /api/observability/runs/{id}/metrics` | 9 项指标（`cost_cny / degraded_tasks / evidence_count / guard_findings / render_failed_count / tokens_total / tool_calls / trace_spans / ui_block_count`） |
| `GET /api/observability/runs/{id}/compliance` | `verdict=PASS`、`strict=true`、`findings=[]` |

**这说明什么**：非 A 股目标下，系统没有编造"正常"结论，而是把失败如实上报、把缺口写进不确定层——这正是题目"数据缺失不得静默生成正常结论"的要求。同时暴露两个已登记边界：`search_ticker` 的自由文本参数未做枚举校验（上游返回 `1003`）、云端 iFinD 令牌未授权（`401`）。

**这说明不了什么**：本次未独立执行云端浏览器的完整 A 股 Run、未做审批/断源/重启注入，也未取得可公开的脱敏 Run 录屏；这些属于候选人的操作测试范围，见[测试说明](TEST_REPORT.md)的证据分级。

---

## 4. 本目录文件说明

| 文件 | 给谁看 | 内容 |
|---|---|---|
| `README.md` | 入口 | 本文件：必交物索引、30 秒理解、评判路径、云端核对结果、文件清单 |
| `IMPLEMENTATION.md` | 监考官 | 提交要求 1–3 逐项实现对照：能力注册、计划执行、压缩、记忆、检查点恢复；权限/审批/停止/成本/可观测；线程/任务/证据/产物/UI 映射与失败降级恢复 |
| `AI_USAGE_AND_VERIFICATION.md` | 监考官 | AI 工具清单、AI 参与环节、候选人具体承担的工作与 10 条纠偏记录、责任边界 |
| `TEST_REPORT.md` | 监考官 | 三类覆盖（主链路 / 缺失失败 / 极端合规）13 个场景、本地回归原文、云端接口原文、复现步骤、云端验收记录表 |
| `AI使用与验证记录和测试说明.md` | 提交平台 | ③+④ 合并的自包含单文件版 |
| `SUBMISSION_CHECKLIST.md` | 候选人 | 提交前待办：待填项（Run ID / 平台视频链接）、录屏脚本、安全检查、Git 操作提醒 |
| `video.mp4` | 评委 | 60–180 秒产品实录（可选视频，H.264/MP4 约 15 MB） |

---

## 5. 声明的边界

- 产品提供的是**研究证据与条件性结论**，不是投资建议；不预测涨跌、不承诺收益。
- 缺数据、口径冲突、越权访问、工具失败一律显式降级；降级不等于"研究已完成"。
- 公网入口无 HTTPS/鉴权/限流，禁止输入真实持仓、账户、密钥、隐私或受限第三方数据。
- 本目录是**评审说明包**，不是第二份源码；源码以仓库为准。文档更新需要候选人审阅后再推送到远端。

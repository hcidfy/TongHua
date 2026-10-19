# 投资 X Buddy · 搭建计划（**最终版 v2 · 24h 口径**）

> 配套文档：[01_PRD.md](./01_PRD.md) ｜ [02_ARCHITECTURE.md](./02_ARCHITECTURE.md) ｜ [DATASOURCES.md](./DATASOURCES.md) ｜ [HANDOFF.md](./HANDOFF.md)
> 版本：**v2（2026-10-19）** — **取代 v1（48h 口径）**，v1 的排期与规模数字全部作废（见 §0、§11）
> 口径依据：题目原文 [投资XBuddy.md](../投资XBuddy.md)「**做题时间：24 小时**」
> **本文件是唯一的排期与范围依据**。与其它文档数字冲突时，一律以本文件 [§10 契约数字唯一来源](#10-契约数字唯一来源一致性自检) 为准。
> **本文件只做规划，不含实际代码。**

---

## 0. v1 → v2 变更摘要（先看这张表）

| 项 | v1（**已废弃**） | v2（生效） | 原因 |
|---|---|---|---|
| 总时长 | 48h | **24h** | 题目原文与 PRD 均为 24h；v1 括号里写「统一规则口径」是**归因错误** |
| 时间盒 | T0–T48 | **T0–T24** | 同上 |
| 数据工具 | 12 | **9** | 24h 收敛（架构 §9 按 8 重排）；**2026-10-19 放开为 9**：补入 `get_financials`（「收入/财务结构」类目标必需，见 §10） |
| 工具注册项 | 12（口径混乱） | **10 = 9 数据 + 1 内部** | 消除「标题 12 / 表格 13 行」的矛盾 |
| UI 组件 | 实现 12 | **实现 7**（白名单仍 12） | 题目只要求证明「动态 UI 映射」机制，白名单是契约全集 |
| checkpoint 事件 | 22 类（**错**） | **26 类** | 架构 §4.2 实列 24 类 + 补登 `RunPaused`/`RunResumed` |
| DB 表 | 15 | **15**（不变） | 已定案：13 实体 + `llm_calls` + `run_metrics` |
| 领域模型（Pydantic） | 9（**错**） | **13** | 9 与架构 13 实体对不上，改按实体全量出模型 |
| SSE 事件 | 11 类（**错**） | **10 类** | 架构 §13.1 实列 10 类 |
| 关键路径 | 漏 M6 | **M0→M2→M1→M3→M5→M6→M7** | M6 承载题目②的可观测性与合规红线，必须在路径上 |
| 开发方式 | 8 个 Owner **并行** | **单人 + AI，模块串行** | 24h 下并行协调成本（契约对齐）高于收益 |
| 可砍项「长期记忆语义召回」 | 列为可砍 | **作废** | 架构 §8 本就只做关键词召回、语义召回是「方案预留不落地」，砍一个不存在的东西属于**无效项** |
| iFinD 定位 | 「密钥不可得、保持未启用」（R-2 概率高） | **「已实测可用，但仅作降级备源」** | [DATASOURCES.md](./DATASOURCES.md) §3 已实测 36 工具可用；不承担主链路的原因改为**返回自然语言文本、无强类型 schema**（工程取舍，非能力盲区） |
| M2 的 `ifind_client.py` 行为 | `IFIND_MCP_ENABLED=false` 时返回 `NotEnabled` | **默认启用，仅作降级备源**；不可用时返回 `NotEnabled` | 同上 |
| `observ/cost.py` | 独立文件 | **保留，但只做「事后计价 + 汇总查询」** | 与 `harness/budget.py`（实时限额）职责写死，消除重复实现 |
| `harness/memory_recall.py` | 独立文件 | **废弃，并入 `harness/memory.py`** | 架构 C8 `MemoryStore.recall()` 本就是同一组件 |
| `runtime/recovery_scanner.py` | 独立文件 | **废弃，并入 `harness/resume.py`** | 架构 §6.1 明说「ResumeEngine 扫 RUNNING 状态的 Run」 |
| `compliance/rules.py` | 独立文件 | **废弃，并入 `compliance/guard.py`** | 规则表 = 模块内数据常量，不单独成模块 |

> **净减 3 个文件**（memory_recall / recovery_scanner / rules）。
> 注：我在核对时曾估计「净减 2 个」，**该估计有误**，实际为 3 个——此处更正。

---

## 1. 总览

| 维度 | 数量 |
|---|---|
| 总时长 | **24h**（T0–T24） |
| 模块 | **8 个**（M0–M7）— 逐条对应题目要求，**无可删模块**（证明见 §1.2） |
| 关键路径 | **M0 → M2 → M1 → M3 → M5 → M6 → M7** |
| 里程碑 | **6 个**（G0–G5） |
| 数据工具 | **9 个**（+1 内部 COMMIT 工具 = **10 个注册项**） |
| UI 组件 | 白名单 **12 个**，24h **实现 7 个** |
| 可砍项 | **3 档**（见 §8） |
| 开发方式 | **单人 + AI 辅助，模块串行**（非多 Agent 并行） |

### 1.1 排期原则

1. **纵向切片优先**——先打通「一句话目标 → 出 1 条证据 → 出 1 个结论卡」的最小闭环，再横向加厚。
2. **内核优先于外观**——M1/M3 是题目考察重点；宁可前端朴素，不可内核空转。
3. **Mock 与部署双前置**——M0 即建立离线 Mock（不依赖网络可演示）**并完成一次公网空壳部署**（把 R-9 部署风险压到 T3 之前）。
4. **契约一次性冻结**——M0 冻结后，任何模块不得改契约；表由 M0 独占。

### 1.2 模块必要性证明（8 个模块，0 个可删）

判据：**只用题目硬要求反向验证**。一个模块只有在「删掉后至少一条题目要求无法交付」时才算必要。

| 模块 | 承载的题目原文要求 | 不可替代的产出 | 判定 |
|---|---|---|---|
| **M0 地基** | ①②③④ 的**全部前置** | 领域模型 / 事件 / 枚举 / DB / Client / Mock | **必要**（删了其余 7 个模块无契约可用） |
| **M1 内核** | ①「能力/工具注册、**计划执行**」 | `PreHook→Loop→PostHook` 管线、ToolRegistry、AgentLoop、ModelRouter | **必要**（ADR-1 技术叙事主线） |
| **M2 数据与工具** | ①「工具注册」③「**证据**」 | 真实数据通道 + 降级阶梯 + 质量元数据 | **必要**（无数据则无 Evidence，③的映射图不成立） |
| **M3 状态与恢复** | ①「**检查点与恢复**」②「权限、**审批、停止规则、成本/时延**」 | CheckpointStore / ResumeEngine / HITLGate / StopRule / Budget | **必要**（单独承载 2 条硬要求中的 6 个考点） |
| **M4 记忆与上下文** | ①「**上下文压缩、记忆**」 | ContextManager / Compactor / 三层记忆 | **必要**（题目点名两项） |
| **M5 产物与动态 UI** | ③「线程、任务、证据、**产物和动态 UI** 如何映射」④「可实际操作 Web URL」 | ArtifactBuilder / UIRenderer / 前端控制台 | **必要**（③的核心考察点） |
| **M6 可观测与合规** | ②「**可观测性**」+ 统一规则 4 条（事实/推断区分、可回溯、不得静默失败、来源时点单位口径） | Tracer / Guard / 脱敏 | **必要**（统一规则是红线，不是加分项） |
| **M7 交付** | ④ 全部提交物 + ⑤ 演示视频 | URL / 仓库 / README / AI 记录 / 测试说明 | **必要**（缺任一项即不满足提交要求） |

**结论：不存在「不需要的模块」。** 可优化的是**模块内部的冗余**（§0 已处理 4 处）与**交付深度**（§8 可砍项），不是模块本身。

---

## 2. 24 小时时间盒

| 时段 | 模块 | 目标（可验证） | 里程碑 |
|---|---|---|---|
| **T0–T3** | **M0** 地基 | 契约冻结、DB 可建表、扶摇取到真实 snapshot、Mock 可用、**公网空壳可访问** | **G0 地基就绪** |
| **T3–T9** | **M1** 内核 + **M2** 数据工具 | 管线跑通 1 个 Task、9 个工具注册、产出 Evidence、**Tracer 埋点同步完成** | **G1 主链路半通** |
| **T9–T13** | **M3** 状态与恢复 | Checkpoint 落事件、kill 后能续跑、审批可挂起/继续、停止规则命中 | **G2 可恢复** |
| **T13–T16** | **M4** 记忆与上下文 | 压缩触发且证据引用不丢；会话/长期记忆可召回 | — |
| **T16–T19** | **M5** 产物与动态 UI | 三层 Artifact 产出；7 组件渲染；前端可操作 | **G3 产品可用** |
| **T19–T21** | **M6** 可观测与合规 | Span 树可查；Guard 能拦截违规输出；日志无 Key | **G4 可审计** |
| **T21–T24** | **M7** 交付 | 部署、README、测试说明、AI 记录、录视频 | **G5 可提交** |

**缓冲策略**：T19–T24 内嵌 1.5h 浮动。任一里程碑超时 **> 1h** 即启动 §8 可砍项（从档 1 往上砍）；**不压缩 M7**（交付物是硬性要求）。

**三条时序硬约束（违反必返工）**：
1. **Tracer 埋点必须在 T3–T9 写 AgentLoop 时同步做**（R-8）；否则 M6 无数据可查。
2. **`tool_calls.dedupe_key` 与 `checkpoint_events(run_id, seq)` 两条 UNIQUE 约束必须在 M0 建表时落地**；否则 M3 的恢复会重复副作用（R-5）。
3. **公网部署演练必须在 G0 完成**（空壳即可）；否则部署风险会压到 T21 之后，无法回滚（R-9）。

---

## 3. 模块分解

> 格式：**目标 / 组件与内部任务 / 配置项 / 接口契约 / 依赖 / 验收**

---

### M0 · 地基（Foundation）

**目标**：所有模块的共同前置。定义契约，让后续模块能独立开工。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `domain/enums.py` | **5 个枚举**：`RunStatus` / `TaskStatus` / `StopReason` / `Phase` / `PermissionLevel`（取值严格按架构 §5 状态机图） |
| `domain/models.py` | **13 个 Pydantic v2 领域模型**（= 架构 §4.1 的 13 个实体，**全量，不缩水**）：Thread / Run / Task / Step / ToolCall / Evidence / Artifact / UIBlock / CheckpointEvent / Memory / Trace / Approval / ToolSpec。**契约单点，产出即冻结** |
| `domain/events.py` | **26 类** checkpoint 事件（架构 §4.2 的 24 类 + `RunPaused` / `RunResumed`） |
| `config/settings.py` | Pydantic Settings；变量清单见架构 §14.1；**`FUYAO_API_KEY` 须为可选** |
| `store/db.py` | SQLAlchemy 2.0 引擎 / Session / Base |
| `store/schema.py` + Alembic | **15 张表**建表迁移（13 实体 + `llm_calls` + `run_metrics`）+ **2 条 UNIQUE 约束** |
| `store/repo/*.py` | 每表一个 Repository（CRUD）；M0 先只做 6 张：threads / runs / tasks / steps / evidences / checkpoint_events |
| `data/errors.py` | 领域异常：`RateLimited(4001)` / `NotReady(3002)` / `SourceTimeout(5002)` / `SourceDown(5003)` / `NoPermission(2003)` |
| `data/fuyao_client.py` | `X-api-key` 注入、`ApiResponse` 信封解析、**靠 `code` 判错（业务错误 HTTP 恒为 200）**、毫秒时间戳、`thscode` 校验 |
| `data/mock/*.json` | 离线数据集：**3 只标的**（`600519.SH` / 半导体题材股 / `000001.SH`）× 行情/估值/财务 + 指数成分 + 涨跌停 + 热榜 + 资讯，**信封与真实响应完全同构** |
| `data/mock_client.py` | 与真实 Client **同签名**，由 `MOCK_DATA_MODE` 切换 |
| `web/skeleton` | Next.js 空壳：布局 + 路由 + SSE 连接封装 + **一次公网部署演练** |

**配置项**：`FUYAO_BASE_URL`、`FUYAO_API_KEY`、`DATABASE_URL`、`MOCK_DATA_MODE`、`APP_ENV`

**接口契约**
```text
FuyaoClient.get(path, params) -> ApiData        # 已剥信封，业务错误抛领域异常
FuyaoClient.raw(path, params) -> RawResponse    # 保留原始响应，供 Evidence.raw_ref
MockClient.get / raw                            # 与上完全同签名，可无缝替换
```

**依赖**：无（起点）
**验收**：`pytest -k m0` 通过；真实取到 `600519.SH` snapshot（字段含 `currency` / `timestamp`）；断网下 Mock 通道产出**同结构**；**公网空壳 URL 可访问**。

---

### M1 · Harness 内核（Kernel）

**目标**：项目的心脏，题目「能力/工具注册、计划执行」的直接对应物。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `harness/pipeline.py` | `PreHook → AgentLoop → PostHook` 三段管线；Hook 注册机制；**PostHook 无条件执行**（`try/finally` 保障） |
| `harness/hooks/pre/*` | ① ContextInject ② BudgetCheck ③ GuardPre ④ Plan（**ContextInject 的文件实现在 M4，本模块只留注册位**） |
| `harness/hooks/post/*` | ① EvidencePersist ② TraceFlush ③ MemoryWrite ④ CostSettle ⑤ GuardPost |
| `harness/tool_registry.py` | `ToolSpec` 注册/查询、从 DB 加载、**phase 声明**（EXPLORE/COMMIT） |
| `harness/tool_executor.py` | 参数校验 → 幂等检查 → 限流 → 缓存 → 主源 → 重试 → 备源 → 降级 → 质量元数据注入 |
| `harness/agent_loop.py` | ReAct：Think(LLM) → Act(Tool) → Observe；每轮落 Step；出口条件判定 |
| `harness/planner.py` | 目标 → Task DAG；**工具名必须来自 Registry**（校验拒收） |
| `harness/session_manager.py` | Thread/Run 生命周期；**Lane 锁**（同 Thread 串行、跨 Thread 并行） |
| `harness/model_router.py` | provider 路由、超时、熔断、failover、结构化输出（`json_schema` + Pydantic 二次校验 + 自动重试 2 次） |
| `harness/prompts/*.md` | **6 个角色**提示词（Clarifier / Planner / Reasoner / Compactor / Synthesizer / Guard） |

**配置项**：`LLM_PROVIDER_PRIMARY`、`LLM_BASE_URL_PRIMARY`、`LLM_API_KEY_PRIMARY`、`LLM_MODEL_PRIMARY`、`LLM_MODEL_SMALL`、`LLM_TIMEOUT_MS`、`LLM_MAX_FAILOVER`
（`CACHE_TTL_S` 归属 M2，**不再放在 M1**）

**接口契约**
```text
Pipeline.run(run_id, goal) -> RunResult
ToolRegistry.register(spec) / list() / get(name)
ToolExecutor.invoke(run_id, task_id, call) -> ToolResult | DegradedResult
AgentLoop.iterate(task, ctx) -> Step
ModelRouter.complete(role, messages, schema) -> LLMResult
```

**依赖**：M0（契约）。与 M2 为**契约并行、联调串行**（见 §4.3）
**验收**：给定目标「半导体板块最近资金情况」，自动规划 ≥3 个 Task 并执行完成、产出 ≥5 条 Evidence；Trace 中每个 Task 有完整 Loop 记录；模拟 `429/4001` 时能退避重试并最终降级。

---

### M2 · 数据与工具（Data & Tools）

**目标**：把扶摇能力裁剪成 **9 个受控数据工具 + 1 个内部 COMMIT 工具**，并保证每条证据带质量元数据。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `tools/specs/*.py` | **10 个 `ToolSpec` 声明**（9 数据 + 1 内部 `emit_artifact`） |
| `tools/impl/*.py` | 每个工具的：参数映射（业务参数 → 扶摇参数）、响应裁剪（只留必要字段）、Evidence 构造 |
| `data/quality.py` | 质量元数据：`as_of`（取响应 timestamp）、`staleness_ms`、`caliber`、`confidence` |
| `data/ratelimit.py` | 令牌桶 + `429/4001` 退避 |
| `data/cache.py` | TTL 缓存；降级时可返回「缓存 + 过期标注」 |
| `data/breaker.py` | 熔断器（连续失败 N 次 → 开路 T 秒） |
| `data/ifind_client.py` | iFinD MCP 客户端骨架，**作为降级备源**；不可用时返回 `NotEnabled` 并在 UI 显式标注 |

**24h 首批工具（9 个数据工具）**

| # | 工具名 | 数据域 | 扶摇接口 | phase |
|---|---|---|---|---|
| 1 | `search_ticker` | meta | `/api/meta/tickers/search`（前置） | EXPLORE |
| 2 | `get_price_snapshot` | price | `/api/a-share/prices/snapshot`、指数快照 | EXPLORE |
| 3 | `get_price_history` | price | `/api/a-share/prices/history`（日线，含复权口径） | EXPLORE |
| 4 | `get_index_constituents` | market | `/api/a-share-index/constituents/ths-stock-list` | EXPLORE |
| 5 | `get_valuation` | fundamental | `/api/a-share/valuations/snapshot` | EXPLORE |
| 6 | `get_limit_up_stats` | heat | `/api/a-share/special-data/limit-*-pool`、`limit-up-ladder` | EXPLORE |
| 7 | `get_hot_rank` | heat | `/api/a-share/special-data/hot-stock-list` | EXPLORE |
| 8 | `get_news_flash` | event | `/api/news/flash/list`、`news/topic/hot-list` | EXPLORE |
| 9 | `get_financials` | fundamental | `/api/a-share/financials/income-statements`（首版只接利润表） | EXPLORE |
| — | `emit_artifact` | 输出 | 内部 | **COMMIT** |

> **8 个覆盖用户旅程示例**：「看看最近半导体板块的**资金**和**消息面**」= `get_index_constituents` + `get_hot_rank`/`get_limit_up_stats` + `get_news_flash`，链路完整。
> **2026-10-19 契约数字放开为 9**：`get_financials` 原属「后置 4 工具」，因「收入架构变化」类研究目标必需而**提前落地**（用户已确认放开），入参契约见 [DATASOURCES.md](./DATASOURCES.md) §2.5。
> **后置 3 个（时间富余再加）**：`get_financial_indicators`、`get_trading_calendar`、`get_dragon_tiger`。
> **裁剪理由**：工具 schema 会全量进上下文；扶摇可调用能力远超 100 个，全塞进去直接爆预算。这是「上下文预算 → 工具裁剪」的真实工程权衡。

**配置项**：`IFIND_MCP_ENABLED`、`IFIND_MCP_ENDPOINT`、`IFIND_MCP_KEY`、`CACHE_TTL_S`、各工具 `timeout_ms` / `retry`（每工具配置块见 §3.1 M2 模板）

**依赖**：M0（Client）
**验收**：9 个工具均可用真实数据调用；断网/限流时能降级且产出 `degradation_notice`；每条 Evidence 必含 `source / as_of / unit / caliber`。

---

### M3 · 状态与恢复（State & Recovery）

**目标**：题目「检查点与恢复」+「审批 / 停止规则 / 成本时延」的直接对应物。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `harness/checkpoint.py` | 事件追加（单调 seq）、快照裁剪（N 个事件合并一次快照） |
| `harness/resume.py` | 事件重放重建内存态；定位未完成 Task；**幂等续跑**（查 `tool_calls.dedupe_key` 复用结果）；**启动扫描 `RUNNING` 状态的 Run 并自动恢复/标记为可人工恢复**（原 `runtime/recovery_scanner.py` 已并入此处） |
| `harness/state_machine.py` | Run/Task 状态迁移表；非法迁移拒绝；每次迁移落事件（`from/to/reason/trigger`） |
| `harness/hitl.py` | 审批挂起（冻结 Loop）、恢复、超时（默认 30min）过期 |
| `harness/stop_rules.py` | **8 条**：max_steps / max_tokens / max_cost / max_wall_time / loop_detect / no_progress / user_abort / guard_reject |
| `harness/budget.py` | token/成本/时延**实时计量与限额判定**；耗尽触发 `BudgetExhausted` |

**配置项**：`BUDGET_MAX_STEPS`、`BUDGET_MAX_TOKENS`、`BUDGET_MAX_COST_CNY`、`BUDGET_MAX_WALL_TIME_S`、`LOOP_DETECT_REPEAT`、`NO_PROGRESS_STEPS`

**接口契约**
```text
CheckpointStore.append(run_id, event) / load(run_id) -> EventStream
ResumeEngine.resume(run_id) -> RunResult
ResumeEngine.scan_orphans() -> list[run_id]          # 启动时扫描（取代 recovery_scanner）
HITLGate.request(run_id, type, payload) -> approval_id
HITLGate.resume(approval_id, decision)
StopRule.check(run, step) -> StopReason | None
BudgetManager.charge(usage) / remaining()
```

**依赖**：M0、M1（管线须预留 `suspend` 出口）、M2（需真实 `tool_calls` 才能证明幂等）
**验收**：执行至第 3 个 Task 中途 kill 进程 → 重启 → 前 2 个 Task 的工具调用**不重复发生**（用 `tool_calls` 记录数证明）；审批挂起期间刷新页面状态不丢；构造死循环 → 3 次重复内终止并给出命中规则名。

---

### M4 · 记忆与上下文（Memory & Context）

**目标**：题目「上下文压缩、记忆」的直接对应物。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `harness/context.py` | 上下文组装（系统提示 + 计划 + 历史 Step + 证据引用 + 工具 schema）；token 估算；阈值触发 |
| `harness/compactor.py` | 分层压缩：Step 摘要化、**Evidence 下沉只留引用**、召回截断；压缩事件上报 |
| `harness/memory.py` | **三层记忆读写 + 召回打分**（关键词命中 + 时间衰减，`recall()` 在本文件内；原 `harness/memory_recall.py` 已并入） |
| `harness/hooks/pre/context_inject.py` | 注入会话记忆 + 长期记忆（**该 Hook 的文件归属 M4**，M1 只保留注册位） |

**配置项**：`COMPACT_THRESHOLD_RATIO=0.6`、`MEMORY_RECALL_TOP_K=5`、`MEMORY_DECAY_HALFLIFE_H=72`

**接口契约**
```text
ContextManager.build(run) -> Messages
Compactor.compact(ctx) -> CompactResult{before_tokens, after_tokens, dropped_steps}
MemoryStore.write(scope, item) / recall(query, scope) -> Memory[]
```

**依赖**：M0、M1
**验收**：构造长会话（≥12 个 Step）触发压缩 → 压缩后 `Artifact` 引用的**全部 `evidence_id` 仍可在 DB 解析**（FR-04 核心验收）；新开 Thread 能召回历史偏好。

---

### M5 · 产物与动态 UI（Product & Dynamic UI）

**目标**：题目「线程、任务、证据、产物和动态 UI 映射」的直接对应物。

**后端组件**

| 组件 | 内部任务 |
|---|---|
| `store/repo/evidences.py` + `harness/hooks/post/evidence_persist.py` | **`EvidenceStore` 的落点**（架构 C17；`put(evidence)` / `resolve(ids)`） |
| `harness/artifact_builder.py` | Evidence → 三层 Artifact（facts/inferences/uncertainties）+ citations；**推断必须带切换条件** |
| `harness/ui_renderer.py` | 组件 props schema 校验；非法组件/非法 props/未实现组件 → `FallbackJsonCard` |
| `api/threads.py` / `api/runs.py` / `api/stream.py` | REST + **SSE（10 类事件）** |
| `api/approvals.py` | 审批列表与决定（**归属 M5**，逻辑调用 M3 的 `HITLGate`） |

**前端组件（Next.js）**

| 组件 | 内部任务 |
|---|---|
| `components/DynamicRenderer.tsx` | 组件白名单注册表 + props 校验 + 错误边界降级 |
| `components/blocks/*.tsx` | **24h 实现 7 个**：`plan_card` / `clarify_question` / `evidence_card` / `evidence_table` / `conclusion_card` / `approval_card` / `degradation_notice` |
| `components/EvidencePanel.tsx` | 证据列表 + 详情（来源/时点/单位/口径/原始响应） |
| `components/ArtifactView.tsx` | 三层分区渲染（配色与标签区分） |
| `components/RunStatus.tsx` | Run 状态、成本、暂停/恢复/终止 |
| `hooks/useRunStream.ts` | SSE 消费 + 增量 UIBlock 插入 + 断线重连 |
| `pages/console` | 研究控制台（输入目标 → 计划 → 证据 → 产物 全流程） |

**渲染规则（白名单 12 个，实现 7 个）**
```text
可渲染条件 = 组件名 ∈ 白名单(12)  ∩  前端已实现(7)
否则 → FallbackJsonCard + Trace 记录 render_status=failed
```
> **后置 5 个组件**：`metric_block`、`series_chart`、`distribution_chart`、`citation_list`、`trace_ref`。
> 白名单仍是**契约全集（12 个）**，架构 §11.2 **不改**；24h 只收窄"已实现集"。

**配置项**：`NEXT_PUBLIC_API_BASE`、`UI_BLOCK_MAX=200`、`RENDER_STRICT=true`

**依赖**：M1（证据流）、M3（审批接口）
**验收**：完整演示一次研究；7 个组件渲染成功；人为注入非法 props → 显示降级卡而非白屏；无引用的 FACT 卡被渲染层拦截。

---

### M6 · 可观测与合规（Observability & Compliance）

**目标**：题目「可观测性」+ 统一规则「合规、可追溯」。

**组件与内部任务**

| 组件 | 内部任务 |
|---|---|
| `observ/tracer.py` | `span()` 上下文管理器；Run→Plan→Task→Loop→LLM/Tool/Compact 树；落 `traces` 表（**埋点已在 M1 同步完成，本模块只做查询与展示**） |
| `observ/cost.py` | **只做事后计价与汇总查询**（`/api/runs/{id}/cost`）；实时限额判定在 `harness/budget.py`，二者职责不重叠 |
| `observ/sanitize.py` | 日志与 Trace 脱敏（Key / 隐私） |
| `compliance/guard.py` | 三类检查：①预测/承诺/建议（关键词 + 结构校验）②无引用 FACT 拦截 ③推断无切换条件拦截；**红线规则表作为本模块内置数据常量**（原 `compliance/rules.py` 已并入）+ `GuardVerdict` 枚举 |
| `api/traces.py` | Span 树 / 成本 / 合规报告接口 |

**配置项**：`TRACE_ENABLED=true`、`GUARD_STRICT=true`、`SANITIZE_PATTERNS`

**接口契约**
```text
Tracer.span(name, **attrs) -> ContextManager
CostReporter.summary(run_id) -> CostSummary        # 取代 observ/cost 的独立调用面
Guard.check(artifact) -> GuardVerdict
```

**依赖**：M1（埋点）、M3（Budget 计量）、M5（产物）
**验收**：任一结论能在 Trace 中定位到工具调用；注入「建议买入某股」→ Guard 拦截并记录；日志中不出现 API Key。

---

### M7 · 交付（Delivery）

**目标**：题目 5 条提交要求的落地。

| 交付物 | 内部任务 |
|---|---|
| 部署 | `docker-compose.yml`（后端 + 前端 + volume）；**公网可访问 URL**（空壳已在 G0 演练过） |
| `README.md` | 启动方式、环境变量、产品选择、AI 角色、数据来源、已知边界、未做事项 |
| `docs/TEST_PLAN.md` | 三类测试说明：主链路 / 数据与接口异常 / 合规边界；含可复现步骤 |
| `tests/` | pytest 覆盖上述三类；关键用例带「断言数字」 |
| `docs/AI_USAGE_LOG.md` | 使用了哪些 AI 工具、参与环节、**人工修正了哪些错误结果**（真实记录） |
| 演示视频（可选） | 60–180s：NL 目标 → 计划审批 → 证据流 → 降级演示 → 恢复演示 → 产物 |
| 仓库 | `.gitignore`（含 `.env`）、`.env.example`、LICENSE |

**依赖**：M0–M6 全部
**验收**：评委可打开 URL 实际操作；README 六要素齐全；测试文档可照着复现；视频完整走通核心任务。

---

## 3.1 M2 工具配置模板（每个工具统一）

```yaml
tool: get_price_snapshot
args_mapping:  { thscodes: list[str] }        # 业务参数
fuyao:         /api/a-share/prices/snapshot
keep_fields:   [thscode, last_price, open_price, high_price, low_price,
                volume, price_change_ratio_pct, currency]
evidence:      { unit: CNY, caliber: 快照价, source: fuyao }
fallback:      ifind（不可用/无 schema 时直接降级）
timeout_ms:    8000
retry:         { times: 2, backoff: exponential }
```

---

## 4. 依赖关系

### 4.1 模块依赖 DAG（**已修正**）

```text
        M0 地基（契约/模型/DB/Client/Mock）
         │
         ├──────────────┬──────────────┐
         ▼              ▼              ▼
       M2 数据工具     M1 内核        M4 记忆上下文
         │              │              │
         └──────┬───────┘              │
                ▼                      │
              M3 状态与恢复 ◄──────────┘   (M3 依赖 M4 的 ContextManager 接口签名)
                │
                ▼
              M5 产物与动态 UI
                │
                ▼
              M6 可观测与合规
                │
                ▼
              M7 交付
```

> **修正点**：v1 的图把 `M4 → M3` 画成前置关系、且漏画 M2→M1。v2 明确：
> - `M2 → M1`：M1 的运行期需要 M2 的真实工具才能端到端（**契约阶段二者并行**）。
> - `M4 → M3`：M3 只依赖 M4 的 `ContextManager` **接口签名**，不依赖其实现完成。

### 4.2 组件级关键路径（**10 跳**，v1 写"13 跳"为误记）

```text
domain/models.py
  → data/fuyao_client.py
    → tools/specs/* + tools/impl/*
      → harness/tool_executor.py
        → harness/agent_loop.py
          → harness/pipeline.py
            → harness/checkpoint.py
              → harness/resume.py
                → api/runs.py + api/stream.py
                  → DynamicRenderer.tsx + blocks/*
                    → RunStatus.tsx / ArtifactView.tsx
```
**模块级关键路径（7 跳）**：`M0 → M2 → M1 → M3 → M5 → M6 → M7`

### 4.3 并行/串行说明（24h 版）

| 关系 | 说明 |
|---|---|
| M1 ∥ M2 | **契约并行**：M0 冻结 `ToolSpec`/`ToolResult`/`Step` 后即可各自开工；**联调串行**：M1 端到端跑通必须等 M2 交出 ≥2 个真实工具 |
| M4 ∥ M5 前半 | M5 前端可先用 Mock SSE 事件流开发，不必等 M4 |
| M6 ∥ M5 后半 | Tracer 埋点在 M1 完成，M6 只做查询与展示 |

> **24h 下不做多 Agent 并行**：契约对齐成本（沟通/返工）高于并行收益，改为**单人 + AI 辅助、模块串行**。

---

## 5. 开发方式与分工（24h 版）

### 5.1 人机分工

| 角色 | 承担者 | 具体职责 | 不可委托的原因 |
|---|---|---|---|
| 产品与架构决策人 | 候选人 | 定范围、批准契约、决定可砍项 | 需要人对题目意图的判断 |
| 代码实现者 | AI 编码助手 | 按模块实现代码、写测试、重构 | — |
| 代码评审与纠错人 | 候选人 | 修正 AI 的错误实现、把控质量 | AI 会犯结构性错误 |
| 金融口径校验人 | 候选人 | 校验指标口径、复权选择、单位换算、来源标注 | AI 无法判断金融实务语义 |
| 合规审查人 | 候选人 | 确认红线覆盖完整、无越界表述 | 合规责任必须由人承担 |
| 验收与演示人 | 候选人 | 端到端验收、录视频、写 AI 使用记录 | 需对产品体验负责 |

### 5.2 协作纪律（避免返工）

1. **M0 冻结前，任何模块不得开工**（契约变更 = 全员返工）。
2. **数据库表由 M0 独占**，其它模块只读 + 提变更请求。
3. **SSE 事件契约（10 类）先冻结**，前端按契约用 Mock 流开发。
4. **Tracer 埋点由 M1 在写 Loop 时同步完成**，M6 只消费，不回头改 Loop。

---

## 6. 风险登记册（**已按实测结果更新**）

| # | 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|---|
| R-1 | 扶摇 API Key 不可用 | ~~中~~ **已消除** | 高 | ✅ 2026-10-19 已实测（`code=0`，`600519.SH` snapshot 取到真实行情） |
| R-2 | iFinD 工具清单不公开、密钥不可得 | ~~**高**~~ **已消除** | 中 | ✅ 已实测 36 工具可用。**仍不承担核心链路**，理由是返回自然语言文本、无强类型 schema（工程取舍） |
| R-3 | 动态 UI 开发超时 | **高** | 中 | 白名单 12 → **24h 只实现 7 个**；未实现组件自动降级为 `FallbackJsonCard` |
| R-4 | 上下文预算爆掉导致 Run 中断 | 中 | 中 | 工具裁剪至 8 个；上下文占满 60% 即触发压缩 |
| R-5 | 恢复逻辑写错导致重复副作用 | 中 | **高** | `tool_calls.dedupe_key` UNIQUE 必须在 M0 落地；非幂等工具标记不自动重放 |
| R-6 | 模型结构化输出不稳定（JSON 解析失败） | 中 | 中 | `json_schema` + Pydantic 二次校验 + 重试 2 次 + 降级为纯文本解析 |
| R-7 | 合规拦截误伤正常输出 | 低 | 中 | Guard 分级：硬红线必拦，灰色表述改为「加免责标注」 |
| R-8 | Tracer 埋点滞后导致可观测性做不出来 | 中 | 中 | **强制在 M1 阶段埋点**（§2 时序硬约束 1） |
| R-9 | 部署环境（公网 URL）不通 | 低 | **高** | **G0（T3）就完成一次空壳部署演练**，不留到最后 |
| **R-10** | **24h 内 8 个模块全做完不现实** | **高** | **高** | 按 §8 三档可砍项逐级降级；**题目①②③的机制必须保留，只砍视觉与覆盖广度** |

---

## 7. （已并入 §8）

---

## 8. 可砍项优先级（保命清单 · 24h 版）

> 触发条件：任一里程碑超时 **> 1h**。**从档 1 往上依次砍**。

| 档 | 可砍项 | 砍掉后的降级形态 | 对评分的影响 |
|---|---|---|---|
| **3（最先砍）** | 图表与增强组件（`series_chart` / `distribution_chart` / `metric_block` / `citation_list` / `trace_ref`） | 全部降级为 `evidence_table` + `FallbackJsonCard` | 低（视觉分） |
| **2** | 多 provider failover | 降级为「单 provider + 重试」，**保留 `ModelRouter` 接口与配置位** | 中（可讲设计） |
| **1（最后砍）** | 后置 3 个工具（`get_financial_indicators` / `get_trading_calendar` / `get_dragon_tiger`）与 Trace Viewer 可视化 | 工具 9 → 9（本就不做）；Trace 降级为 `/api/runs/{id}/traces` JSON 导出 | 中 |

**绝对不可砍**（砍掉即不满足题目硬性要求）：
- Tool Registry 与 AgentLoop（题目 ①）
- Checkpoint / Resume / HITL（题目 ①⑥）
- ContextCompactor 与记忆（题目 ①）
- StopRule / Budget / Trace 落库（题目 ②）
- 证据链与三层产物（题目 ③ + 统一规则）
- Guard 合规拦截（统一规则）
- 部署 URL / README / 测试说明 / AI 使用记录（题目 ④）

> **v1 可砍项「长期记忆语义召回」已作废**：架构 §8 本就只做关键词召回，语义召回为「方案预留、不落地」，砍它等于什么都没砍。

---

## 9. 交付物清单对照

| 题目要求 | 交付物 | 责任 | 状态 |
|---|---|---|---|
| 可操作 Web URL | 公网部署 | M7 | 待办（G0 先出空壳 URL） |
| 源代码仓库 | Git 仓库（含 `.env.example`、`.gitignore`） | M7 | 待办 |
| README（六要素） | `README.md` | M7 | 待办 |
| AI 使用与验证记录 | `docs/AI_USAGE_LOG.md` | 你 + M7 | 待办 |
| 测试说明（三类场景） | `docs/TEST_PLAN.md` + `tests/` | 全模块 | 待办 |
| 演示视频（60–180s，可选） | 录屏 | 你 | 待办 |
| 设计文档 | 本目录 4 份 | — | ✅ |

---

## 10. 契约数字唯一来源（一致性自检）

> **任何文档出现下列数字时，必须与本表一致**。本表是解冲突的唯一依据。

| 数字 | 值 | 唯一来源 |
|---|---|---|
| 做题时间 | **24h** | 题目原文 `投资XBuddy.md` |
| 模块数 | **8**（M0–M7） | 本文件 §3 |
| 里程碑 | **6**（G0–G5） | 本文件 §2 |
| 关键路径 | **7 模块 / 10 组件跳** | 本文件 §4.1 / §4.2 |
| DB 表 | **15** | 架构 §4.1（13 实体）+ `llm_calls`、`run_metrics` |
| 领域模型（Pydantic） | **13** | 架构 §4.1 的 13 个实体 |
| 领域枚举 | **5** | 架构 §5 状态机 |
| checkpoint 事件 | **26 类** | 架构 §4.2（24）+ `RunPaused`/`RunResumed` |
| 数据工具 | **9** | 本文件 §3 M2（2026-10-19 由 8 放开为 9，补入 `get_financials`） |
| 内部工具 | **1**（`emit_artifact`） | 本文件 §3 M2 |
| 工具注册项合计 | **10** = 9 + 1 | = 上行两行之和 |
| UI 组件白名单 | **12** | 架构 §11.2 |
| UI 组件 24h 实现 | **7** | 本文件 §3 M5 |
| SSE 事件 | **10 类** | 架构 §13.1 |
| 停止规则 | **8 条** | PRD FR-08 |
| AI 角色 | **6 个** | PRD §5 |
| 领域实体 | **7 个** | PRD §3.1 |
| 功能需求 | **16 条**（FR-01…FR-16） | PRD §4 |
| 环境变量 | 见架构 §14.1（**已补齐 9 项** = M4–M6 后端 8 + 前端 1） | 架构 §14.1 |

---

## 11. 废弃与取代清单（Deprecated）

> 按要求：**冲突项一律显式标注废弃，不静默删除**，便于追溯"当时为什么这么定"。

| 废弃对象 | 原位置 | 取代为 | 废弃理由 |
|---|---|---|---|
| 48h 总时长 | 本文件 v1 §1 | 24h | 与题目原文不符 |
| T0–T48 时间盒 | 本文件 v1 §2 | T0–T24 | 同上 |
| 「总时长 48h（统一规则口径）」 | 本文件 v1 §1 | 删除该归因 | **统一规则写的是 24h**，归因错误 |
| 「48h 内的取舍」 | 架构 §2.1 标题 | 「24h 内的取舍」 | 同上 |
| ADR-6「48h 部署成本」 | 架构 §16 | 「24h 部署成本」 | 同上 |
| 「12 个工具」（作数据工具口径） | 架构 §9.2 标题 | **12 数据工具 + 1 内部（=13 注册项）**；24h 首做 **9 + 1**（2026-10-19 由 8 + 1 放开） | 标题与表格行数矛盾 |
| 「22 类 checkpoint 事件」 | 本文件 v1 §3 M0 | 26 类 | 与架构 §4.2 不符 |
| 「11 类 SSE 事件」 | 本文件 v1 §3 M5 | 10 类 | 与架构 §13.1 不符 |
| 「13 跳关键路径」 | 本文件 v1 §4.2 | 10 跳 | 误记 |
| 关键路径漏 M6 | 本文件 v1 §1 | 补 M6 | M6 承载题目② |
| 「9 个 Pydantic 模型」 | 本文件 v1 §3 M0 | 13 个 | 与架构 13 实体不符 |
| 「8 个 Owner 并行」 | 本文件 v1 §5.2 | 单人 + AI 串行 | 24h 下并行成本更高 |
| `harness/memory_recall.py`（文件） | 本文件 v1 §3 M4 | 并入 `harness/memory.py` | 架构 C8 本就含 `recall()` |
| `runtime/recovery_scanner.py`（文件） | 本文件 v1 §3 M3 | 并入 `harness/resume.py` 的 `scan_orphans()` | 架构 §6.1 已规定由 ResumeEngine 扫描 |
| `compliance/rules.py`（文件） | 本文件 v1 §3 M6 | 并入 `compliance/guard.py` 的数据常量 | 规则表不构成独立模块 |
| 「长期记忆语义召回」作为可砍项 | 本文件 v1 §7 | **作废**（不是可砍项） | 架构 §8 本就未做语义召回 |
| 「iFinD 未启用 / 密钥不可得」 | 架构 §14.1、PRD §8.5、本文件 v1 R-2 | **已实测可用，仅作降级备源** | 实测已推翻前提 |
| 「五元实体 + 一个执行单元」 | PRD §3.1 标题 | 「**7 个领域实体**」 | 标题与表内 7 行不符 |
| 架构 §1.1 L1 表清单（列 11 张） | 架构 §1.1 | 15 张 | 漏 `tool_calls`、`approvals`、`llm_calls`、`run_metrics` |
| 计划 §9「下一步」与 M0 重复的条目 | 本文件 v1 §9 | 并入 §12 | 重复描述 |

---

## 12. 下一步（开工顺序 · 24h 版）

> 原则：**先枚举 → 再模型 → 再事件 → 再配置 → 再存储 → 再取数 → 再 Mock → 最后空壳**。

1. **环境**：`conda activate TongHua`（Python 3.11.17）→ `python -m pip install fastapi "pydantic>=2" pydantic-settings sqlalchemy alembic httpx pytest`（**不用 uv**）
2. **工程骨架** + `domain/enums.py`（5 个枚举）
3. **`domain/models.py`（13 个模型，产出即冻结）**
4. **`domain/events.py`（26 类）**
5. **`config/settings.py`**（`FUYAO_API_KEY` 可选，缺失则降级 Mock）
6. **`store/db.py` + `schema.py` + Alembic 首版**（15 张表 + 2 条 UNIQUE）
7. **`store/repo/*.py`**（先做 6 张）
8. **`data/errors.py` → `fuyao_client.py` → `mock/*.json` → `mock_client.py`**
9. **`web/skeleton`**（pnpm）+ **一次公网部署演练** → **G0**
10. **`tests/test_m0_*.py`**：`pytest -k m0` 全绿，含「真实 `600519.SH` snapshot 字段断言」+「断网 Mock 同构断言」

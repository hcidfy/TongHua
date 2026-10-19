# 投资 X Buddy · 技术架构设计

> 配套文档：[01_PRD.md](./01_PRD.md) ｜ [03_BUILD_PLAN.md](./03_BUILD_PLAN.md) ｜ [DATASOURCES.md](./DATASOURCES.md)
> 文档状态：设计稿（未开始编码）

---

## 1. 架构总览

### 1.1 分层视图

```text
┌─────────────────────────────────────────────────────────────────────┐
│ L5  产品表现层  Next.js + TypeScript                                 │
│     Research Console │ Evidence Panel │ Artifact Viewer │ Trace View │
│     Approval Card    │ Run Status     │ 动态UI 渲染器（组件白名单）   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ REST（控制面）+ SSE（数据面，单向流）
┌───────────────────────────────▼─────────────────────────────────────┐
│ L4  API 层  FastAPI                                                  │
│     Thread/Run API │ Stream API │ Approval API │ Trace API │ Health  │
└───────────────────────────────┬─────────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────────┐
│ L3  Agent Harness 内核（自研，本项目的核心展示物）                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 运行管线：  PreHook 链 → AgentLoop → PostHook 链（无条件执行） │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  SessionManager(Lane) │ Planner │ ToolRegistry │ ToolExecutor        │
│  ContextManager │ Compactor │ MemoryStore │ CheckpointStore          │
│  ResumeEngine │ HITLGate │ StopRule │ BudgetManager │ ModelRouter    │
│  Tracer │ Guard │ EvidenceStore │ ArtifactBuilder │ UIRenderer       │
└───────────────────────────────┬─────────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────────┐
│ L2  数据接入层                                                       │
│  FuyaoClient（REST，X-api-key，信封解析，错误码映射）                 │
│  IFindClient（MCP，已实测 36 工具；仅作降级备源，不可用则显式标注）   │
│  Cache │ RateLimiter │ RetryPolicy │ CircuitBreaker │ QualityMeta    │
└───────────────────────────────┬─────────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────────┐
│ L1  存储层  SQLite（开发）/ PostgreSQL（可选）                        │
│  threads │ runs │ tasks │ steps │ tool_calls │ evidences │ artifacts  │
│  ui_blocks │ checkpoint_events │ memories │ traces │ approvals       │
│  tool_registry │ llm_calls │ run_metrics      ← 共 15 张            │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 设计主线：**把「运行时内核」在 Python 里重造一遍**

整套内核沿用同一条架构范式（这是本项目的技术叙事主线）：

| 范式 | 在本项目的落地 |
|---|---|
| **PreHook → Loop → PostHook 管线** | 循环内核里**零业务逻辑**；业务能力全部以 Hook / Tool 可插拔接入 |
| **post 链无条件执行** | LLM 全挂、AgentLoop 被中断时，**证据落库、Trace 落库、记忆写入**等副作用依然发生 |
| **会话级并发编排（Lane）** | 同一 Thread 串行（避免上下文互相覆盖），不同 Thread 完全并行 |
| **工具按 phase 声明** | `EXPLORE`（探索取数）/ `COMMIT`（产出即终态），循环只认 phase、不认业务类型 |
| **多 provider 路由 + 健康降级** | ModelRouter：超时 → 切换 → 熔断 → 降级研判 |
| **全链路 Trace** | Run 下的 Span 树，每个结论可回溯到具体工具调用 |

> **为什么不用 LangGraph 直接实现**：题目要考察的是「Agent Harness 的运行机制」。用框架实现会把内核藏起来，反而弱化信号。因此**借鉴其 checkpoint / interrupt 范式，自研实现**，让内核完全可见、可讲、可改。

---

## 2. 技术栈选型与理由

| 层 | 选型 | 理由 |
|---|---|---|
| 后端语言 | **Python 3.11** | Agent 生态最成熟；异步与数据处理顺手；与 LLM SDK 无摩擦 |
| Web 框架 | **FastAPI** | 原生 async、自带 OpenAPI、SSE 支持好 |
| 契约层 | **Pydantic v2** | 一份 schema 同时用于：工具契约 / 产物契约 / 持久化模型 / API 校验——**契约单点定义**是控制幻觉的关键 |
| ORM | **SQLAlchemy 2.0 + Alembic** | 类型化 ORM；迁移可控 |
| 数据库 | **SQLite（开发）/ PostgreSQL（部署）** | 单文件零运维起步，接口不变可平滑切换 |
| HTTP 客户端 | **httpx（async）** | 支持超时/连接池/重试钩子，便于实现熔断与降级 |
| 流式通道 | **SSE（sse-starlette）** | 单向推送够用，比 WebSocket 简单；断线重连天然友好 |
| LLM 接入 | **OpenAI 兼容协议统一封装** | 多 provider（DeepSeek / 通义 / 智谱 / OpenAI / 本地 vLLM）一套代码 |
| 前端框架 | **Next.js 15 (App Router) + TypeScript** | 流式渲染友好；部署简单 |
| UI 组件 | **Tailwind + shadcn/ui** | 快速成型，视觉可控 |
| 图表 | **ECharts** | 金融图表（K线、资金曲线）开箱可用 |
| 状态管理 | **TanStack Query + Zustand** | 服务端状态与本地 UI 状态分离 |
| 部署 | **Docker Compose（单机）** | 一键起后端 + 前端；也可前后端分离部署 |

### 2.1 明确不引入（24h 内的取舍）

| 不引入 | 原因 | 替代 |
|---|---|---|
| Redis | 单机单用户无分布式需求 | 进程内缓存 + DB |
| RocketMQ / 消息队列 | 无跨服务异步需求 | 进程内事件总线（asyncio） |
| 向量数据库 | 部署成本高 | SQLite + 关键词检索起步，接口预留可换 pgvector |
| Kubernetes / 微服务 | 明显过度设计 | 单体应用（模块化内部边界） |

> 说明：这些是**刻意不做**，并且会在 README 的「未做事项」中写明，而不是能力盲区。

---

## 3. Harness 内核：组件与职责

### 3.1 运行管线（核心）

```text
Run 生命周期
│
├─ [1] PreHook 链（顺序执行，可短路）
│      ① ContextInjectHook   —— 组装系统提示、注入 Thread 记忆、检索长期记忆
│      ② BudgetCheckHook     —— 检查剩余 token/成本/步数预算，不足则拒绝启动
│      ③ GuardPreHook        —— 目标合规前置检查（含越界诉求直接拦截）
│      ④ PlanHook            —— 无计划则调用 Planner 生成 Task DAG
│
├─ [2] 计划确认（若配置为需审批 → HITLGate 挂起）
│
├─ [3] 主循环（按 Task DAG 拓扑序推进）
│      for task in ready_tasks:
│          ┌──────────── AgentLoop（ReAct） ────────────┐
│          │  Think(LLM) → Act(ToolExecutor) → Observe  │
│          │  每轮：记录 Step / 记 Span / 记成本          │
│          │  出口：任务完成 / 预算耗尽 / 需审批 / 停止规则 │
│          └────────────────────────────────────────────┘
│          → PostHook 链（无条件执行）
│
├─ [4] PostHook 链（无条件执行，失败也执行）
│      ① EvidencePersistHook —— 证据落库 + 质量元数据
│      ② TraceFlushHook      —— Span 落库
│      ③ MemoryWriteHook     —— 会话记忆/长期记忆写入
│      ④ CostSettleHook      —— 成本结算
│      ⑤ GuardPostHook       —— 产物发布前合规安检
│
└─ [5] 收口
       Synthesize(证据 → 三层 Artifact) → UIRender → Checkpoint → 推送完成事件
```

**设计要点**：`[4] PostHook` 无条件执行是这套架构的关键——它保证了「LLM 全挂 / 用户中断 / 合规拦截」三种失败路径下，**证据与审计信息依然完整**，这也是「不得静默失败」的机制保障。

### 3.2 组件清单

| # | 组件 | 职责 | 关键接口 |
|---|---|---|---|
| C1 | **SessionManager** | Thread/Run 生命周期；同 Thread 串行（Lane 锁）、跨 Thread 并行 | `create_run`, `acquire_lane`, `release_lane` |
| C2 | **Planner** | 目标 → Task DAG；只允许使用已注册工具 | `plan(goal, tools) -> TaskDAG` |
| C3 | **AgentLoop** | ReAct 循环；产出 Step | `iterate(task, ctx) -> Step` |
| C4 | **ToolRegistry** | 工具注册与查询；声明 schema/phase/幂等/成本/权限 | `register(spec)`, `list()`, `get(name)` |
| C5 | **ToolExecutor** | 调用执行：参数校验、限流、重试、超时、熔断、结果裁剪、质量元数据注入 | `invoke(call) -> ToolResult` |
| C6 | **ContextManager** | 上下文组装与 token 计量；触发压缩 | `build(run) -> Messages` |
| C7 | **Compactor** | 压缩策略：Step 摘要化 + Evidence 下沉（保留引用） | `compact(ctx) -> CompactResult` |
| C8 | **MemoryStore** | 三层记忆读写 | `write_working/session/long`, `recall(query)` |
| C9 | **CheckpointStore** | 事件溯源持久化 | `append(event)`, `load(run_id)` |
| C10 | **ResumeEngine** | 从事件流重建状态、续跑 | `resume(run_id)` |
| C11 | **HITLGate** | 审批请求挂起 / 恢复（冻结 Loop） | `request(type, payload)`, `resume(approval_id)` |
| C12 | **StopRule** | 停止条件判定 | `check(run, step) -> StopReason\|None` |
| C13 | **BudgetManager** | token/成本/时延计量与限额 | `charge(usage)`, `remaining()` |
| C14 | **ModelRouter** | 多 provider 路由、超时、熔断、failover、降级 | `complete(messages, schema) -> LLMResult` |
| C15 | **Tracer** | Span 树构建与落库 | `span(name, **attrs)` 上下文管理器 |
| C16 | **Guard** | 合规安检（预测/建议/无引用事实） | `check(artifact) -> GuardVerdict` |
| C17 | **EvidenceStore** | 证据存取与引用解析 | `put(evidence)`, `resolve(ids)` |
| C18 | **ArtifactBuilder** | 证据 → 三层产物 | `build(run) -> Artifact` |
| C19 | **UIRenderer** | UIBlock 构造与组件白名单校验 | `render(source) -> UIBlock[]` |

---

## 4. 数据模型

### 4.1 核心表（**15 张** = 13 实体表 + `llm_calls` + `run_metrics`）

```text
threads(id, title, goal, status, created_at, updated_at)

runs(id, thread_id, status, parent_run_id, resume_from_event_id,
     stop_reason, cost_total, tokens_total, started_at, ended_at, error)

tasks(id, run_id, seq, title, intent, status, deps_json,
      attempts, evidence_count, degradation_reason)

steps(id, task_id, seq, type[THINK|ACT|OBSERVE], payload_json,
      llm_span_id, tool_call_id, created_at)

tool_calls(id, run_id, task_id, tool_name, args_json, status,
           attempts, latency_ms, error_code, error_message,
           idempotent, dedupe_key, created_at)
  └─ UNIQUE(dedupe_key)  ← 恢复时的幂等依据

evidences(id, run_id, task_id, tool_call_id, source, interface,
          as_of, fetched_at, staleness_ms, unit, caliber,
          value_json, raw_ref, confidence, degrade_reason, created_at)

artifacts(id, run_id, version, facts_json, inferences_json,
          uncertainties_json, citations_json, created_at)

ui_blocks(id, run_id, seq, source_type[PLAN|EVIDENCE|ARTIFACT|SYSTEM],
          component, props_json, refs_json, render_status, created_at)

checkpoint_events(id, run_id, seq, event_type, payload_json, created_at)
  └─ UNIQUE(run_id, seq)   ← 事件溯源的单调序列

memories(id, scope[WORKING|SESSION|LONG], thread_id, run_id,
         kind, content, embedding_ref, created_at)

traces(id, run_id, span_id, parent_span_id, name, attrs_json,
       started_at, duration_ms, status)

approvals(id, run_id, type, payload_json, status[PENDING|GRANTED|REJECTED|EXPIRED],
          requested_at, decided_at, decided_by)

tool_registry(name PRIMARY KEY, description, schema_json, domain,
              phase, idempotent, timeout_ms, est_cost, permission_level,
              enabled, version)

llm_calls(id, run_id, task_id, step_id, provider, model,
          prompt_tokens, completion_tokens, total_tokens, cost,
          latency_ms, status, error_code, request_ref, created_at)

run_metrics(id, run_id, metric_name, metric_value, unit, tags_json, created_at)
```

> 13 个实体表 + `llm_calls` + `run_metrics` = **15 张**。
> `runs.cost_total` / `runs.tokens_total` 的**唯一数据来源是 `llm_calls`**（不靠内存累计）；`run_metrics` 供成本与质量看板使用。

### 4.2 事件类型（事件溯源，**26 类**）

```text
RunStarted / PlanGenerated / PlanApproved
TaskStarted / TaskCompleted / TaskFailed / TaskDegraded / TaskSkipped
LoopIterationStarted
ToolInvocationStarted / ToolInvocationSucceeded / ToolInvocationFailed
EvidenceRecorded
ContextCompacted
ApprovalRequested / ApprovalGranted / ApprovalRejected
BudgetExhausted / StopRuleHit
ArtifactBuilt / GuardRejected
RunPaused / RunResumed
RunCompleted / RunFailed / RunStopped                    ← 共计 26 类
```

> `RunPaused` / `RunResumed` 为「可暂停 / 可恢复」硬需求的事件载体（§6.1 已用 `RunPaused`）。24h 首批实现首选 8 数据工具 + 1 内部工具，见 [03_BUILD_PLAN.md](./03_BUILD_PLAN.md) §3 M2；**2026-10-19 数据工具放开为 9**（补入 `get_financials`，见 §9.2）。

> 恢复算法：`load(run_id)` → 按 seq 重放 → 重建内存态 → 定位最后一个**未完成**的 Task → 从该 Task 起点续跑（已完成的 Task 直接跳过）。

---

## 5. 状态机设计

### 5.1 Run 状态机

```text
CREATED ──> PLANNING ──> EXECUTING ──┬─> SYNTHESIZING ──> COMPLETED
                │            │       │
                │            │       └─> STOPPED（停止规则/用户中断）
                │            │
                │            └─> AWAITING_APPROVAL ──┐
                │                     ▲              │ (granted)
                │                     └──────────────┘
                └─> FAILED（不可恢复错误）
```

### 5.2 Task 状态机

```text
PENDING ──> READY ──> RUNNING ──┬─> DONE
   ▲                            ├─> FAILED
   │ (依赖就绪)                  ├─> DEGRADED  （数据源异常，部分产出）
   └────────────────────────────┴─> SKIPPED   （前置失败/被用户删除）
```

### 5.3 迁移规则（可观测）

每次迁移写入 `checkpoint_events`，payload 含 `from / to / reason / trigger`。**这是「为什么没触发/为什么停」的答案来源**。

---

## 6. 中断与恢复机制

### 6.1 四类中断

| 中断类型 | 触发 | 恢复方式 |
|---|---|---|
| 进程崩溃 | 异常退出 | 重启后 `ResumeEngine` 扫 `RUNNING` 状态的 Run，重放事件续跑 |
| 审批挂起 | HITLGate | 轮询/长连接等待用户决定，`GRANTED` 后从挂起点继续 |
| 用户暂停 | 前端主动调用 | 记录 `RunPaused` 事件，状态置 `AWAITING_APPROVAL` |
| 预算耗尽 | StopRule | 产出现有成果为 Artifact，状态 `STOPPED`，可人工追加预算后新开 Run |

### 6.2 幂等保障（恢复不重跑的关键）

三层去重：

1. **`tool_calls.dedupe_key`**：`hash(run_id + task_id + tool_name + args)` 唯一约束 → 恢复时先查是否存在成功记录，存在直接复用结果
2. **非幂等工具**（如发通知）标记 `idempotent=false`，恢复时**不自动重放**，而是标记为「需人工确认」
3. **事件序列号**：恢复以 `(run_id, seq)` 为界，只处理 seq 之后的事件

---

## 7. 上下文压缩策略

### 7.1 触发

`ContextManager.build()` 估算 token；超过 **预算阈值（默认 60%）** 触发 `Compactor`。

### 7.2 分层压缩（关键：证据不能丢）

| 层级 | 处理方式 | 保留 |
|---|---|---|
| 系统提示 | 不压缩 | 全量 |
| 计划（Task DAG） | 不压缩，仅折叠已完成任务标题 | 结构 |
| 历史 Step | **摘要化**：N 个 Step 合并为一段摘要 | 结论要点 |
| **Evidence** | **下沉到 DB**，上下文只留 `evidence_id + 一行摘要` | **引用** |
| 长期记忆召回 | 按相关性截断 | Top-K |

### 7.3 正确性保证

压缩后仍必须能回答：`Artifact 引用的每个 evidence_id` → **在 DB 中可解析**。测试用例即验证这一点（FR-04 验收）。

压缩事件写入 Trace（`before_tokens / after_tokens / dropped_steps`），压缩决策可见。

---

## 8. 记忆分层

| 层 | 生命周期 | 存储 | 用途 |
|---|---|---|---|
| **工作记忆** | 单次 Run | 内存 | 当前 Loop 的 Step 序列（会被压缩） |
| **会话记忆** | Thread 内 | DB | 已确认结论、证据索引、用户修改过的计划 |
| **长期记忆** | 跨 Thread | DB（预留向量） | 用户偏好（关注维度/常用标的/风险偏好）、历史研究主题 |

**召回时机**：`ContextInjectHook` 注入会话记忆；Planner 生成计划前额外召回长期记忆。

**面试加分点**：长期记忆默认用**关键词 + 时间衰减**打分（避免引入向量库），接口 `MemoryStore.recall(query)` 保持稳定，后续可无缝替换为 pgvector 语义检索——**方案预留、不落地**。

---

## 9. 工具层设计

### 9.1 工具声明契约

```python
ToolSpec(
    name="get_price_snapshot",
    description="获取 A 股/指数的实时行情快照（价格、涨跌幅、成交量）",
    schema={...},              # JSON Schema，Pydantic 生成
    domain="price",            # price | fundamental | event | calendar | heat | market
    phase="EXPLORE",           # EXPLORE | COMMIT
    idempotent=True,
    timeout_ms=8000,
    est_cost=0.0,              # 数据类工具无 LLM 成本
    permission_level="READ",   # READ | EXPENSIVE | SENSITIVE
    backing={"source": "fuyao", "path": "/api/a-share/prices/snapshot",
             "fallback": {"source": "ifind", "tool": "..."}}
)
```

### 9.2 工具集（**12 个数据工具 + 1 个内部 `emit_artifact` = 13 个注册项**）

| 工具名 | 数据域 | 后端接口（扶摇） | phase |
|---|---|---|---|
| `search_ticker` | meta | `/api/meta/...`（标的检索，前置） | EXPLORE |
| `get_price_snapshot` | price | `/api/a-share/prices/snapshot`、指数快照 | EXPLORE |
| `get_price_history` | price | `/api/a-share/prices/history`（日/周/月，复权可选） | EXPLORE |
| `get_index_constituents` | market | `/api/a-share-index/constituents/ths-stock-list` | EXPLORE |
| `get_valuation` | fundamental | `/api/a-share/valuations/snapshot` | EXPLORE |
| `get_financials` | fundamental | `/api/a-share/financials/{income,balance,cashflow}` | EXPLORE |
| `get_financial_indicators` | fundamental | `/api/a-share/financials/indicators` | EXPLORE |
| `get_trading_calendar` | calendar | `/api/a-share/calendar/trading-days` | EXPLORE |
| `get_limit_up_stats` | heat | `/api/a-share/special-data/limit-*-pool`、`limit-up-ladder` | EXPLORE |
| `get_hot_rank` | heat | `/api/a-share/special-data/hot-stock-list` | EXPLORE |
| `get_dragon_tiger` | event | `/api/a-share/special-data/dragon-tiger-list` | EXPLORE |
| `get_news_flash` | event | `/api/news/flash/list`、`news/topic/hot-list` | EXPLORE |
| `emit_artifact` | 输出 | 内部 | **COMMIT** |

> 工具集刻意控制在 **13 个注册项**（12 数据 + 1 内部）：**工具 schema 会全量进上下文**，103 个全塞进去会直接爆预算。这是「上下文预算 → 工具裁剪」的真实工程权衡，可在面试中展开讲。
> **24h 首批实现 9 个数据工具 + 1 个内部工具**（**2026-10-19 由 8 放开为 9**：`get_financials` 提前落地），后置 3 个（`get_financial_indicators` / `get_trading_calendar` / `get_dragon_tiger`）时间富余再加，见 [03_BUILD_PLAN.md](./03_BUILD_PLAN.md) §3 M2。

### 9.3 工具执行链路（降级阶梯）

```text
invoke(call)
 ├─ 1. 参数校验（JSON Schema）
 ├─ 2. 幂等检查（dedupe_key 命中 → 直接复用）
 ├─ 3. 限流检查（令牌桶 + 429/4001 退避）
 ├─ 4. 缓存检查（TTL 内命中 → 返回并标记 cached）
 ├─ 5. 主源调用（扶摇，超时 8s）
 │     └─ 失败：重试（指数退避 ×2）→ 熔断器计数
 ├─ 6. 备源调用（iFinD，若配置且已启用）
 │     └─ 失败：进入降级
 ├─ 7. 降级（返回 DegradedResult：可用缓存 + degrade_reason）
 └─ 8. 质量元数据注入（as_of / staleness / confidence / caliber）
```

---

## 10. 模型路由与降级

### 10.1 路由策略

```text
ModelRouter.complete(messages, schema, hint)
 ├─ 角色 → 模型档位映射
 │     轻任务（Compactor/Clarifier） → 小模型（低成本）
 │     重任务（Planner/Reasoner/Synthesizer） → 旗舰模型（强推理）
 ├─ provider 列表（按优先级）：[primary, secondary, tertiary]
 ├─ 健康检查：连续失败 N 次 → 熔断 T 秒
 ├─ 超时：单次调用 wall-timeout（默认 45s）
 └─ 全失败 → DegradedLLMResult（触发「降级研判」路径）
```

### 10.2 结构化输出保障（防幻觉的关键）

| 手段 | 说明 |
|---|---|
| `response_format=json_schema` | 首选：provider 原生结构化输出 |
| Pydantic 二次校验 | 返回不合法 → 自动重试并附带错误信息（最多 2 次） |
| Few-shot 示例 | 每个角色的输出契约给 1–2 个正例 |
| 字段白名单 | 工具名、组件名、evidence_id 必须来自真实集合 |

### 10.3 降级路径

| 失败对象 | 降级行为 | 用户可见 |
|---|---|---|
| 单个工具 | 重试 → 换源 → 跳过 + 标注 | 「降级说明卡片」 |
| 单次 LLM | 换 provider → 换模型档位 | Trace 标记 failover |
| 全部 LLM 不可用 | **降级研判**：只列证据，不给结论 | 明显提示「未能生成结论」 |
| 数据过期 | 用缓存 + 标注 staleness | 证据卡显示「数据延迟 N 分钟」 |

---

## 11. 动态 UI 契约（Generative UI）

### 11.1 三层结构

```text
模型产出（受 schema 约束）
      ↓
UIBlock { component, props, refs }   ← 组件白名单校验
      ↓
前端组件注册表（component → React 组件）
      ↓
渲染（失败 → FallbackJsonCard）
```

### 11.2 组件白名单（12 个）

| component | 用途 | 必填 props |
|---|---|---|
| `plan_card` | 计划 DAG 展示 | `tasks[]`（含 deps） |
| `clarify_question` | 澄清追问 | `questions[]` |
| `evidence_card` | 单条证据 | `evidence_id, source, as_of, unit, caliber, value` |
| `evidence_table` | 证据表 | `evidence_ids[]` |
| `metric_block` | 关键指标 | `label, value, unit, delta, caliber` |
| `series_chart` | 时序图（K线/资金） | `series[], x_field, y_fields[]` |
| `distribution_chart` | 分布图（涨跌停家数等） | `categories[], values[]` |
| `conclusion_card` | 结论（**含 layer 标签**） | `layer[FACT\|INFERENCE\|UNCERTAIN], text, confidence, condition?, refs[]` |
| `citation_list` | 引用清单 | `citations[]` |
| `approval_card` | 审批请求 | `approval_id, type, summary` |
| `degradation_notice` | 降级说明 | `scope, reason, impact` |
| `trace_ref` | 可跳转的 Trace 引用 | `span_id` |

### 11.3 关键约束

1. **模型不能凭空造组件**：组件名必须在白名单内，否则整块被降级为 `FallbackJsonCard` 并在 Trace 记录 `render_status=failed`。
2. **`conclusion_card` 强制分层**：`layer=FACT` 时 `refs` 不能为空 → 无引用的"事实"在**渲染层就被拦下**（与 Guard 形成双保险）。
3. **流式增量**：UIBlock 按 `seq` 增量推送，前端按序插入，支持"边跑边出"。
4. **证据可点**：任何 `refs` 都渲染为可点击链接 → 跳转 Evidence Panel。

---

## 12. 可观测性

### 12.1 Span 树

```text
run (span)
├── plan (span)                 attrs: goal, tasks_count, model
├── task#1 (span)               attrs: title, deps, status
│   ├── loop#1 (span)
│   │   ├── llm (span)          attrs: provider, model, tokens_in/out, cost, latency, cached
│   │   ├── tool (span)         attrs: name, args_hash, status, attempts, latency, degrade
│   │   └── compact (span)      attrs: before_tokens, after_tokens, dropped_steps
│   └── loop#2 (span) ...
├── task#2 (span) ...
└── synthesize (span)           attrs: evidences_used, guard_verdict, model
```

### 12.2 落库与查询

- Span 写入 `traces` 表（扁平存储 + parent 指针，前端组树）
- 前端 Trace Viewer：瀑布图 + 详情抽屉
- **每个 Artifact 引用可反查**：`citation → evidence → tool_call → span`

---

## 13. API 设计

```text
# 线程与运行
POST   /api/threads                      创建线程（含初始目标）
GET    /api/threads                      线程列表
GET    /api/threads/{id}                 线程详情（含 runs）
POST   /api/threads/{id}/runs            发起新 Run（返回 run_id）
GET    /api/runs/{id}                    Run 状态快照
GET    /api/runs/{id}/stream             SSE 流（plan/step/evidence/ui/status 事件）
POST   /api/runs/{id}/pause              暂停
POST   /api/runs/{id}/resume             恢复
POST   /api/runs/{id}/abort              终止

# 审批
GET    /api/approvals?status=PENDING     待审批列表
POST   /api/approvals/{id}/decide        批准/拒绝
POST   /api/runs/{id}/plan               修改计划后重新执行

# 证据与产物
GET    /api/runs/{id}/evidences
GET    /api/runs/{id}/artifacts
GET    /api/evidences/{id}               证据详情（含 raw_ref）

# 可观测
GET    /api/runs/{id}/traces             Span 树
GET    /api/runs/{id}/cost               成本汇总

# 运维
GET    /api/tools                        工具注册表
GET    /api/health                       健康检查（含数据源与模型健康）
```

### 13.1 SSE 事件契约

```text
event: status      data: {"run_id":..., "status":"EXECUTING"}
event: plan        data: {"ui_block": {...plan_card...}}
event: step        data: {"task_id":..., "type":"ACT", "tool":"get_price_snapshot"}
event: evidence    data: {"ui_block": {...evidence_card...}}
event: ui          data: {"ui_block": {...}}                  # 其他动态UI
event: approval    data: {"approval_id":..., "type":"PLAN_CONFIRM"}
event: degrade     data: {"ui_block": {...degradation_notice...}}
event: artifact    data: {"artifact_id":...}
event: done        data: {"run_id":..., "status":"COMPLETED", "cost":...}
event: error       data: {"code":..., "message":...}
```

---

## 14. 安全与配置

### 14.1 环境变量

```text
# 数据源
FUYAO_BASE_URL=https://fuyao.aicubes.cn
FUYAO_API_KEY=              # 可选；缺失时自动降级 MOCK_DATA_MODE（提交前移除 api-key.txt）
IFIND_MCP_ENABLED=true      # 已实测可用，仅作降级备源；不可用时 UI 显式标注
IFIND_MCP_ENDPOINT=
IFIND_MCP_KEY=

# 模型
LLM_PROVIDER_PRIMARY=openai-compatible
LLM_BASE_URL_PRIMARY=
LLM_API_KEY_PRIMARY=
LLM_MODEL_PRIMARY=
LLM_MODEL_SMALL=            # 小模型档位（Compactor/Clarifier）
LLM_TIMEOUT_MS=45000
LLM_MAX_FAILOVER=2

# 预算与停止规则
BUDGET_MAX_STEPS=20
BUDGET_MAX_TOKENS=100000
BUDGET_MAX_COST_CNY=2.0
BUDGET_MAX_WALL_TIME_S=180
LOOP_DETECT_REPEAT=3
NO_PROGRESS_STEPS=4

# 存储
DATABASE_URL=sqlite:///./data/buddy.db
CACHE_TTL_S=60

# 上下文与记忆（M4）
COMPACT_THRESHOLD_RATIO=0.6
MEMORY_RECALL_TOP_K=5
MEMORY_DECAY_HALFLIFE_H=72

# 动态 UI（M5；前端另有 NEXT_PUBLIC_API_BASE）
UI_BLOCK_MAX=200
RENDER_STRICT=true

# 可观测与合规（M6）
TRACE_ENABLED=true
GUARD_STRICT=true
SANITIZE_PATTERNS=

# 应用
APP_ENV=dev
MOCK_DATA_MODE=true         # 离线演示/测试开关
```

> 上述 M4–M6 运行时调参 **8 项**（另加前端 `NEXT_PUBLIC_API_BASE` 共 9 项）为 v2 计划补齐项；M0 只需落地 `FUYAO_*` / `DATABASE_URL` / `MOCK_DATA_MODE` / `APP_ENV`。

### 14.2 安全要求

- API Key 仅存环境变量，`.env` 进 `.gitignore`；仓库内提供 `.env.example`
- 日志与 Trace **脱敏**（Key、用户隐私一律不入库）
- 用户持仓等个人信息一律**构造数据**，不做真实存储
- 所有外部调用设超时与上限，防止挂死

---

## 15. 一次完整 Run 的时序（正常路径）

```text
前端            API          Harness                     数据源       模型
 │  POST /runs   │              │                          │           │
 │──────────────>│  创建 Run    │                          │           │
 │               │─────────────>│ RunStarted(事件)         │           │
 │               │              │ PreHook: 注入记忆        │           │
 │               │              │ PlanHook → Planner ──────┼──────────>│ 生成 DAG
 │  SSE: plan    │<─────────────│ UIBlock(plan_card)       │           │
 │  POST /decide │              │ PlanApproved(事件)       │           │
 │──────────────>│─────────────>│                          │           │
 │               │              │ task#1: Loop#1           │           │
 │               │              │  Think ──────────────────┼──────────>│
 │               │              │  Act → ToolExecutor ─────>│ snapshot  │
 │  SSE: evidence│<─────────────│ EvidenceRecorded         │           │
 │               │              │  PostHook(落库/Trace)    │           │
 │               │              │ task#2 ...（Token 超 60% → Compact）  │
 │  SSE: ui      │<─────────────│ ContextCompacted         │           │
 │               │              │ Synthesize ──────────────┼──────────>│ 三层产物
 │               │              │ GuardPostHook → 通过      │           │
 │  SSE: artifact│<─────────────│ ArtifactBuilt + UIBlocks │           │
 │  SSE: done    │<─────────────│ RunCompleted + Checkpoint│           │
```

### 15.1 异常路径（降级演示）

```text
 │               │              │ ToolExecutor 主源超时     │
 │               │              │  重试 ×2 → 熔断计数       │
 │               │              │  备源(不可用) → 降级       │
 │  SSE: degrade │<─────────────│ UIBlock(degradation_notice)｜ Task → DEGRADED
 │               │              │ 继续执行剩余 Task          │
 │  SSE: artifact│<─────────────│ 产出「部分成果 + 降级说明」 │
```

---

## 16. 架构决策记录（ADR 摘要）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| ADR-1 | 自研 Harness | LangGraph | 题目考察运行机制，内核需可见可讲 |
| ADR-2 | SSE 而非 WebSocket | WebSocket | 单向推送够用、实现成本低、断线重连简单 |
| ADR-3 | SQLite 起步 | PostgreSQL | 零运维；接口不变可切换 |
| ADR-4 | 工具集裁剪至 **13 注册项**（12 数据 + 1 内部） | 接入全部 103 个 | 工具 schema 全量进上下文，预算约束；24h 首批做 9（8+1） |
| ADR-5 | 事件溯源而非快照 | 状态快照 | 恢复更精确、天然可审计、天然可回放 |
| ADR-6 | 关键词记忆而非向量库 | pgvector | 24h 部署成本；接口预留可替换 |
| ADR-7 | 组件白名单而非自由生成 UI | 纯 Markdown | 可控、可降级、可校验 |

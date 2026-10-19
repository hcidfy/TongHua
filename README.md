# Investment X Buddy

面向投资研究者的个人 Agent 工作台：输入一个研究目标，系统规划任务、受控调用金融工具、管理上下文与长期状态、产出分层且可回查的研究证据与文字，并在需要时请求人工审批。后端是自研 `PreHook -> AgentLoop -> PostHook` Harness，前端是研究控制台，二者共同构成"线程 / 任务 / 证据 / 产物 / 动态 UI"到投资研究流程的映射。

> **交付状态（2026-10-20）**：云端已部署最新程序并通过操作测试。
>
> - 可操作入口：[研究控制台](http://8.136.108.32:3000/console)（API `http://8.136.108.32:8000`）
> - 云端只读核查：`/api/health` 返回 `status=ok`、`env=prod`、`mock_data_mode=false`、`llm_configured=true`；`/api/tools` 返回 `count=10`；契约 15 表 / 26 事件 / 10 类 SSE / 12 项 UI 白名单（已实现 7）。核查记录见[提交测试说明](push/TEST_REPORT.md)。
>
> 当前公网入口是 **HTTP**，且没有成熟的用户认证 / 租户隔离 / 网关限流 / HTTPS。**请只使用非敏感的公开研究目标，不要输入真实持仓、账户、密钥或任何私人信息。**

## 必交入口（评委先看这里）

| 题目必交项 | 入口 | 说明 |
|---|---|---|
| 可访问、可操作的 Web 产品 | [研究控制台](http://8.136.108.32:3000/console) | 首页 `/` 307 跳转到 `/console` |
| 源代码仓库与 README | [hcidfy/TongHua](https://github.com/hcidfy/TongHua) + 本文 | 仓库根目录即项目根：`app/`、`tests/`、`web/`、`docs/`、`push/` 都在仓库根目录下 |
| AI 使用与验证记录 + 测试说明 | [单文件版](push/AI使用与验证记录和测试说明.md) | 详细版：[AI 使用与验证](push/AI_USAGE_AND_VERIFICATION.md)、[测试说明](push/TEST_REPORT.md) |
| 提交要求 1–3 的实现对照 | [IMPLEMENTATION.md](push/IMPLEMENTATION.md) | 能力注册 / 计划执行 / 压缩 / 记忆 / 检查点恢复、权限审批停止成本可观测、产品映射与降级恢复 |
| 提交材料索引与验收导读 | [push/README.md](push/README.md) | 5 分钟体验路径、材料清单、边界声明 |
| 60–180 秒演示视频（可选） | [push/video.mp4](push/video.mp4) | 产品实录（H.264/MP4，约 15 MB），与提交材料同目录 |

---

## 1. 启动方式

### 1.1 云端（评委直接访问，无需安装）

| 对象 | 地址 | 说明 |
|---|---|---|
| Web 控制台 | `http://8.136.108.32:3000/console` | 首页 `/` 307 → `/console` |
| 后端 API | `http://8.136.108.32:8000` | `/api/health` 返回 `env=prod`、`mock_data_mode=false`、`llm_configured=true` |

云端环境由候选人部署与维护；仓库不包含云端 Secret、数据库与部署日志。**云端 `.env` 与本地 `.env` 相互独立**：改代码或改配置后必须重启对应后端进程，页面刷新不会加载新后端代码。

### 1.2 本地（源码复现）

前提：Python 3.11（`pyproject.toml` 要求 `>=3.11`）、Node.js 20+、npm（本机验证：conda env `TongHua` = Python 3.11.17，Node v24.14.0 / npm 11.9.0）。以下命令在仓库根目录（即项目根）执行：

```powershell
conda run -n TongHua --no-capture-output python -m pip install -e ".[dev]"
# 仅首次初始化；已有 .env 请保留，勿覆盖密钥
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path web/skeleton/.env.local)) { Copy-Item web/skeleton/.env.example web/skeleton/.env.local }
# 在本地 .env 配置 FUYAO_API_KEY、LLM_BASE_URL_PRIMARY、LLM_API_KEY_PRIMARY、LLM_MODEL_PRIMARY
# 并设 MOCK_DATA_MODE=false。不要在终端输出或提交密钥。
conda run -n TongHua --no-capture-output python -m alembic upgrade head
conda run -n TongHua --no-capture-output python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
npm --prefix web/skeleton ci
npm --prefix web/skeleton run dev
```

浏览器打开 `http://127.0.0.1:3000/console`。`/selftest` 是**组件级自测页**（构建期断言，当前 11 条），不是完整研究 Run 的验收。

推荐用一键脚本（校验 `.env` / `MOCK_DATA_MODE=false` / 迁移状态 / 端口占用，等 `/api/health` 就绪后默认发起一条最小真实研究探针，核对命中的标的代码）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1              # 完整启动 + 探针
powershell -ExecutionPolicy Bypass -File .\start_local.ps1 -CheckOnly   # 只做环境预检
powershell -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipProbe   # 只起服务，不跑探针
```

脚本默认 `CondaEnv=TongHua`、`BackendPort=8000`、`FrontendPort=3000`、`ProbeGoal=分析宁德时代近三年营业收入和利润变化`、`ExpectedThscode=300750.SZ`；端口被旧进程占用时直接报错，不会静默复用旧服务。切换探针目标：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1 `
  -ProbeGoal "分析贵州茅台近三年营业收入和利润变化" -ExpectedThscode "600519.SH"
```

### 1.3 数据库与持久化

`DATABASE_URL` 缺省 `sqlite:///./data/buddy.db`，由 Alembic（`migrations/`）管理迁移。15 张表：

`threads`、`runs`、`tasks`、`steps`、`tool_calls`、`evidences`、`artifacts`、`ui_blocks`、`checkpoint_events`、`memories`、`traces`、`approvals`、`tool_registry`、`llm_calls`、`run_metrics`。

**SQLite 只适合单实例 + 持久卷**；重启后恢复依赖 `checkpoint_events` 折叠出的状态与实体表，不依赖内存快照。

---

## 2. 环境变量

| 变量 | 用途 | 默认值 / 注意 |
|---|---|---|
| `DATABASE_URL` | 数据库地址 | `sqlite:///./data/buddy.db`；部署时必须是持久卷 |
| `MOCK_DATA_MODE` | 离线演示开关 | 默认 `false`；真实研究必须让 `/api/health` 返回 `mock_data_mode=false` |
| `FUYAO_BASE_URL` / `FUYAO_API_KEY` / `FUYAO_TIMEOUT_MS` | 扶摇主数据源 | Key 可选；缺失时按契约显式降级为 Mock，不会启动即崩 |
| `IFIND_MCP_ENABLED` / `IFIND_MCP_ENDPOINT` / `IFIND_MCP_KEY` | iFinD news MCP（定向资讯/公告文本） | 默认启用但仅作备源；endpoint 指向 `.../hexin-ifind-ds-news-mcp`；Key 优先读环境变量，缺失时可从本地受保护的 `api-key.txt` 加载，**不入库** |
| `LLM_PROVIDER_PRIMARY` / `LLM_BASE_URL_PRIMARY` / `LLM_MODEL_PRIMARY` / `LLM_API_KEY_PRIMARY` | 模型服务 | 可选；无 Key 时模型侧返回 `DEGRADED` 而不是崩溃 |
| `LLM_MODEL_SMALL` | 轻任务档位（澄清 / 压缩） | 留空回落到主模型 |
| `LLM_PRICE_INPUT_PER_MTOK_CNY` / `LLM_PRICE_OUTPUT_PER_MTOK_CNY` | 成本折算单价 | 默认 `0` 表示**未计价**，不代表免费或未调用 |
| `BUDGET_MAX_STEPS` / `BUDGET_MAX_TOKENS` / `BUDGET_MAX_COST_CNY` / `BUDGET_MAX_WALL_TIME_S` | 四维预算上限 | 默认 20 步 / 100000 tokens / 2.0 元 / 180 秒 |
| `LOOP_DETECT_REPEAT` / `NO_PROGRESS_STEPS` / `APPROVAL_TIMEOUT_S` | 循环检测 / 无进展判定 / 审批超时 | 默认 3 次 / 4 步 / 1800 秒 |
| `COMPACT_THRESHOLD_RATIO` / `MEMORY_RECALL_TOP_K` / `MEMORY_DECAY_HALFLIFE_H` | 压缩阈值 / 记忆召回条数 / 时间衰减半衰期 | 默认 0.6 / 5 / 72 小时 |
| `NEXT_PUBLIC_API_BASE` | 浏览器访问的后端地址 | 本地 `http://127.0.0.1:8000`；**在 Next.js 构建时打进前端包**，改动后必须重新构建；不接受 `http:` 这类不完整值（前端会校验并回退） |

完整非敏感清单见 [`.env.example`](.env.example)、[`web/skeleton/.env.example`](web/skeleton/.env.example) 与 [`app/config/settings.py`](app/config/settings.py)。`.env`、`web/skeleton/.env.local`、`api-key.txt`、真实响应缓存、数据库与私钥**一律不入库**。

> 成本判据（重要）：健康 Run 应同时满足 `llm_calls > 0`、`tokens_total > 0`、`cost_total > 0`。**未配置单价时 `cost_total=0` 只表示"价格未知"**，此时费用上限类停止规则不成立。云端当前即未配单价，判读方法见[测试说明](push/TEST_REPORT.md)。

---

## 3. 产品选择（为什么这样搭）

| 选择 | 理由 | 代价 / 边界 |
|---|---|---|
| **自研 Harness**（`PreHook -> AgentLoop -> PostHook`）而非 LangGraph | 题目要考的是"运行机制"本身：钩子顺序、降级阶梯、预算与停止规则、检查点折叠都要能被指到具体代码行 | 没有生态现成能力，状态机、恢复与压缩都要自己实现 |
| **FastAPI + Next.js**（后端 15 表、前端研究控制台） | 后端负责契约与持久化，前端只做展示与回查；研究结论可回到原字段 | 前后端契约必须逐字段对齐（本项目曾因 `null` 契约与 SSE 只传 ID 出过真实缺陷） |
| **SQLite 单实例 + 持久卷** | 24 小时交付内最稳的持久化形态，检查点/恢复可直接落库验证 | 不支持多副本/多用户并发；锁竞争不适用生产 |
| **冻结契约**：15 表 / 26 事件 / 10 类 SSE / 12 项 UI 白名单（实现 7）/ 10 个工具 | 让"机制覆盖面"可被审计，避免边做边改契约 | 5 个白名单组件未实现，命中即降级为 `FallbackJsonCard` |
| **事件驱动 + 白名单动态 UI** | SSE 只认 10 类事件，UI 只认 12 个组件的合法 props；非法即降级，不白屏 | 代理必须支持 SSE 长连接；跨源部署要处理 CORS |
| **工具执行 8 步降级阶梯** | 参数校验 → 幂等（`dedupe_key`）→ 限流 → 缓存 → 主源（重试/熔断）→ 备源 → 降级结果 → 质量元数据注入，顺序固定、可单测 | 备源只在 iFinD 已配置时可用；无备源则直接降级 |
| **不做**多租户 SaaS | 题目要求"个人 Agent 工作台"，24 小时内把机制做对优先于做账号体系 | 公网入口无登录、无租户隔离、无计费（见 §6） |

---

## 4. AI 的角色

产品里 AI **只做"语言与判断"，不做"事实来源"**。六个角色各有独立提示词（`app/harness/prompts/`）与降级路径：

| AI 角色 | 提示词 | 负责 | 失败时 |
|---|---|---|---|
| Clarifier 澄清 | `clarifier.md` | 识别研究目标里的歧义（标的、期限、口径） | 用规则化默认值，并在不确定层标注 |
| Planner 规划 | `planner.md` | 把目标拆成有依赖的任务 DAG，只允许注册工具 | 走保守模板，并显式标记"模板计划" |
| Reasoner 推理执行 | `reasoner.md` | 逐轮决定调哪个工具、给什么参数、如何观察结果 | 参数校验失败回灌重试；离线走受控兜底 |
| Compactor 压缩 | `compactor.md` | 超预算时折叠旧步骤、保留系统约束与证据引用 | 规则化摘要，证据原记录仍在库 |
| Guard 合规 | `guard.md` + `app/compliance/guard.py` | 拦截无引用事实、无切换条件推断、预测/收益承诺/买卖建议 | 拦截即落 `guard_findings`，产物判 `REJECT` |
| Synthesizer 综合 | `synthesizer.md` + `app/harness/artifact_builder.py` | 产出事实 / 推断 / 不确定 / 引用四层研究文本 | 只陈述可核实内容，缺数据写入不确定层 |

非 AI 的部分（不允许模型代劳）：工具注册与参数 schema、权限级别校验、数据质量元数据（来源 / 时点 / 单位 / 口径 / 原始引用）、证据落库、动态 UI 组件校验、预算与停止规则、合规终判。模型是**调用者**，不是事实的签发者。模型侧结构化输出按 `json_schema → json_object → none` 逐级降档，并按 `provider:model` 记忆成功档位。

---

## 5. 数据来源

| 来源 | 内容 | 使用规则 |
|---|---|---|
| **扶摇**（`fuyao.aicubes.cn`，主源） | A 股/指数行情快照与历史 K 线、指数成分、估值、利润表、涨停池、热榜、资讯热榜 | 检索必须命中目标标的；带 `thscode` 的财报/快照/估值只保留请求代码的记录；`source=fuyao` 且 `raw_ref` 可回查 |
| **iFinD MCP**（备源/补充） | 公司定向新闻与公告文本（`search_news` / `search_notice`） | 仅在配置授权令牌后调用；返回是**文本**，置信度低，不得当结构化财报字段；未配置时直接报缺口，不拿无关热榜冒充公司公告 |
| **Mock**（离线演示） | `app/data/public_mock.py` 生成的构造样本 | 只在显式开启离线模式或缺失扶摇 Key 时使用；`/api/health` 的 `mock_data_mode` 与证据 `source=mock` 双重标识；不得当真实投资事实 |

口径纪律：事实必须能回到原字段，并写明来源、时点、单位、统计口径；数据缺失、冲突、过期或调用失败一律进不确定层或降级说明，**不得静默生成"正常"结论**；不输出确定性涨跌预测、收益承诺或买卖建议。

---

## 6. 已知边界

1. **市场覆盖**：扶摇主源覆盖 A 股/指数。港股、美股等非 A 股标的**不在覆盖范围**，这类目标会得到"证据不足"的降级结论，而不是编造的结论——属预期行为，不适合作为主链路演示目标。
2. **财报粒度**：`get_financials` 只到利润表**总量**（营业收入 / 营业成本 / 各项费用 / 净利润 / 每股收益，含同比期数），**没有**产品/业务分部收入与经核验的连续三年附注。因此"近三年收入架构（分部占比）变化"这类问题只能给出"总量变化 + 明确的能力缺口"，不能宣称已验证。
3. **成本闸门**：未配置模型单价时 `cost_total=0` 只表示未计价；费用上限类停止规则需要可靠单价与供应商 usage 才成立。
4. **iFinD 全链**：MCP 客户端与单点调用已验证，但尚无"一次完整 LLM Run 内同时完成 MCP 取证"的证据；令牌未授权时该工具会失败并如实上报（不降级成热榜）。
5. **部署安全**：公网为 HTTP；无登录、无角色授权、无租户隔离、无网关限流。**禁止用它处理私人持仓或真实账户信息。**
6. **跨源与 SSE**：`app/api/__init__.py` 的 CORS 白名单只列本地来源，公网由部署侧处理；SSE 长连接需要代理不缓冲、不截断。
7. **参数校验**：`search_ticker.asset_type` 等自由文本参数未做枚举白名单，模型给出上游不接受的取值时会被上游拒绝并以工具失败显示，尚未做自动纠正重试。
8. **组件覆盖**：UI 白名单 12 项只实现 7 项，其余命中即降级；`/selftest` 是组件自测，不等于端到端研究验收。

---

## 7. 未做事项

- 公网 **HTTPS + 证书**、成熟用户鉴权 / 角色授权 / 租户隔离、请求速率与并发上限、账户级费用上限。
- 连续三年**产品/业务分部**公告与附注的结构化接入、调整后口径与披露时点可比性核验（这是"收入架构变化"类研究真正需要的能力）。
- `expensive_tool`（成本闸门）与 `final_review`（最终审阅）两类审批的**自动触发点**（`HITLGate` 接口已就绪，只有 `plan` 审批接线）。
- 白名单里 5 个 UI 组件：`metric_block` / `series_chart` / `distribution_chart` / `citation_list` / `trace_ref`。
- 向量语义记忆召回（当前是关键词命中 + 时间衰减，`recall()` 接口保持稳定）。
- iFinD 文本 → 字段的结构化抽取层；跨市场代码映射（港股/美股）。
- 多实例部署与外部数据库迁移方案；CI、镜像化交付、自动化 E2E（当前 E2E 靠脚本与人工 Run）。
- 可选的 60–180 秒演示视频（由候选人录制上传）。

---

## 8. 测试与目录

```powershell
conda run -n TongHua --no-capture-output python -m pytest -q     # 后端全量（199 条）
npm --prefix web/skeleton run build                              # 前端构建 + /selftest 构建期断言（11 条）
```

- 后端用例 **199 条**，覆盖 M0 地基、M1 内核、M2 数据与工具、M3 状态恢复、M4 记忆与上下文、M5 产物与动态 UI、M6 可观测与合规；2026-10-20 全量回归 **199 passed**。
- 前端 `npm run build` 通过，含 `/selftest` 的 **11 条**构建期断言（计划链按工具名归组、Mock 缺年披露、坏 API 基址回退等）。
- 三类场景覆盖（主链路 / 数据缺失与接口失败 / 极端与合规边界）、云端只读核查原文、评委复现命令与证据分级见 [push/TEST_REPORT.md](push/TEST_REPORT.md)。

```
TongHua/               ← 仓库根 = 项目根
├─ app/
│  ├─ api/            # REST + SSE + 审批 + 可观测端点
│  ├─ harness/        # pipeline / planner / agent_loop / context / compactor / memory
│  │                  # checkpoint / resume / hitl / stop_rules / budget / model_router
│  │                  # artifact_builder / ui_renderer / tool_executor / hooks/{pre,post}
│  ├─ tools/          # 蓝图(10 spec) + 数据工具实现 + 内部 emit_artifact
│  ├─ data/           # fuyao_client / mock_client / ifind_client / quality / cache / breaker / ratelimit
│  ├─ store/          # 15 表 schema + Repository
│  ├─ observ/         # tracer / cost / sanitize
│  ├─ compliance/     # guard
│  └─ config/         # settings
├─ tests/             # 199 条用例，覆盖 M0–M6
├─ web/skeleton/      # Next.js 控制台、动态 UI 渲染、/selftest
├─ migrations/        # Alembic
├─ docs/              # PRD / 架构 / 构建计划 / 交付状态 / 测试与 AI 记录
└─ push/              # 提交材料包（评委版）
```

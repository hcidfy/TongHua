# Investment X Buddy

面向投资研究者的个人 Agent 工作台。输入研究目标后，系统执行规划、受控金融工具调用、上下文压缩、记忆、检查点与恢复，输出分层且可回查的研究证据与产物。

> **当前状态**：本地真实扶摇数据与真实模型研究链已验证；已有 HTTP 云端页面入口 `http://8.136.108.32:3000/console`，但本次本地改动尚未部署到云端。公网入口尚缺 HTTPS、认证与用量限制，不应公开存放密钥或视作正式生产服务。

## 产品体验与架构

- Web 控制台：`/console` 输入目标，查看任务、工具执行、证据、事实/推断/不确定与引用；`/selftest` 为组件级自测，不能替代真实研究 E2E。
- Python FastAPI 后端采用自研 `PreHook -> AgentLoop -> PostHook`，不依赖 LangGraph；9 个数据工具和 1 个内部 `emit_artifact` 工具。
- 计划/执行状态、检查点、线程/长期记忆、人工审批、预算/停止规则、Trace/成本/合规检查分层实现。详细设计见 [架构](docs/02_ARCHITECTURE.md)与[构建计划](docs/03_BUILD_PLAN.md)。
- 主要 AI 角色：澄清、规划、推理执行、压缩、合规检查、综合产物。模型未配置或不可用时进入显式降级路径；不能把降级产物解释为已验证的市场事实。

## 本地启动

前提：Python 3.11、Node.js 20+、npm；Windows 已验证 `TongHua` Conda 环境。以下命令在项目根目录执行；请自行创建隔离环境并安装依赖：

```powershell
conda run -n TongHua --no-capture-output python -m pip install -e ".[dev]"
# 仅首次初始化；已有 .env 请保留，勿覆盖密钥
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path web/skeleton/.env.local)) { Copy-Item web/skeleton/.env.example web/skeleton/.env.local }
# 在本地 .env 配置 FUYAO_API_KEY、LLM_BASE_URL_PRIMARY、LLM_API_KEY_PRIMARY、LLM_MODEL_PRIMARY
# 并设 MOCK_DATA_MODE=false。勿在终端输出/提交密钥。
conda run -n TongHua --no-capture-output python -m alembic upgrade head
conda run -n TongHua --no-capture-output python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
npm --prefix web/skeleton ci
npm --prefix web/skeleton run dev
```

浏览器访问 `http://127.0.0.1:3000/console`。`start_local.ps1` 现已升级为**本地真实模式验收脚本**：会检查 `.env` / `.env.local`、校验 `MOCK_DATA_MODE=false`、处理本地 SQLite 迁移状态、启动前后端、等待 `/api/health`，并默认发起一条最小真实研究探针，确认 `search_ticker` 与 `get_financials` 命中的标的代码符合预期。已有 Key 且 `MOCK_DATA_MODE=false` 时实际访问扶摇；没有 Key 会降级为 Mock，并在 `/api/health` 中显示 `mock_data_mode=true`。本地数据库、日志、密钥不可提交。

推荐直接使用脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1
```

只做本地环境预检而不真正起服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1 -CheckOnly
```

切换探针目标公司：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1 `
  -ProbeGoal "分析贵州茅台近三年营业收入和利润变化" `
  -ExpectedThscode "600519.SH"
```

脚本默认使用 `3000` 作为前端访问端口、`8000` 作为后端接口端口；若端口已被旧进程占用会直接报错，不会静默复用旧服务。

## 环境变量

| 变量 | 用途 | 公开默认/注意 |
|---|---|---|
| `DATABASE_URL` | SQLite 地址 | `sqlite:///./data/buddy.db`；部署时需持久卷 |
| `MOCK_DATA_MODE` | 离线演示开关 | 默认 `false`；真实研究必须确认 `/api/health` 返回 `mock_data_mode=false` |
| `FUYAO_API_KEY` | 扶摇主数据源 | 可选；仅经受限环境注入 |
| `IFIND_MCP_ENDPOINT`, `IFIND_MCP_KEY` | 定向资讯/公告的 iFinD news MCP | endpoint 指向 `.../hexin-ifind-ds-news-mcp`；Key 优先读环境变量，缺失时可从本地受保护的 `api-key.txt` 加载，不入库 |
| `LLM_BASE_URL_PRIMARY`, `LLM_MODEL_PRIMARY`, `LLM_API_KEY_PRIMARY` | 模型服务 | 可选；无 Key 时模型降级 |
| `LLM_PRICE_INPUT_PER_MTOK_CNY`, `LLM_PRICE_OUTPUT_PER_MTOK_CNY` | 成本折算 | 默认 `0` 代表未计价，不代表免费 |
| `NEXT_PUBLIC_API_BASE` | 浏览器后端地址 | 本地 `http://127.0.0.1:8000`；公网部署时必须重构建为 HTTPS 地址 |

完整非敏感配置见 [后端示例](.env.example)和[前端示例](web/skeleton/.env.example)。`.env`、`web/skeleton/.env.local`、`api-key.txt`、真实响应缓存和私钥**不得提交**；本项目不会从 GitHub 自动获取密钥。

## 数据与研究边界

真实研究以目标标的核对后的扶摇结构化记录为财务/行情证据；公司定向资讯或公告在配置 iFinD news MCP 后调用 `search_news` / `search_notice`，返回文本仅作低置信度资料，不能冒充已核实的财报字段。未配置 MCP 时不拿无关热榜填补公司资讯。仅在明确开启离线模式或缺失扶摇密钥时使用 Mock，且不能把 Mock 当真实投资事实。

真实接口已验证可获取宁德时代 2023/2024/2025 年年度利润表；但当前工具仍只有利润表**总量**字段，没有产品/业务分部收入和经核验的三年附注，不能据此宣称已验证“收入架构（业务占比）变化”。数据缺失、过期、冲突或工具失败必须在不确定层说明；事实需关联原字段、来源、时点、单位和口径。产品不提供涨跌预测、收益承诺或买卖建议。

真实模式自检（只输出布尔状态，不显示任何密钥）：

```powershell
conda run -n TongHua --no-capture-output python -c "from app.config.settings import get_settings; s=get_settings(); print('mock=',s.is_mock,'llm=',s.is_llm_configured,'ifind_news=',bool(s.ifind_mcp_enabled and s.ifind_mcp_endpoint and s.ifind_mcp_key))"
Invoke-RestMethod http://127.0.0.1:8000/api/health | Select-Object mock_data_mode,llm_configured
```

两项必须分别为 `mock=False` 和 `llm=True`。新建 Run 后确认 `/api/runs/{run_id}/evidences` 的 `source` 为 `fuyao`（资讯可为 `ifind`）、检索命中的代码属于目标、财报各行代码也与目标一致；只看到 `COMPLETED` 不代表真实取证成功。服务器必须自行配置不入库的 Key 与 `MOCK_DATA_MODE=false` 并重启后端；本地 `.env` 不会上传。

## 测试与交付

```powershell
conda run -n TongHua --no-capture-output python -m pytest -q
npm --prefix web/skeleton run build
```

测试说明见 [TEST_PLAN.md](docs/TEST_PLAN.md)，AI 参与及人工纠偏见 [AI_USAGE_LOG.md](docs/AI_USAGE_LOG.md)。上述命令需以当前环境重新执行并记录结果；先前测试数量不等同于本次公开演示的验收。

已知未完成项：公网 HTTPS、云端**真实研究链** E2E、第三方数据公开演示授权、审批部分触发点及后置 UI 组件。云端现有页面连通不代表本轮代码已部署，也不代表 MCP 已授权可用。必须增加访问控制和用量限制，不可把无租户隔离、持有真实 Key 的 API 直接暴露公网。

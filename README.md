# Investment X Buddy

面向投资研究者的个人 Agent 工作台。输入研究目标后，系统执行规划、受控金融工具调用、上下文压缩、记忆、检查点与恢复，输出分层且可回查的研究证据与产物。

> **当前状态**：本仓库提供本地可运行版本；公网 Web URL 尚未部署，不能将本仓库地址视为在线产品链接。部署计划待云资源准备完成后验证，见 [交付说明](docs/DELIVERY.md)。

## 产品体验与架构

- Web 控制台：`/console` 输入目标，查看任务、工具执行、证据、事实/推断/不确定与引用；`/selftest` 为组件级自测，不能替代真实研究 E2E。
- Python FastAPI 后端采用自研 `PreHook -> AgentLoop -> PostHook`，不依赖 LangGraph；9 个数据工具和 1 个内部 `emit_artifact` 工具。
- 计划/执行状态、检查点、线程/长期记忆、人工审批、预算/停止规则、Trace/成本/合规检查分层实现。详细设计见 [架构](docs/02_ARCHITECTURE.md)与[构建计划](docs/03_BUILD_PLAN.md)。
- 主要 AI 角色：澄清、规划、推理执行、压缩、合规检查、综合产物。模型未配置或不可用时进入显式降级路径；不能把降级产物解释为已验证的市场事实。

## 本地启动

前提：Python 3.11、Node.js 20+、npm；Windows 已验证 `TongHua` Conda 环境。以下命令在项目根目录执行；请自行创建隔离环境并安装依赖：

```powershell
conda run -n TongHua --no-capture-output python -m pip install -e ".[dev]"
Copy-Item .env.example .env
Copy-Item web/skeleton/.env.example web/skeleton/.env.local
conda run -n TongHua --no-capture-output python -m alembic upgrade head
conda run -n TongHua --no-capture-output python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
npm --prefix web/skeleton ci
npm --prefix web/skeleton run dev
```

浏览器访问 `http://127.0.0.1:3000/console`。`start_local.ps1` 是本地启动快捷脚本，启动前确认 8000/3000 未被旧进程占用。默认 `MOCK_DATA_MODE=true`，**默认使用代码构造的虚构、可公开分发样本**，不需要真实 Key；原始接口快照、数据库和日志不会上传。

## 环境变量

| 变量 | 用途 | 公开默认/注意 |
|---|---|---|
| `DATABASE_URL` | SQLite 地址 | `sqlite:///./data/buddy.db`；部署时需持久卷 |
| `MOCK_DATA_MODE` | 演示数据开关 | `true`，内容全部是虚构例子 |
| `FUYAO_API_KEY` | 扶摇主数据源 | 可选；仅经受限环境注入 |
| `IFIND_MCP_ENDPOINT`, `IFIND_MCP_KEY` | iFinD 备源 | 可选；需自行确认授权与工具可用性 |
| `LLM_BASE_URL_PRIMARY`, `LLM_MODEL_PRIMARY`, `LLM_API_KEY_PRIMARY` | 模型服务 | 可选；无 Key 时模型降级 |
| `LLM_PRICE_INPUT_PER_MTOK_CNY`, `LLM_PRICE_OUTPUT_PER_MTOK_CNY` | 成本折算 | 默认 `0` 代表未计价，不代表免费 |
| `NEXT_PUBLIC_API_BASE` | 浏览器后端地址 | 本地 `http://127.0.0.1:8000`；公网部署时必须重构建为 HTTPS 地址 |

完整非敏感配置见 [后端示例](.env.example)和[前端示例](web/skeleton/.env.example)。`.env`、`web/skeleton/.env.local`、`api-key.txt`、真实响应缓存和私钥**不得提交**；本项目不会从 GitHub 自动获取密钥。

## 数据与研究边界

金融工具的真实通道为扶摇；iFinD 是可选备源。公开仓库中的离线样本由 [`app/data/public_mock.py`](app/data/public_mock.py) 人工构造，证券代码只是参数和关联演示，其价格、财报、成分、热度和资讯均**不代表真实市场或公告**。私人原始接口样本被 `.gitignore` 排除。

当前财报工具只提供利润表总量字段，虚构样本仅有 2024/2025 年，且没有产品/业务分部收入。对“近三年收入架构变化”不能给出已经验证的分部结构结论。数据缺失、过期、冲突或工具失败必须在不确定层说明；事实需要关联原字段、来源、时点、单位和口径。产品不提供涨跌预测、收益承诺或买卖建议。

## 测试与交付

```powershell
conda run -n TongHua --no-capture-output python -m pytest -q
npm --prefix web/skeleton run build
```

测试说明见 [TEST_PLAN.md](docs/TEST_PLAN.md)，AI 参与及人工纠偏见 [AI_USAGE_LOG.md](docs/AI_USAGE_LOG.md)。上述命令需以当前环境重新执行并记录结果；先前测试数量不等同于本次公开演示的验收。

已知未完成项：公网 HTTPS 部署与可访问 URL、云端真实浏览器 E2E、第三方数据公开演示授权核对；审批的部分触发点和后置 UI 组件仍未完成。部署前需增加访问控制和用量限制，不能把当前无租户隔离的 API 直接暴露公网。

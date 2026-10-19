# AI 使用与人工验证记录

> 本文为早期摘要。面向评委的完整记录（AI 工具、参与环节、候选人承担的工作与 10 条纠偏）见 [push/AI_USAGE_AND_VERIFICATION.md](../push/AI_USAGE_AND_VERIFICATION.md)；与测试说明合并的单文件版见 [push/AI使用与验证记录和测试说明.md](../push/AI使用与验证记录和测试说明.md)。依据开发过程中已留存的设计/测试记录整理；不包含提示词中的密钥、账户与真实持仓。

| 环节 | AI 参与 | 人工检查/纠偏 | 验证依据 |
|---|---|---|---|
| 需求与架构 | 协助形成 PRD、架构、M0–M7 计划与自研 Harness 方案 | 候选人决定不用 LangGraph，冻结表/事件/SSE 契约，并确认金融口径与合规红线 | `docs/01_PRD.md`、`docs/02_ARCHITECTURE.md`、`docs/03_BUILD_PLAN.md` |
| 工具与运行链路 | 辅助实现工具注册、Planner、AgentLoop、上下文压缩、记忆、检查点和恢复 | 发现推理模型在没有参数签名时猜错工具入参，补入 schema 与参数回灌；拒绝凭空增加数据库/事件 | `app/harness/`、`app/tools/` 与 `tests/` |
| 模型兼容性 | 实现 OpenAI 兼容接口与结构化输出 | 实测某兼容端点拒绝 `json_schema`（HTTP 400），改为逐级降档，不把失败伪装成功 | `app/harness/model_router.py`、相关测试 |
| 研究产物与展示 | 辅助开发证据、计划链、三层产物和动态 UI | 修正 API 路径误当工具名、裸露证据 ID、事实文本只有接口而无原始值等问题 | `app/api/runs.py`、`app/harness/artifact_builder.py`、`web/skeleton/` |
| 金融与合规 | 生成财务指标示例和研究文字 | 明确 Mock 两年利润表无法证明“三年收入架构变化”；总收入不等于分部收入，Mock 不得冒充真实结论 | `app/data/public_mock.py`、`tests/test_m5_api.py`、`app/compliance/guard.py` |
| 公开仓库准备 | 辅助 README、测试说明、安全排查 | 原始接口快照和内部交接记录不进入公开提交；默认演示数据改为虚构生成，并检查暂存内容不含常见令牌格式 | `.gitignore`、`app/data/public_mock.py`、`docs/TEST_PLAN.md` |

**验证口径**：自动化测试、组件构建自检、真实 LLM 调用及浏览器检查是不同证据，不可相互替代；公网浏览器验收必须在部署后另行完成。发布时仅记录脱敏的运行标识、测试日期、代码版本与结论。

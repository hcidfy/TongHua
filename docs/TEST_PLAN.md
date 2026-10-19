# 测试与复现说明

> **本文是早期离线测试草案，已不是提交材料。** 评委版测试步骤、三组场景覆盖（主链路 / 数据缺失与接口失败 / 极端与合规边界）、本地回归结果、公网只读核查原文与复现命令，见 [push/TEST_REPORT.md](../push/TEST_REPORT.md)；合并单文件版见 [push/AI使用与验证记录和测试说明.md](../push/AI使用与验证记录和测试说明.md)。

> 2026-10-20 现状：云端最新程序已由候选人部署并完成操作测试；本次收尾对公网做了只读核查（`/api/health`、`/api/tools`、线程/Run/证据/产物/可观测接口），未独立执行云端浏览器写操作。自动化测试使用隔离 fixture；`MOCK_DATA_MODE` 缺省 `false`，实际模式取决于是否有扶摇 Key，须以 `/api/health` 为准。原“云端待部署”空表已由下方状态行取代。

## 1. 主链路

1. 在根目录执行 `conda run -n TongHua --no-capture-output python -m pytest -q`，在 `web/skeleton` 运行 `npm run build`；记录通过数、失败项和日期。
2. 分别启动后端和前端（见根目录 README），打开 `/console` 输入“分析茅台近三年公司收入架构变化”；核对目标被拆成任务、调用财报工具，证据可展开为来源/时点/原字段。
3. 查看事实、推断和不确定分层及引用；应明确“虚构数据”“只有两年”“没有分部披露”，不应生成确定的三年结构结论。刷新页面并核对 Run 状态与持久证据。
4. 核对 `/api/runs/{run_id}/evidences` 中 `tool_calls` 与 `evidences[*].tool_name` 可关联，`/artifacts` 的事实 `refs` 可以回查到证据。自动化入口为 `tests/test_m5_api.py`、`tests/test_m5_artifacts_ui.py`。

## 2. 数据缺失与接口失败

1. 不配置真实 Key 时确认程序可启动，Mock 来源有明确标识；无可用证据时事实不应伪造取值。
2. 用测试中的断网、限流、上游业务错误、过期缓存和工具异常场景运行 `tests/test_m0_data.py`、`tests/test_m2_tools.py`，检查降级原因与来源标识。
3. 验证 LLM 不支持结构化 schema 时自动降档；非法工具参数不应被静默吞掉（`tests/test_m1_router.py`、`tests/test_m1_tools.py`）。
4. 云端另从外部网络验证 SSE 断开/重连、反向代理超时、重启后检查点恢复，并记录实际结果。

## 3. 极端与合规边界

1. 超预算、重复循环、无进展、审批挂起/拒绝/超时、暂停/恢复：按 `tests/test_m3_state_recovery.py`、`tests/test_m4_memory_context.py` 验证状态机合法迁移，停止时最终为 `STOPPED`。
2. 注入无引用的 FACT、非法动态 UI props，确认 FACT 被拒、非法组件降级而不是白屏；检查 `tests/test_m5_artifacts_ui.py` 与 `/selftest`。
3. 对直接买卖建议、确定性涨跌预测、收益承诺和含敏感数据的 Trace 运行 `tests/test_m6_observability_compliance.py`；人工检查可观察端点与页面，没有未经证实的“正常”结论。

## 云端验收记录

完整记录（含只读接口原文、13 个场景与待补项）见 [push/TEST_REPORT.md §6](../push/TEST_REPORT.md)。摘要：

| 项 | 状态 |
|---|---|
| 公网 URL | `http://8.136.108.32:3000/console`（API `:8000`），HTTP、无鉴权，仅供非敏感演示 |
| 部署状态 | 候选人已部署最新程序并完成云端操作测试（候选人确认） |
| 本次独立只读核查 | `/api/health`、`/api/tools`、`/api/threads`、`/api/runs/{id}`、`/evidences`、`/artifacts`、`/observability/*` 均通过；最新 Run 为"京东"非 A 股目标，系统按设计降级为"证据不足"而非编造结论 |
| 待候选人补 | 最终提交号、脱敏 `run_id`、浏览器跨源 POST/SSE、重启恢复、云端审批与 Guard 实测、演示视频链接 |

生产环境不要使用真实账户持仓进行测试。测试截图、日志和录屏不得包含密钥或个人信息；视频属于可选交付物，不能替代可操作 URL。

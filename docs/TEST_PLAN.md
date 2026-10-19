# 测试与复现说明

> 环境：Python 3.11、项目依赖、Node.js 与 npm。默认使用 `MOCK_DATA_MODE=true` 的虚构数据，不接入真实密钥。公网验收需在云端重新记录，不借用本地历史通过数。

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

## 云端验收记录（待部署后填写）

| 日期/版本 | 公网 URL | 主链路 | 异常/恢复 | 合规/敏感信息 | 验证人 |
|---|---|---|---|---|---|
| 待验证 | 待部署 | 待验证 | 待验证 | 待验证 | 待填写 |

生产环境不要使用真实账户持仓进行测试。测试截图、日志和录屏不得包含密钥或个人信息；视频属于可选交付物，不能替代可操作 URL。

# 提交前清单（候选人自查用）

> 本文件是**给提交人自己**的操作清单，不面向评委。评委材料见 [push/README.md](README.md)。

## 1. 提交平台上要填的四项

| 项 | 现在可填的内容 | 还需你补 |
|---|---|---|
| 可操作 Web 产品 URL | `http://8.136.108.32:3000/console` | 若已配域名/HTTPS，替换为最终 URL（改了就要重建前端并核对 `NEXT_PUBLIC_API_BASE`） |
| 源代码仓库 | `https://github.com/hcidfy/TongHua`（仓库根即项目根） | 推送后确认评委视角能看到 README 与 `push/` |
| AI 使用与验证记录 | `push/AI使用与验证记录和测试说明.md`（或分开提 `AI_USAGE_AND_VERIFICATION.md`） | — |
| 测试说明 | 同上（或 `push/TEST_REPORT.md`） | §6 云端验收表的待补项 |
| 演示视频（可选） | 已放在仓库 `push/video.mp4`（H.264/MP4 约 15 MB） | 若要单独上传平台，补一个**可访问链接**；并确认视频里没有 `.env`/Key/持仓/个人信息 |

## 2. 需要你手填的值（本材料刻意留空，不伪造）

| 字段 | 填什么 | 填到哪 |
|---|---|---|
| 最终部署提交号 | `git rev-parse --short HEAD` 的结果 | `push/TEST_REPORT.md` §6 表格 |
| 云端测试日期 | 例如 `2026-10-20` | 同上 |
| 脱敏 `run_id` | 云端任一条**A 股**目标的成功 Run（`/api/threads` → run 列表） | 同上（ID 不含个人信息，可直接公开） |
| 浏览器跨源 POST/SSE | 云端页面能创建线程、能收到 SSE | 同上 |
| 重启后恢复 | 重启后端再打开旧 Run，任务/证据/产物仍在 | 同上 |
| 云端审批实测 | 触发计划审批 → 挂起 → 同意/拒绝 | 同上 |
| 视频链接（可选） | 若把 `push/video.mp4` 另外上传到平台，填其地址 | 提交平台 + `push/README.md` 顶部表格 |

## 3. 录屏脚本建议（60–180 秒，产品实录，不要讲 PPT）

| 时间 | 画面 | 要说的一句话 |
|---|---|---|
| 0:00–0:10 | 打开 `/console`，页面上能看到真实模式标识 | "这是研究控制台，当前走扶摇真实数据 + 真实模型。" |
| 0:10–0:25 | 输入 A 股目标（**推荐 `分析宁德时代近三年营业收入和利润变化`**） | "输入研究目标，不选工具、不写提示词。" |
| 0:25–0:50 | 任务卡出现：逐个任务展开，指出哪个工具**实际被调用**、命中的代码是 `300750.SZ` | "任务 → 工具 → 状态，未调用和失败会分开显示。" |
| 0:50–1:20 | 展开一条证据：来源/时点/单位/口径/原始引用 | "每条结论都能回到原始字段。" |
| 1:20–1:50 | 右侧研究结论：事实/推断/不确定/引用；点开引用序号 | "事实带引用，推断带条件，缺口写在不确定层。" |
| 1:50–2:20 | 刷新页面，Run 与证据仍在；打开 Network/EventStream 看 10 类事件 | "SSE 来自已落库状态，刷新可恢复。" |
| 2:20–2:40（可选） | 再跑一个"收入架构变化"目标，指出页面明确写"缺分部数据" | "答不了的问题会明说，不会编。" |

**录制注意**：① 用 A 股标的，别用"京东"（港股/美股不在主源覆盖，会正确降级但不利于展示主链路）；② 不出现 `.env`、Key、令牌、持仓、个人邮箱；③ 页面地址栏清晰可见；④ 不要用 `/selftest` 替代真实 Run。

## 4. 推送前的安全检查

```powershell
cd D:\basic_use\TongHuaShun\InvestmentXBuddy
git status --short
git check-ignore -v .env web/skeleton/.env.local docs/api-key.txt data/buddy.db
```

确认：

- [ ] `.env`、`web/skeleton/.env.local`、`docs/api-key.txt`、`*.db`、`logs/`、`app/data/mock/*.json` **均被忽略**。
- [ ] `git status` 里**没有**任何真实 Key/令牌/持仓/客户数据；`push/` 里也没有（本材料只写布尔状态与公开接口响应）。
- [ ] `docs/HANDOFF.md`、`docs/人机操作.md`、`docs/解决文档.md`、`docs/DATASOURCES.md` **不出现在提交里**（它们是内部记录，已被 `.gitignore` 排除；`push/` 已把关键事实内联）。
- [ ] 若曾把 PAT 写进聊天/文件，已在 GitHub 设置里撤销并重新签发最小权限凭据。
- [ ] 云端 `.env` 不入库、不截图；云端进程已重启加载最新代码。

## 5. Git 提醒

本次已提交的改动：根 `README.md`、`.gitignore`、`docs/AI_USAGE_LOG.md`、`docs/DELIVERY.md`、`docs/TEST_PLAN.md`，以及整个 `push/`（含 `video.mp4`）。以后若要再改：

```powershell
git add README.md push
git status --short          # 再核一遍清单
git commit -m "docs: update submission package"
git push origin main        # 用你自己的最小权限凭据
```

- 不要 `git add -f` 被忽略的内部文档。
- 不要 `--force` 覆盖远端历史。
- 推完用**无登录的浏览器**打开仓库链接，确认 README、`push/` 与视频/链接都能看。
- GitHub 对单文件 >50 MB 会警告、>100 MB 直接拒绝；当前 `push/video.mp4` 约 15 MB，无需处理。

## 6. 最后 5 分钟自测

- [ ] `/api/health`：`mock_data_mode=false`、`llm_configured=true`、契约 15/26/10/12/7。
- [ ] `/console` 能创建线程并完成一次 A 股 Run。
- [ ] 证据可展开到原字段；产物四层非空且引用可点。
- [ ] 页面明确披露"缺三年分部数据"，不声称已完成"收入架构变化"研究。
- [ ] `push/README.md` 里的链接（项目 README、IMPLEMENTATION、AI 记录、测试说明）都能打开。
- [ ] `push/video.mp4` 能播放，画面里没有 `.env`、Key、令牌、持仓或个人信息。

## 7. 已知遗留（不是本次待办，但面试可能被问）

1. 公网 HTTPS / 登录鉴权 / 租户隔离 / 网关限流未做。
2. 三年产品/业务分部附注未接入，"收入架构变化"只能回答总量。
3. `expensive_tool`、`final_review` 两类审批只有接口没有触发点。
4. 5 个白名单 UI 组件未实现（降级卡兜底）。
5. 云端未配置模型单价，`cost_total=0` 无法当成本证据；`search_ticker` 的自由文本参数缺枚举校验；云端 iFinD 令牌当前 401。
6. 工作区里可能残留 `.pytest_tmp/`、`tmp_pytest/`（本次测试产生，已被 `.gitignore` 忽略，可手动删除）。

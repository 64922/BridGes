# 07 — 内联呈现交付状态、失败与真实恢复

**What to build:** 用户直接分辨哪些内容已交付、哪些尚未完成，并能在对应位置执行真实可用的停止、重试或补充操作，而无需阅读本轮结果卡片。

**Blocked by:** 06 — 用单行真实进度集中呈现公开过程

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

保留 ADR-0034 的可信状态与渐进交付语义，替换固定结果卡容器。停止、重试和继续不能由前端合并成虚假的统一成功动作。

## 工作范围

- 将已交付、阻塞、待补齐、等待输入、可信限制和恢复时间映射到正文附近的轻量说明。
- 对渐进快照采用稳定身份和覆盖语义，完整保存已核验部分；停止或失败时仍显示有效正文。
- 根据真实能力给出恢复入口，沿用原命令、幂等与尝试历史。

## 验收标准

- [x] 生成终态与交付可信状态分别判定；部分成功、等待和取消不能一律标为已完成。
- [x] 重复或重放的结果快照不重复追加内容，不把未核验草稿展示成已交付结果。
- [x] 停止后正文保留，已有有效结果可用；后到事件不使停止的运行在界面上继续生成。
- [x] 可重试错误显示准确操作；不可重试或退役能力没有可点击的虚假恢复按钮。
- [x] 服务端冷却未到时按钮不可执行，到时只解除限制，不自动发起请求。
- [x] 重试不清除原用户问题与已有尝试记录；新尝试与旧结果的关系可追溯。
- [x] 正文和状态说明不重复抄写整份结果投影；来源不足的判断留在对应内容附近。
- [x] 部分交付后总结失败、等待补充后续接、网络断连后恢复均有明确可见结果。

## 验证方式

组合正式 API、执行器、SSE 和持久化验证至少完整成功、部分交付后失败、不可重试、冷却、停止竞态、重复事件与用户补充七类路径；故障由受控外部边界注入。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不更改质量门、不让前端宣称可信、不新增自动重试；不为了改界面扩大恢复权限。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

提供状态／操作矩阵、各恢复入口实际命令及重试历史的回归证据。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## 实现交接（2026-10-18）

**状态：** 行为验收通过，待人工复核。

**实际变更**

- `DeliveryStatus` 从固定 `TurnResultCard` 改为正文下方内联说明；删除 `TurnResultCard.tsx` 及其测试，内部路径直接替换，无兼容 shim、双写或长期双实现。
- 状态／操作矩阵：

| 投影／终态 | 正文 | 内联说明 | 操作 |
| --- | --- | --- | --- |
| `running` + 已有交付 | 保留 | 已交付摘要；错误/停止后的迟到快照标为中断，不再宣称运行中 | 停止仍在输入区，不新增自动操作 |
| `complete` + `qualified` + 能力一致 | 保留 | 不显示，避免固定结果卡 | 工具栏“重新生成” |
| `complete` + `evidence_bound` 或请求/实际能力不一致 | 保留 | 可信限制或能力差异说明 | 工具栏“重新生成” |
| `partial` / `blocked` | 保留 | 已交付、阻塞、去重缺口、可信限制、能力差异与恢复说明 | 仅服务端 `recovery.retryable` 且命令真实时显示“重试本轮” |
| `needs_input` | 保留 | 等待原因，不自动续跑 | 用户发送补充消息；无“重新生成” |
| `failed` | 保留 | 未完成终态与恢复说明 | 仅可重试时显示“重试本轮”；无恢复投影时无按钮 |
| `cancelled` / `stopped` | 保留已生成内容 | “本轮已停止，不会自动继续；已生成的内容保留” | 非退役模块、非人味化/SKILL 历史时显示“继续生成” |

- `AssistantActions` 只对完成或部分完成显示“重新生成”；旧模板失败消息（无 `turn_result`）保留“重试”；失败、停止、待补充、阻塞及退役能力的恢复入口统一由内联说明按真实可重试性控制，避免工具栏与说明出现两个不同承诺。
- 重试追溯：把持久化 `attempt_number` 带入历史视图和进行中运行，新尝试头部显示“第 N 次尝试”，原用户问题与 `AttemptHistory` 的旧尝试记录保留。
- 冷却：`RecoveryLine` 在 `available_after` 前禁用按钮；定时器到点只刷新可用状态，不发起任何自动请求。
- 去重与如实呈现：已交付只显示短标签，不复制完整结果投影；`gaps` 去掉与阻塞项重复的原因；未知状态显示“未核实”，不泄露内部英文值。
- 退役能力（`career`、`resources`、`github`）及人味化/SKILL 历史不再提供会被服务端拒绝的“继续生成”。

**接口、协议与数据**

- 未修改服务端、API 合同、数据库 schema 或迁移；复用 `turn-result-v1`、重试命令、冷却时刻与 `attempt_number`。
- 仅新增服务端回归测试，未改 Python 业务实现。

**验证命令与结果**

```powershell
cd apps/web
npm run typecheck   # 通过
npm run test:unit   # 48 files / 387 tests 通过
npm run lint        # 通过，仅 2 条既有 <img> 警告
npm run build       # 通过 23 个页面；Windows junction 导致 standalone symlink EPERM 警告，命令退出成功
```

E2E 使用 Conda `agent` 环境与 `BRIDGES_PYTHON`：

```powershell
$env:BRIDGES_PYTHON='C:\Users\33755\anaconda3\envs\agent\python.exe'
npx playwright test e2e/issue07-delivery-and-recovery.spec.ts   # 3 passed
npx playwright test e2e/issue06-public-progress.spec.ts e2e/issue38-trusted-progress.spec.ts e2e/issue07-delivery-and-recovery.spec.ts --workers=1   # 12 passed
npx playwright test e2e/issue11-chat.spec.ts --grep "重试创建新的助手尝试"   # 1 passed
npx playwright test e2e/issue21-web-search.spec.ts --grep "搜索失败显示可操作重试"   # 1 passed
```

- 正式 API + 执行器 + SSE + 持久化：真实补充“帮我找几篇论文”得到待输入说明；补充“Transformer 架构”后同一轮继续，原问题与等待说明保留。
- 正式 API + 执行器 + SSE + 持久化：真实分块流式生成中按 Esc 停止；服务端为 `stopped`，已生成正文保留，迟到分块未进入停止内容，刷新后一致，未自动新增尝试；点击“继续生成”创建第 2 次尝试，原问题与旧尝试历史保留。
- 页面读取语义：部分交付、不可重试失败、冷却未到、待补充四类终态均不出现虚假恢复；冷却按钮禁用，待输入无“重新生成”；部分交付的重复缺口只显示一次；退役能力在交付说明与工具栏都不出现恢复入口。
- 前端单测覆盖：快照覆盖不追加正文、停止后迟到事件不恢复生成、冷却到点不自动请求、退役能力不显示恢复入口、迟到运行快照不宣称运行中。

Python 定向回归：

```powershell
pytest tests/chat/test_issue38_turn_result.py -q                  # 18 passed
pytest tests/chat/test_issue06_stream_replay_consistency.py -q    # 27 passed
pytest tests/chat/test_issue38_progressive_result.py -q           # 6 passed, 1 known baseline failure
```

**既有失败与未验证项**

- 既有失败：`tests/chat/test_issue38_progressive_result.py::test_formal_graph_publishes_qualified_snapshots_before_terminal`。测试 `_understanding()` 含退役模块，而当前 `AVAILABLE_MODULE_IDS` 为 `commute/paper/tieba`，在 main 上同样失败，非本票引入。
- 既有失败：`tests/chat/test_chat_api.py::test_issue06_chat_messages_populate_current_account_profile` 在基线已失败。
- 全量 pytest 当前不可作为门禁：`tests/career_plan` 4 个测试因已退役的 `CareerBackgroundLoader` 无法导入；忽略该目录后为 328 failed / 5767 passed / 39 skipped，失败集中在既有退役能力、旧路由与项目文件迁移，非本票改动。本票未修改 Python 业务源码。
- `e2e/release-gate.spec.ts` 与 `e2e/issue21-desktop-acceptance.spec.ts` 的学习旅程依赖真实模型/公开检索；本地测试替身未产生“局部学习进展”，后者还在“上传本节书页照片开始预习”既有退役文案处失败，均未计为通过。两处旧 `turn-result` 入口断言已机械替换为 `public-process`，避免引用已删除的测试标识。
- 未采集截图；本票证据以正式 E2E 断言、持久化读取与服务端终态为准。

**遗留限制**

- `e2e/release-gate.spec.ts` 的真实模型/公开检索路径未验证。
- `e2e/issue21-desktop-acceptance.spec.ts` 学习路径未验证，失败点早于本票改动。
- 全量 Python 套件的既有失败未在本票修复，避免扩大范围。
- 冷却时间格式非法时保留原始字符串兜底；正常服务端合同为带时区 ISO 时间。

**回退方式**

- 回退本票分支/合并提交即可恢复 `TurnResultCard` 与旧工具栏行为；仅前端展示、客户端运行态与测试变更，无 schema、迁移或存量数据需要回滚。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现、定向测试与正式 E2E 完成；行为验收通过，状态转为 ready-for-human，等待人工复核。

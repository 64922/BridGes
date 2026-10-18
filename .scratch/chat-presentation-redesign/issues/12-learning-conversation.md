# 12 — 让学习讲解与实际作答反馈连续呈现

**What to build:** 用户在同一消息流中阅读讲解、作答、收到反馈并选择继续、跳过或暂停，学习状态与依据按需查看，不由教学卡片主导阅读。

**Blocked by:** 07 — 内联呈现交付状态、失败与真实恢复；08 — 从真实引用打开右侧资料并继续对话

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

代码已出现学习目标、待验证问题、诊断任务、学习记录纠正、暂停和阶段小结等路径；实施时需核对实际版本，不把旧上下文文档中的“尚未实现”当唯一事实。

## 工作范围

- 按现行学习合同展示教学正文、待回答问题、真实判定与必要下一步操作。
- 把学习路线、证据和记录详情放到轻量入口或右侧资料空间，保留现有纠正、删除和诊断重试能力。
- 当前学习与旧书页会话分开识别，旧会话的历史只读和显式续学边界继续有效。

## 验收标准

- [x] 问题、讲解和反馈按交流顺序连续阅读，不要求用户先打开教学卡才能知道要回答什么。
- [x] 作答反馈来自真实教学与诊断结果；不按原型关键词或固定文案判断掌握。
- [x] 未作答、跳过、未验证、错误与服务失败明确区分，不能用成功样式把未验证计为掌握。
- [x] 直接讲解、跳过、暂停与继续沿用现有合同，必要操作不隐藏在折叠过程内。
- [x] 诊断异步失败时保留已经生成的解释，并展示真实可用的重试入口。
- [x] 学习目标、路线、证据和阶段小结的同轮／跨轮归属正确，打开旧消息不被当前学情覆盖。
- [x] 已有学习记录纠正／删除、采用依据说明及旧会话续学仍可达，不扩张为新学习后台。
- [x] 历史只读旧书页流程不因新版 UI 被自动启动；新会话遵守固定学习模式。

## 验证方式

通过正式学习执行器演示提出目标、实际作答、判定、跳过、暂停续学及诊断失败恢复；另验证旧书页会话读取。业务未就绪的路径如实列出外部阻塞，不用原型答案填补。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不重写教学法、学习证据模型或 ADR-0042 的实现，不重新引入固定书页流程为新默认。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

交付学习信息与操作去向矩阵，以及真实可用、历史只读和未完成能力的区分。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现与自动验证完成，转为 ready-for-human 待人工验收。

### 交接：实际变更

- 后端协议新增 `StudyControlProjection.assistant_message_id` 与 `StudyAdoptionProjection.assistant_message_id`，默认空字符串；`study_session_controls` 通过 `generation_runs.run_id` 取得真实助手消息，采用记录读取既有 `assistant_message_id` 列。同步重新生成 `openapi.json` 与 `packages/contracts/src/generated.ts`。无 schema 变更、无数据迁移。
- 新增 `StudyStatusBar`（ChatHeader 下方单行入口）、默认关闭的 `StudyDetailsPanel`（右侧非模态对话框）、`StudyDetailsContent`、`StudyDiagnosisPanel`、`StudyLegacyHistory`、`StudyTurnStatus`，以及 `lib/study-presentation.ts`。
- 助手回合内联呈现：待答问题、目标形成、跳过/直接讲解、暂停、阶段小结、诊断失败与重试、逐证据学习记录、采用条数。未验证与已核验使用不同 `data-state`，服务失败可重试。
- `buildStudyTurns` 仅使用服务端真实消息标识：待答/小结/控制/采用直接读取助手消息 ID；诊断与暂停从用户消息锚点取该轮最后一条助手消息；记录按 `feedback_message_id` 逐证据归属。历史结论使用当时 evidence 的 verdict 与 source_available，不套用记录最新结论。
- 目标按 `study_goal.source_message_id` 后的真实助手回合标注“本轮形成学习目标”；当前目标/路线仍在详情中展示，打开旧消息不会被当前学情覆盖。
- 旧书页历史、阶段、小结、页序与续学入口收进详情；读取历史不请求续学选项，用户点击“主动续学”后才请求。
- 现有引用/附件/论文/通勤/贴吧面板状态收口到 `use-conversation-panels.ts`，保证同一时刻仅一个右侧面板打开，关闭时焦点回到触发按钮；`page.tsx` 从 570 行降至 345 行。
- 删除旧 `StudyProgress.tsx`，测试改为 `StudyDetailsContent.test.tsx`；新增 `study-presentation.test.ts` 与 `e2e/issue12-learning-conversation.spec.ts`；更新 issue05/06/07/13/21 的入口与断言。

### 交接：学习信息与操作去向

| 学习信息／操作 | 阅读位置 | 操作与去向 |
| --- | --- | --- |
| 讲解、问题、实际反馈、诊断失败解释 | 当前助手消息正文 | 不依赖面板；失败轮内保留解释 |
| 待作答、跳过、未验证、错误、服务失败 | 助手消息内联 `StudyTurnStatus` | 不把未验证显示为成功 |
| 诊断失败重试 | 助手消息内联 | 调用 `POST /chat/conversations/{id}/study/diagnosis/{taskId}/retry` |
| 目标、当前知识点、暂停、待答摘要 | ChatHeader 下方 `StudyStatusBar` | “学习详情”打开右侧面板 |
| 目标、路线、目标暂停/通知 | 详情 `学习目标` 区域 | 路线按需展开；当前状态只读 |
| 待答问题与帮助/资料状态 | 详情 `当前待答问题` | 资料失效明确按未验证处理 |
| 暂停位置、保留问题、阶段小结 | 详情 `暂停状态` / `阶段小结` | 小结原文按需展开 |
| 诊断任务、证据、纠正/删除 | 详情 `学习档案` | 重试、纠正、删除沿用既有合同 |
| 学习记录、采用依据与调整方向 | 详情 `学习档案` / `教学采用的学情依据` | 只展示真实 applied 条目；无 entries 只显示不确定性说明 |
| 旧书页阶段、页序、小结、续学 | 详情 `旧学习记录` | 默认只读；点击“主动续学”才读取选项 |
| 引用、论文、附件、通勤、贴吧 | 右侧对应面板 | 与学习详情互斥，关闭归还焦点 |

### 交接：能力状态

- 真实可用：学习正文连续阅读、内联状态、右侧详情、诊断重试、记录纠正/删除、目标/路线查看、旧历史只读与主动续学。
- 历史只读：旧书页阶段/页序/小结；不会自动启动新流程，不自动请求续学选项。
- 已知限制：当前协议只暴露最新 `study_last_control` 与 `study_summary`，旧回合的这类标记会随最新投影替换而不在详情中重复保留；没有可靠来源时不伪造历史。路线没有逐消息来源字段，因此只在目标/详情中显示当前路线，不做逐回合猜测。
- 外部阻塞：真实 Qwen 学习旅程未执行（缺少凭据）；相关链路以学习执行器 + 受控模型适配器测试覆盖。`issue21-desktop-acceptance.spec.ts` 的真实后端路径本轮未运行，避免与并行 worktree 的服务/端口冲突。
- 既有问题：`tests/chat/test_issue09_adaptive_teaching.py:448` 的 ruff `F841`；全仓 mypy 既有错误。均不在本次修改行。

### 交接：验证命令与结果

- 后端学习链路：`python -m pytest tests/chat/test_issue04_learner_controls.py tests/chat/test_issue04_atomic_binding.py tests/chat/test_issue05_durable_diagnosis.py tests/chat/test_issue06_learning_record_controls.py tests/chat/test_issue07_goals_and_learning_route.py tests/chat/test_issue08_resume_and_goal_switch.py tests/chat/test_issue08_turn_control_and_pre_send_controls.py tests/chat/test_issue09_adaptive_teaching.py -q` → `52 passed`。
- 前端类型：`npm run typecheck` → 通过。
- 前端单测：`npm run test:unit` → 52 files / 406 tests 通过。
- 前端 lint：`npm run lint` → 0 error；仅既有两处 `<img>` warning（`search-page-client.tsx`、`ImageTaskCard.tsx`）。
- 生产构建：`npm run build` → 编译、类型检查与 23 个静态页面生成通过；standalone trace copy 因 worktree `node_modules` junction 报 `EPERM symlink`，不影响构建产物。
- 定向 E2E：隔离 closeout 配置（端口 8920-8924）运行 issue05/06/07/12/13 的 12 个学习测试 → `12 passed`，含 3 种桌面视口与键盘纠正。
- 隔离配置为临时文件，验证后已删除；未占用其他 worktree 的 3000/8000/8025-8027/8143。
- Python 变更文件：mypy 通过；ruff 仅命中上述既有 `F841`。

### 交接：回退方式

- 回退本票提交即可恢复旧 `StudyProgress`/页面状态；新增协议字段为带默认值的可选字段，旧客户端无需迁移。
- 无数据库 DDL/DML 变更，无需数据回退；已合并后如需紧急降级，仅需回退应用与生成契约。

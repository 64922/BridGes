# 06 — 用单行真实进度集中呈现公开过程

**What to build:** 用户生成回答时默认只看到一行真实进度，可主动展开查看公开步骤、来源说明和本轮上下文使用情况。

**Blocked by:** 01 — 整理对话运行职责，保持发送与恢复行为；05 — 让普通回答形成连续可阅读正文

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

当前思考摘要会在生成中自动展开，节点／阶段与各功能过程可能分别占位。需要统一呈现而不泄漏内部编排细节。

## 工作范围

- 按现有公开事件映射用户可理解的进度；明确节点、阶段和无细节时的降级规则，避免多个进度区域重复显示。
- 集中公开过程与已有回答上下文说明入口；不直接暴露整个用户画像或内部来源账本。
- 把折叠状态留在消息视图，生成增量不能自动展开或频繁重置用户选择。

## 验收标准

- [x] 生成中与历史回放都默认收起；用户能用鼠标和键盘展开、收起同一轮过程。
- [x] 进度、数量和耗时仅来自已发生的真实事件；缺数据时省略数值或使用诚实的通用状态，不设置模拟计时。
- [x] 节点／阶段更新和正文生成不出现多个相互矛盾的状态标题。
- [x] 错误、待补充与部分失败仍直接可见，不能被收入折叠区后消失。
- [x] 上下文说明沿用既有公开披露和纠正入口，不展示完整画像、敏感原文或内部推理。
- [x] 上下文未采用、服务关闭、加载失败和历史无披露有明确处理，不长期显示加载中。
- [x] 进度更新不移动焦点；屏幕阅读器只播报必要状态转换。
- [x] 完成、停止及重试后的过程关联到正确尝试，不把后一轮来源追加到前一轮。

## 验证方式

从正式 SSE 与历史投影验证阶段切换、缺事件、迟到事件、展开状态、终态及上下文披露；加入敏感信息不进入公开摘要的负例。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不增加模型调用来编造“思考过程”，不改变画像提取、授权与撤回规则。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

交付公开事件到单行状态的映射、缺数据规则以及上下文说明的可见范围。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## 实现交接（2026-10-18）

**状态：** 行为验收通过，待人工复核；实现提交 `a74a6d67`、`7ca68555`，本记录与截图随最终提交入库。

**实际变更**

- 新增 `PublicProcess`，在 `AssistantMessage` 正文上方统一渲染一行公开过程；生成中与历史回放均默认收起，展开选择保存在消息视图内，增量刷新不重置。
- 单行文案优先级：真实 node → active stage；未知或缺失时为 `正在生成回答…`；错误为 `本轮生成失败`，停止为 `本轮已停止`；完成时仅在存在真实 `thinking.seconds` 时显示 `回答已生成 · 用时 N 秒`，否则为 `回答已生成`，无模拟计时。
- 展开区集中公开步骤、来源说明、工具进度、质量检查与上下文说明；移除旧的独立 `ThinkingSummary`、`GenerationStageLine`、`ContextNoteCard`，不再出现多个进度标题。
- 上下文说明覆盖 `ready / empty / off / error / 历史无披露 / 生成中待披露`；只有 ready 保留既有「这次回答不合适」纠正入口，不渲染画像原文、敏感字段或内部推理。服务端为工单 27 写入 `thinking.tools` 的上下文披露行由前端去重，以专用说明为准。
- 恢复运行不再合成 `queued` 阶段；使用服务端持久化的 `active_run.stage/current_node`。新增 `stopped` 运行态：停止后消息立即标记停止，迟到事件不再改回完成；完成/失败按真实终态写入对应尝试。
- `ProjectionCards` 仍在折叠区外直接展示部分失败、待补充与恢复方式，不被收入折叠区。

**接口、协议与数据**

- 未修改服务端、API 合同、数据库 schema 或迁移；复用既有 `ChatThinkingSummary`、`ContextNoteProjection`、`ChatRunView`、`node/stage` 事件与 `turn_result`。
- 无数据回填；无兼容 shim、双写或并行实现。

**验证命令与结果**

```powershell
cd apps/web
npm run typecheck   # 通过
npm run test:unit   # 45 files / 373 tests 通过
npm run lint        # 通过，仅 2 条既有 <img> 警告
npm run build       # 通过，23 个页面；Windows junction 导致 standalone symlink EPERM 警告，构建退出成功
```

E2E 需在 Conda `agent` 环境并设置 worktree 源码与端口：

```powershell
$env:PYTHONPATH='D:\BridGes\.worktrees\06-public-progress\src'
$env:BRIDGES_PYTHON='C:\Users\33755\anaconda3\envs\agent\python.exe'
$env:PORT='3106'; $env:API_PORT='8106'; $env:API_BASE_URL='http://127.0.0.1:8106'
npx playwright test e2e/issue06-public-progress.spec.ts e2e/issue38-trusted-progress.spec.ts   # 9 passed
npx playwright test e2e/issue05-continuous-answer.spec.ts e2e/issue06-public-progress.spec.ts e2e/issue27-profile-slices-disclosure-feedback.spec.ts   # 5 passed
npx playwright test e2e/issue11-chat.spec.ts --grep "重试创建新的助手尝试"   # 1 passed
```

- 工单 06 正式 SSE 覆盖：历史默认收起与键盘展开、真实流式单行进度与展开保持、停止/失败/历史无披露终态。
- 合并 main（含工单 08 引用面板）后复验：`typecheck` 通过；`test:unit` 47 files / 383 tests 通过；生产构建通过；`issue06 + issue08-citation-viewer + issue27` 共 9 个 E2E 通过。
- `e2e/release-gate.spec.ts` 的入口断言已同步为 `public-process`，但该旅程依赖真实模型与公开检索，本次未运行，不能计为通过。

**证据**

- `.scratch/chat-presentation-redesign/evidence/06/history-collapsed.png`
- `.scratch/chat-presentation-redesign/evidence/06/history-expanded.png`
- `.scratch/chat-presentation-redesign/evidence/06/streaming-collapsed.png`
- `.scratch/chat-presentation-redesign/evidence/06/streaming-expanded.png`

**遗留限制**

- SSE 订阅可能晚于早期 node/stage 事件落库，此时单行使用诚实的通用状态；具体节点/阶段映射由 `chat-run` 与 `PublicProcess` 单测覆盖，E2E 同时接受真实标签与通用降级。
- 未识别的新 node/stage 名称会降级为 `正在生成回答…`，不猜测内部编排。
- 上下文披露去重依赖服务端公开文案中的「你已授权的用户背景信息」标记；若该公开文案改名，需要同步调整并补测试。
- release-gate 真实旅程未验证，见上。

**回退方式**

- 集成后回退本票 merge commit，或按逆序 revert `7ca68555`、`a74a6d67`；仅前端展示与客户端运行态变更，无 schema、迁移或存量数据需要回滚。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现、测试、代码评审修复与证据采集完成；行为验收通过，状态转为 ready-for-human，等待人工复核。

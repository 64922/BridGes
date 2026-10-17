# 02 — 整理消息呈现职责，保留全部结果投影

**What to build:** 当前与历史消息继续完整可读，正文、过程、状态、引用和操作具有清晰的呈现边界，使后续改版能替换容器而不丢数据。

**Blocked by:** None — 可立即开始

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

现有消息列表约 1010 行，集中承载多种功能卡片、消息操作及失败分支；历史消息与实时消息均消费这条路径。

## 工作范围

- 先列出当前每类投影在界面上承载的信息及操作，区分当前可运行能力与仅历史可读能力。
- 拆分用户消息、助手正文、公开过程、交付状态、资料入口和消息操作的职责；保持现有行为直到对应改版票接管。
- 消息协议类型不依赖 React；视图模型在边界组装，避免领域类型直接携带可变 UI 状态。

## 验收标准

- [ ] 普通、学习、论文、贴吧、通勤、检索、上下文说明及媒体消息的现有信息与操作均有去向清单。
- [ ] 退役模块历史结果和旧失败尝试仍可读，退役能力不因整理重新获得启动入口。
- [ ] 同一用户轮次的重试仍按现有尝试关系展示，不静默覆盖先前失败或停止记录。
- [ ] 正文的复制、朗读及已有反馈行为不因组件拆分失效。
- [ ] 正常、空正文、部分交付、失败、停止和旧投影缺字段的历史样本均保留原行为。
- [ ] 拆分后当前消息列表及新增呈现模块各不超过 500 行，不创建按任意字符串动态挂载的插件框架。
- [ ] 无新增网络请求、持久化字段或平行渲染路径；旧内部实现被直接替换。

## 验证方式

对同一组服务端历史投影与流式投影比较重构前后用户可见信息和操作；覆盖各现存投影、历史尝试和空字段。验证跨层读取到真实消息，不仅比较组件快照。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不提前隐藏卡片、不改变结果可信状态、不新增功能或删除历史数据。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

交付信息去向清单和呈现职责说明，供后续票逐项核销；不要求未来代理沿用固定文件布局。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## 实施交接（2026-10-17）

### 实际变更

- 新增 `apps/web/src/lib/chat-message.ts`：`ChatMessage`、`ChatThinking`、`ChatPreviousAttempt` 纯协议类型，不导入 React。
- 新增 `apps/web/src/lib/chat-message-view.ts`：`ChatMessageView extends ChatMessage`，仅增加可选 `body: ReactNode`；历史/流式边界负责组装，组件不再接收领域投影后自行猜测正文。
- `apps/web/src/lib/chat-thread.tsx` 在历史边界完成投影到视图模型的映射，并为历史助手正文生成 `body`（保留原 `--line-height-relaxed`）；`apps/web/src/app/(app)/chat/[conversationId]/page.tsx` 继续在流式边界组装实时视图模型。
- `MessageList` 只保留消息流编排：用户消息交给 `UserMessage`，助手消息交给 `AssistantMessage`；正文、公开过程、交付状态、来源投影、媒体任务和操作行各有独立模块。
- 删除 `apps/web/src/components/bridges/message/types.ts`，未保留 `MessageList` 的旧类型重导出；所有消费端直接引用 `@/lib/chat-message-view`。
- 新增 `MessageList.presentation.test.tsx`，覆盖投影共存、历史尝试/停止/空正文、复制/反馈、退役结果只读、流式行高和空正文附件间距；`chat-thread.test.tsx` 增加跨层投影与历史正文 `body` 断言。
- 无新增网络请求、持久化字段、schema 变更、平行渲染路径或按任意字符串动态挂载的插件框架；所有新增/拆分文件均低于 500 行。

### 信息去向清单

“当前”表示仍可由真实运行路径产生；“历史只读”表示只展示既有结果，不因整理新增启动入口。

| 投影/消息 | 承载信息 | 现有操作 | 能力状态 | 呈现模块 |
| --- | --- | --- | --- | --- |
| 用户消息 | 正文、显式模块标识、照片缩略图、文件卡片（类型/大小/序号/解析状态/下载） | 查看附件、下载原件 | 当前 | `UserMessage`、`MessageAttachments` |
| 助手正文 | 最终正文、流式增量与光标、部分交付正文、失败说明、贴吧场景隐藏正文但保留 `plainText` | 复制、反馈、按条件重试 | 当前 | `MessageBody`、`AssistantActions` |
| 历史尝试 | 此前失败/停止的尝试序号、状态、错误原因 | 展开/折叠 | 历史只读 | `AttemptHistory` |
| 思考摘要 | 可公开步骤、采用证据、工具进度、质量检查、真实耗时 | 展开/折叠 | 当前 | `PublicProcess` |
| 生成阶段 | 当前 stage/node 的中文状态行 | 无 | 当前（仅流式） | `PublicProcess` |
| 上下文说明 | 画像/材料使用披露、加载态、纠正反馈入口 | 展开披露、提交“这次回答不合适”反馈 | 当前 | `SourceProjectionCards` → `ContextNoteCard` |
| 学习模式 | 目标、诊断依据、教学步骤、暂停/结束、初学者开始 | 跳过、重试、开始初学者路径 | 当前 | `SourceProjectionCards` → `TeachingCard` |
| 公网搜索 | 查询、真实来源、失败/取消/降级说明 | 打开来源、重试、取消 | 当前 | `SourceProjectionCards` → `WebSearchCard` |
| arXiv 检索 | 论文引用、摘要/PDF 入口、失败/取消/陈旧提示 | 打开引用、重试、取消 | 当前 | `SourceProjectionCards` → `ArxivPaperSearchCard` |
| 论文模块 | 查询条件、结果、阅读顺序/范围、候选、证据边界 | 打开全文、展开候选、重试 | 当前 | `SourceProjectionCards` → `PaperSearchCard` |
| 贴吧调研 | 已读帖子/楼层、降级说明、官方核验、剔除候选、冲突与证据边界 | 展开分区、跳转引用、重试 | 当前 | `SourceProjectionCards` → `TiebaResearchCard` |
| 校园通勤 | 起终点、路线、课间缓冲、外部调用记录、缺凭据入口、证据边界 | 打开路线/凭据分区、按 `retryable` 重试 | 当前 | `SourceProjectionCards` → `CommuteRouteCard` |
| 分层检索引用 | 引用列表、证据详情、加载/失败、来源打开 | 展开证据、重试详情、打开来源 | 当前 | `SourceProjectionCards` → `RetrievalCard` |
| 模块建议 | 建议模块、理由、原文、歧义提示 | 点击后以原文显式启动模块 | 当前（仅非流式） | `SourceProjectionCards` → `ModuleSuggestionCard` |
| 学习资料推荐 | 图书/视频清单、外部调用记录、证据边界 | 查看；既有 `retryable` 分支保留 | 历史只读（ADR-0041） | `SourceProjectionCards` → `LearningResourcesCard` |
| 职业规划（`career`） | 岗位样本、筛选依据、薪资/技能归纳、证据边界 | 查看；既有 `retryable` 分支保留 | 历史只读（ADR-0041） | `SourceProjectionCards` → `CareerPlanCard` |
| GitHub 推荐 | 仓库链接、功能匹配、维护/许可证据、剔除候选 | 查看；既有 `retryable` 分支保留 | 历史只读（ADR-0041） | `SourceProjectionCards` → `GithubProjectsCard` |
| 人味化结果 | 最终正文、五要素、事实核对、引用/体裁复核 | 查看、展开审计、复制最终正文 | 历史只读（写路径退役） | `SourceProjectionCards` → `HumanizerProcessCard` / `HumanizerResultCard` |
| 生涯规划（`career_planning`） | 分类结果、证据、边界声明、逐项反馈 | 查看、逐项反馈；既有恢复态重试分支保留 | 历史只读（退役写路径） | `SourceProjectionCards` → `CareerPlanningProcessCard` / `CareerPlanningResultCard` |
| 图片任务 | 历史提示词、状态、版本链、图片/资产 | 查看、下载；明确不提供重试/取消 | 历史只读（ADR-0030；Issue 21 退役入口） | `MediaTaskCards` → `ImageTaskCard` |
| 视频任务 | 历史提示词、状态、预览、下载 | 查看、下载；明确不提供重试/取消 | 历史只读（ADR-0030；Issue 21 退役入口） | `MediaTaskCards` → `VideoTaskCard` |
| 交付状态 | 完成/待输入/部分/阻塞/取消、可信状态、能力、已交付/阻塞/缺口、真实恢复、等待原因 | 继续/按恢复条件重试 | 当前 | `DeliveryStatus` → `TurnResultCard` |
| 消息操作 | 复制、重试、有帮助/需改进、复制结果提示 | 复制、反馈、按条件重试；朗读入口保持退役 | 当前 | `AssistantActions` |

### 呈现职责说明

- `MessageList`：仅按角色编排消息流，固定顺序为用户气泡、助手文章。
- `AssistantMessage`：固定助手阅读顺序：历史尝试 → 来源/过程投影 → 思考摘要 → 正文 → 交付状态 → 生成阶段 → 操作行 → 媒体任务。
- `MessageBody`：正文优先级为调用方 `body`，缺省时使用 `plainText`；错误/部分交付/人味化未交付/贴吧隐藏分支保持在正文边界内。
- `PublicProcess`：只承载可公开过程（思考摘要与 stage/node），不展示原始思维链。
- `ProjectionCards`：每个投影使用显式类型条件，无字符串动态挂载；媒体任务单独导出。
- `DeliveryStatus`：只承载已提交回合结果投影；恢复动作仍由页面提供的 `onRetry` 决定。
- `AssistantActions`：复制、反馈与条件重试；人味化写路径和朗读入口不重新出现。
- `AttemptHistory`：只读展示同轮先前失败/停止尝试，不覆盖最新尝试。

### 审查与修复

- Standards 轴：发现 `MessageList` 仍重导出新类型，形成旧入口兼容层；已删除 `message/types.ts` 与全部重导出，消费端直接引用 `@/lib/chat-message-view`。同时把视图模型从组件目录移入 `lib`，消除 `lib/chat-thread` 反向依赖组件目录。
- Spec 轴：发现流式正文误继承历史宽松行高、空正文带附件时丢失间距，已修复并增加回归断言；发现缺少信息去向清单与交接记录，本节补齐。
- 其余判断项（投影卡参数较多、`DeliveryStatus` 仅转发、错误提示与正文同文件）属于本票明确要求的职责边界或为避免扩大改动而保留，未按“代码外形相似”提前抽象。

### 验证命令与结果

- `npm run typecheck`：通过。
- `npm run test:unit`：合入 main 的 issue-03 后 40 个测试文件、340 项通过。
- `npm run lint`：通过；仅保留 2 条既有 `<img>` 警告。
- `npm run build`：Next.js 编译、类型检查和 23 个静态页面生成通过；worktree 使用 Junction 的 `node_modules`，standalone trace 复制出现既有 `EPERM symlink` 警告。合并到 main 后在真实依赖目录复跑。
- Playwright（`BRIDGES_PYTHON` 指向 conda `agent` Python）：`issue06-stream-replay-consistency`、`issue38-trusted-progress`、`issue08-article-result-projection`、`issue30-dictation-reading` 的相关 18 项全部通过，覆盖流式/重放、停止、终态历史、部分交付、旧投影、正文复制和朗读入口退役。
- 重构前后对照：临时 parity 测试覆盖普通、学习、论文、贴吧、职业、GitHub、学习资料、通勤、模块建议、检索引用、人味化历史结果、生涯规划历史结果、图片/视频历史任务、历史尝试、流式、停止、部分失败、空正文和旧字段缺省；重构前记录基线，重构后同组通过，临时文件已删除。
- 集成：执行期间 main 已合入 issue-03（`76e0c8d9`）；本分支合入 `main@58882a15` 无冲突，并在合并后重跑 typecheck、单测、lint、build 与上述 18 项浏览器测试。
- `python -m pip check`：通过。
- `python -m mypy src`：分支 `141 errors in 23 files`；在 `main` 同命令得到相同 `141 errors`，属既有失败，未由本票引入。
- `python -m pytest -q`：两侧均在收集阶段因 4 个 career_plan 测试导入不存在的 `CareerBackgroundLoader` 中断（`main` 同为 4 个 collection errors、6255 tests collected）；未将未执行完的套件标为通过。

### 限制与回退

- 退役的 `resources`/`career`/`github`/`career_planning` 卡片仍保留既有 `retryable` 回调分支；本票未新增入口，后续退役清理票应按 ADR-0041 核销，不应把“历史可读”误写成“能力仍可启动”。
- worktree 的 standalone trace `EPERM` 是 Junction 环境限制；生产构建的编译与页面生成已通过。
- 回退：还原本票提交即可；没有 schema、API、数据迁移或持久化字段变更，历史消息与投影不受影响。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。

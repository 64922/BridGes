# 03 — 整理输入区职责，保持发送、附件与听写能力

**What to build:** 用户仍能输入、换行、添加附件、选择现有模块及使用已有听写能力；输入区可以在后续改版中调整布局而不重写发送逻辑。

**Blocked by:** None — 可立即开始

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

现有输入组件约 1070 行。需要先隔离输入行为、附件草稿、功能选择与工具栏呈现，防止视觉重排改变请求含义。

## 工作范围

- 在保持当前界面的前提下，梳理文本、中文输入法、提交中状态、附件草稿和能力可用性的所有者。
- 分离现有输入与附件操作以及工具栏呈现，保留既有首轮和后续轮的回调语义。
- 保留模块建议与用户显式选择进入请求的既有规则，确认日常陪伴只有三个可启动模块。

## 验收标准

- [ ] Enter 发送、Shift+Enter 换行和中文输入法确认不误发送；空输入和重复提交遵守当前合同。
- [ ] 发送失败保留可重发草稿；已提交附件不串到下一轮，移除草稿不删除已绑定历史附件。
- [ ] 文件／照片上传、解析状态和失败重试仍使用真实账户级草稿与服务响应。
- [ ] 论文、通勤、贴吧的选择及清除行为不变；切换到学习模式不携带无效日常模块选择。
- [ ] 已有听写开始、停止、结果回填与不可用反馈不退化；不新增持续监听。
- [ ] 文本、附件和模式共同决定的请求身份未被简化为只有文本。
- [ ] 输入区组合及本票拆分模块均不超过 500 行，不通过全局可变对象在组件间共享草稿。

## 验证方式

覆盖中文输入法、失败重发、双击发送、同文本换附件、模块选择清除，以及听写可用与不可用状态；上传检查经过真实草稿接口与绑定路径。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不引入新编辑器、不更改上传限制、附件用途或模块派发规则，不重新设计听写服务。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

说明提交成功／失败的草稿处理合同，以及附件、模式和能力状态的入口。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。

## 实现交接（2026-10-17）

**分支／工作树：** `chat-presentation-03-composer-boundaries`，worktree `.worktrees/chat-presentation-03-composer-boundaries`，基点 `main`（4100e87e）。

### 实际变更

- `Composer.tsx` 只保留输入区组合与发送编排（233 行）；正文/IME/预填由 `useComposerText` 拥有，附件草稿由 `useComposerAttachments` 拥有，听写状态机由 `useDictation` 拥有，附件草稿托盘与工具栏分别由 `ComposerAttachments`、`ComposerToolbar` 呈现。
- 拆分文件行数：`useDictation` 266、`ComposerToolbar` 261、`ComposerAttachments` 247、`useComposerAttachments` 221、`useComposerText` 109、`composer-styles` 15；`Composer.test.tsx` 496。均不超过 500 行。
- 删除旧单体内联实现，无兼容 shim／双写；草稿只存在于 `useComposerAttachments` 的组件内状态与 ref，跨组件不使用全局可变对象。
- 新增行为保护：Enter 在 `isComposing`、keyCode 229 或组合状态期间不发送；发送在途用 ref 防止双击重复提交；发送成功只按本轮 `object_id` 过滤迟到的草稿恢复响应，纯文字发送仍可恢复其他账户草稿；切到学习模式时通知宿主清空无效的日常模块选择。
- 接口变化：`Composer` 对外 props 与 `onSend(text, attachmentIds)` 合同不变；`prefill` 类型改由导出的 `ComposerPrefill` 表达。附件、模式与能力状态的宿主入口不变（`conversationId`、`asr`、`variant`、`moduleId`/`onModuleChange`、`mode`）。

### 草稿与发送合同

- 发送中：`canSend` 要求正文或草稿存在、听写 idle、上传计数为 0，且不在提交中；`generating` 仍由宿主控制。
- 发送失败（`onSend` 返回 `false` 或抛错）：正文、附件草稿与解析状态原地保留，可重发。
- 发送成功：清空正文与草稿；服务端已把草稿绑定为会话附件。移除按钮只对未发送草稿调用删除接口，不会删除已绑定历史附件。
- 附件、模式与能力入口：附件选择/拖入/粘贴 → `useComposerAttachments` → 账户级 `/chat/attachment-drafts`；模块选择与清除 → `ComposerToolbar`／chip → 宿主 `moduleId`；能力可用性 → `asr` 与 `variant`；听写开始/停止/重试/取消 → `useDictation`，仅在点击入口后申请麦克风。

### 验证

在 Conda `agent`（Python 3.11.15、Node 24）执行：

| 命令 | 结果 |
| --- | --- |
| `python -m pip check` | No broken requirements found |
| `python -m pytest -q tests/chat/test_v2_05_photo_attachments.py tests/chat/test_v2_06_file_attachments.py tests/chat/test_chat_attachments.py tests/chat/test_issue04_atomic_binding.py tests/chat/test_issue04_attachment_contract.py` | 49 passed；真实 API + SQLite 覆盖草稿上传、幂等重放、原子绑定、已绑定不误删与跨账户隔离 |
| `npm run typecheck` | 通过 |
| `npm run test:unit` | 39 files / 333 tests passed |
| `npm run lint` | 0 error；2 条既有 `no-img-element` warning（`search-page-client`、`ImageTaskCard`） |
| `npm run build` | 合并后在 main 复核：编译成功并生成 23 个静态页面，无 standalone trace 警告（worktree 的 EPERM symlink 仅由 node_modules junction 引起）；main 上 `typecheck`、`lint`、`test:unit` 同步复跑通过 |
| `npx playwright test e2e/issue13-new-chat-composer.spec.ts e2e/issue30-dictation-reading.spec.ts` | 13 passed / 5 failed；同一命令在 main 基线上同为 13 passed / 5 failed，失败集合完全相同（见下） |

新增测试：`Composer.send-contract.test.tsx`（Enter/Shift+Enter、中文输入法、空输入、双击、失败重发、同文本换附件、迟到草稿恢复、模式清除）与 `Composer.dictation.test.tsx`（不可用/首轮前禁用、开始/停止/回填不自动发送、网络失败原样重试、取消不提交）；既有 `Composer.test.tsx` 迁移到共享 fixture，继续覆盖附件、解析状态、模块与听写能力入口。本票为行为整理，无视觉改动，未新增截图；浏览器证据为上述 e2e 列表。

### 既有失败与剩余限制

- 全量 `python -m pytest -q` 在收集阶段被 4 个既有 `tests/career_plan/test_issue29_*` 阻塞：`CareerBackgroundLoader` 无法从 `bridges.api.main` 导入；同一收集错误在 main 复现，与本票无关。
- `python -m mypy src` 报 141 个既有错误（23 个文件）；本票未改 Python 源，错误与前端改动无交集。
- e2e 五个既有失败（main 同样失败）：新聊天页期望隐藏 file input 为 0、学习模式按钮首轮前 disabled 的三条用例，以及听写纯键盘 Shift+Tab 路径未回填；本票未改变这些宿主行为，13 条通过用例覆盖录音、取消、权限拒绝、网络重试、入口可用性与首轮合同。
- 真实外部 ASR 供应商未在本票调用；听写协议由既有替身与后端固定快照测试覆盖，和当前证据等级一致。
- 请求身份：`Composer` 仍把正文与按页序附件 ID 交给宿主；模式/模块由宿主状态决定，未简化为仅文本；既有 page/NewChatHome 的幂等键逻辑未在本票改写。

### 回退

本票只改前端组件与测试，无 schema、API、生成类型或数据迁移。回退时 revert 本票两个提交（`6ba6c70b`、`0888c77d`）即可恢复单体 Composer；历史消息、附件绑定与草稿数据不受影响。

# 08 — 从真实引用打开右侧资料并继续对话

**What to build:** 用户点击回答中的真实引用，在默认关闭的右侧面板查看对应资料；仍能阅读和继续原对话，并在资料不可访问时获得明确反馈。

**Blocked by:** 04 — 交付新对话布局与真实首轮发送；05 — 让普通回答形成连续可阅读正文

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

以现有会话内检索引用及引用详情读取作为首个完整场景，按账户、会话、消息和引用身份关联。它是后续领域资料呈现的真实用例，不先建空面板框架。

## 工作范围

- 把真实检索引用变为正文引用标记或轻量来源入口，右侧读取现有引用详情。
- 面板与对话分别滚动，用户主动打开；保持主回答可独立理解和输入可达。
- 将资料身份与 UI 选中状态分开，处理请求取消、迟到结果、权限变化和切换账户。

## 验收标准

- [x] 新对话及重开历史时面板默认关闭；收到引用或生成新消息不会自动打开。
- [x] 引用定位到该轮真实来源及页码／章节／片段，不能用当前选中文件替换历史来源。
- [x] 同一会话继续提问时已打开资料保持可读；切换会话或账户清除旧面板内容和未完成请求。
- [x] 快速点击不同引用只呈现当前选择；后返回的旧请求不能覆盖新资料。
- [x] 加载、无资料、读取失败、已删除、权限变化分别呈现；读取失败可重试，权限不允许时不保留敏感片段。
- [x] 面板关闭不丢对话滚动位置；Esc 和关闭按钮可用，焦点回到触发处或合理替代位置。
- [x] 正文结论与必要限制可独立阅读，不用打开面板才能得到核心答案。
- [x] 未经核实的文字编号不能伪造成可打开引用；外部链接遵守安全 URL 规则。
- [x] 三种目标桌面尺寸及缩放下正文、面板、输入框互不遮挡；必要时折叠侧栏。

## 验证方式

真实账户 A 的引用成功读取、账户 B 拒绝、原文删除／授权变更、切换后迟到响应、并排追问与历史重开。以正文点击到详情响应为完整路径。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不建设通用网页浏览器、不自动抓取任意网址，不新增笔记持久化、多标签管理或 PDF 编辑功能。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

记录资料身份与请求生命周期、现有引用 API 复用情况和后续领域内容的最小接入方式。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现与验证完成，转 ready-for-human；交接见下节。

## 交接记录（2026-10-18）

**分支与基线：** `08-citation-viewer`，基于 main `aefc4bf5`。本票只改前端，未触及后端、schema、协议或依赖。

**实际变更**

- `AssistantMessage` 在正文之后渲染来源入口；`RetrievalCard` 不再内联读取或展开，只把真实 `retrieval.citations` 上报给页面。正文中的 `[n]` 保持纯文本，不能伪造成可打开引用。
- 新增 `CitationViewerPanel`：默认不渲染；用户点击后按 `conversation / message / citation` 三元组读取现有引用详情；面板与对话独立滚动；Esc 与关闭按钮均可关闭。
- 状态覆盖 loading、missing（404）、error（可重试）、accessible、deleted、permission_changed 与无片段。原文删除或授权变化时不保留旧 snippet，也不提供下载入口。
- 下载入口只接受同源相对路径或 `https://`；外部地址使用 `target="_blank"` 与 `rel="noopener noreferrer"`，拒绝 `javascript:` 和协议相对地址。
- 页面持有选中状态与触发按钮；关闭后按“触发按钮 → 输入框 → 主内容区”顺序恢复焦点，并统一使用 `preventScroll`。切换会话清空选择，账户切换随页面卸载；`AbortController` 加 key 重挂载保证迟到响应不覆盖新选择。
- `ChatThread` 的自动滚动只在消息内容变化时触发，面板开关不会把对话拉回底部。

**接口与资料身份**

- 复用 `GET /conversations/{conversation_id}/messages/{message_id}/citations/{citation_id}` 与 `CitationDetailProjection`；仅给 `getCitationDetail` 增加可选 `AbortSignal`，不改后端合同。
- 资料身份固定到具体消息的具体 citation，不使用“当前选中文件”；后续领域内容只需提供同一引用详情投影即可接入。

**验证**

- `npm run test:unit`：47 文件 / 373 测试通过。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅两处既有 `<img>` 警告。
- `npm run build`：成功；`.next/standalone` 复制 node_modules 时因工作树 junction 出现 EPERM 警告，构建产物正常，属工作树环境限制。
- `npx playwright test -c playwright.issue08.config.ts`（临时配置）：8/8 通过，覆盖默认关闭与并排追问、快速切换迟到响应、重试／删除／授权变化／无资料、Esc／关闭焦点与滚动、切换会话、1280／1440／1920 三尺寸及 125% 缩放等效视口。
- `pytest tests/retrieval/test_retrieval_service.py tests/retrieval/test_v2_07_knowledge_base_retrieval.py tests/chat/test_retrieval_chat.py -q`：40 通过、2 失败。失败用例 `test_scope_attachment_then_project_then_kb` 与 `test_citation_detail_project_layer_uses_project_download_entry` 在 main 上同样失败：项目层文件源已按 V2 Issue 06 退役，测试仍走旧 `project_id` 入队，与本票前端改动无关。
- E2E 在开发模式 StrictMode 下会双挂载 effect，失败／重试计数用例允许前两次失败；生产构建只发起一次详情请求。

**证据**

- `.scratch/chat-presentation-redesign/evidence/08/panel-1280x720.png`
- `.scratch/chat-presentation-redesign/evidence/08/panel-1440x900.png`
- `.scratch/chat-presentation-redesign/evidence/08/panel-1920x1080.png`
- `.scratch/chat-presentation-redesign/evidence/08/panel-1280x720-zoom125.png`
- 截图显示正文在前、来源入口在后，面板与输入框互不遮挡；125% 缩放下对话区可继续滚动。

**遗留限制**

- 账户切换未做独立浏览器用例，由页面卸载加面板卸载中止请求覆盖（单测验证 `AbortSignal` 被 abort）。
- 项目层引用详情后端测试为既有失败，需由后续任务单独修复，不在本票范围。

**回退**

- 回退本票前端提交即可恢复旧内联引用卡；无数据迁移、schema 变更或协议兼容负担。

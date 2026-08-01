# 23 — 交付学习模式教学编排与证据充足性门
Status: ready-for-agent
Blocked by: [14](./14-deliver-conversation-modes-and-thinking-summary.md), [20](./20-deliver-layered-retrieval-and-citations.md), [21](./21-deliver-duckduckgo-private-web-search.md), [22](./22-deliver-arxiv-mcp-paper-search.md)
Covered requirements: CHAT-01, CHAT-06, CHAT-09, CHAT-10, A-01, BONUS-01, BONUS-02, SCORE-01, SCORE-02, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0002](../../../docs/adr/0002-tiered-profile-writing-and-emotion-boundary.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把学习模式实现为全程发生在统一聊天流中的因材施教老师。智能体根据用户明确学习意图、当前会话、所属项目、可用材料和已有学习证据形成当前目标与教学步骤，使用解释、例子、类比、提问和适量测验，并根据真实作答调整后续内容。不得恢复固定课程树或旧项目制教学工作台。

每个教学轮次先运行证据充足性门：检查当前附件、项目文件和已授权全局知识库。用户没有上传材料，或材料过时、冲突、证据不足、无法覆盖教学目标时，必须自动使用 DuckDuckGo、arXiv 或二者补充教学必需知识。联网仍不足时明确暴露缺口，不能用模型记忆伪装可靠教学依据。

## Acceptance criteria

- [ ] 学习模式收到学习意图后，在聊天中确认或推导本轮目标、当前水平假设、教学步骤与理解检查方式。
- [ ] 讲解能依据用户回答调整深度、例子和下一问；测验不过量，且用户可跳过、追问或切换模式。
- [ ] 测验题、用户作答、评价依据和知识状态变化形成可追溯学习证据；模型自述不得直接标记用户“已掌握”。
- [ ] 每轮先检查当前附件、项目文件和授权知识库，并输出结构化“充分/不足/冲突/不可用”判断及理由。
- [ ] 三层本地材料均不存在时自动联网；材料存在但过时、冲突或覆盖不足时也自动联网补充。
- [ ] 普通公开知识优先使用 DuckDuckGo，论文型问题使用 arXiv；必要时可组合，但不得把普通网页伪装成论文证据。
- [ ] 联网触发、搜索过程、来源和引用对用户可见；私人原文仍遵守最小查询披露边界。
- [ ] 联网无结果、失败或证据仍不足时，回答明确说明缺口、可继续采取的步骤和无法可靠断言的部分。
- [ ] 切回日常陪伴后，后续消息停止强制教学结构；历史教学消息与模式切换标记保持不变。
- [ ] 教学卡片与消息流具有中文 loading、empty、error、permission 和 recovery 状态，可取消或重试且不重复消息。

## Verification

- 在 Conda `agent` 环境运行证据充足性状态机、模式边界、测验证据和知识状态更新测试。
- 使用固定场景覆盖：无本地材料、本地充分、本地不足、本地冲突、DuckDuckGo 失败、arXiv 无结果和联网后仍不足。
- 运行前端类型检查和桌面 E2E，验证教学全程在聊天中完成、过程可见、引用可开、失败可恢复和模式可切换。
- 对样例教学回答人工核对关键科学主张均有来源，且不存在旧课程节点页面。

## Non-goals

- 不创建固定课程目录、节点审批或项目制教学工作台。
- 不把模型记忆、单次情绪或未确认画像当作持久学习证据。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 14：对话模式与思考摘要](./14-deliver-conversation-modes-and-thinking-summary.md)
- [Issue 20：分层本地检索与引用](./20-deliver-layered-retrieval-and-citations.md)
- [Issue 21：DuckDuckGo 隐私联网搜索](./21-deliver-duckduckgo-private-web-search.md)
- [Issue 22：受限内置 arXiv MCP](./22-deliver-arxiv-mcp-paper-search.md)

## Comments

本 Issue 先使用用户明确陈述与可追溯学习证据；画像最小切片和反馈闭环将在 Issue 27 接入，不形成反向依赖。

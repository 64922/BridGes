# 21 — 交付 DuckDuckGo 隐私联网搜索
Status: ready-for-agent
Blocked by: [20](./20-deliver-layered-retrieval-and-citations.md)
Covered requirements: EXT-01, CHAT-09, BONUS-01, SCORE-02, MODEL-03, DESKTOP-01
ADRs: [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在聊天工具流水线中接入无需用户配置 Key 的 DuckDuckGo 搜索。日常陪伴模式只在用户明确要求联网、问题明显依赖最新信息或确有事实核查需要时启用；学习模式的强制触发由后续教学证据门决定。搜索请求必须在本地提炼和脱敏，只向 DuckDuckGo 发送完成检索所需的查询词，不发送原始私人文档、完整画像、QQ 邮箱、用户名或秘密凭据。

联网过程在思考摘要与工具卡中可见，最终回答提供可点击 URL、标题、来源站点和访问时间。联网失败或证据不足必须明确暴露，不得退回模型记忆并伪装成搜索结论。

## Acceptance criteria

- [ ] 日常陪伴模式仅在明确联网请求、时效性问题或事实核查需要时触发 DuckDuckGo，并向用户显示触发原因。
- [ ] 本地查询规划器从问题中生成最小搜索词；测试证明私人附件原文、画像、账户标识和凭据不会进入请求。
- [ ] DuckDuckGo 不需要在 `.env`、账户设置或部署参数中配置密钥。
- [ ] 搜索中显示中文进度和搜索词概述；完成后显示来源标题、站点、URL、摘要和访问时间。
- [ ] 最终回答中的联网主张可追溯到真实返回结果，点击引用打开对应网页，不伪造 URL 或来源。
- [ ] 搜索结果回到本地后才与授权私人上下文组合，私人内容不反向发送给搜索服务。
- [ ] 超时、限流、无结果、解析失败和断网分别显示可理解错误及重试入口，不被呈现为空白成功。
- [ ] 搜索请求与审计仅记录必要元数据和数据类别，默认不额外复制保存敏感查询正文。
- [ ] 用户可在本轮取消搜索；取消或重试不会重复发送用户消息或残留“正在搜索”状态。
- [ ] 工具卡具有中文 loading、empty、error、permission 和 recovery 状态，并符合桌面键盘操作要求。

## Verification

- 在 Conda `agent` 环境用可捕获请求的确定性假服务测试触发条件、查询脱敏、超时、无结果和取消。
- 运行隐私测试，将 QQ 邮箱、画像、私人文档句子和测试凭据放入上下文，确认外发请求均不包含这些值。
- 运行前端类型检查和桌面 E2E，覆盖过程可见、引用打开、失败重试和取消。
- 通过显式启用的 DuckDuckGo 冒烟测试验证无需 Key 的真实结果；测试日志不得保存完整私人查询。

## Non-goals

- 不在本 Issue 实现 arXiv 论文专用检索或学习模式强制联网判定。
- 不把网页搜索结果自动保存到个人知识库。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 20：分层本地检索与引用](./20-deliver-layered-retrieval-and-citations.md)

## Comments

DuckDuckGo 是固定搜索供应商，不属于用户可更换模型；页面不得出现供应商选择器或 Key 输入框。

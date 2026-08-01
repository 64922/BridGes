# 20 — 交付分层本地检索、融合排序与引用
Status: ready-for-agent
Blocked by: [14](./14-deliver-conversation-modes-and-thinking-summary.md), [17](./17-deliver-document-ingestion-and-versioned-index.md), [19](./19-deliver-folder-learning-projects.md)
Covered requirements: CHAT-09, CHAT-10, KNOW-01, PROJ-01, MODEL-02, BONUS-01, SCORE-02, DESKTOP-01
ADRs: [0008](../../../docs/adr/0008-contract-locked-embedding-alias.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在真实对话中交付三层本地检索：当前对话附件、当前学习项目文件、用户已授权的全局知识库。每层使用 FTS/BM25 与 `text-embedding-v4` 向量结果融合，并通过独立候选配额避免某一来源淹没其余材料。检索结果必须追溯到原文件、页码或章节，并以消息引用和可展开证据详情呈现。

检索器输出证据充足性信号供后续教学门使用；本 Issue 只处理本地来源，不使用模型记忆伪造成“已检索依据”。用户可在发送前关闭全局知识库使用，但当前明确附加的文件仍视为本轮授权。

## Acceptance criteria

- [ ] 每轮按“当前附件 → 当前项目文件 → 已授权全局知识库”确定候选作用域，且只查询当前账户资源。
- [ ] 关键词与向量结果按固定、可测试的融合合同排序，各来源拥有独立候选配额和去重规则。
- [ ] 用户可查看本轮启用的来源层并关闭全局知识库；关闭后请求、日志和引用均不包含其候选。
- [ ] 每个引用包含来源类型、文件名、页码或章节、可验证片段和授权打开原文的入口。
- [ ] 点击引用精确打开当前账户可访问的证据位置；对象已删除或权限变化时显示安全中文状态。
- [ ] 回答与思考摘要清楚区分“引用依据”和“模型组织说明”，不得生成不存在的本地文件或页码。
- [ ] 无命中、命中冲突、覆盖不足和索引不可用均输出结构化充足性结果，不以空候选表示成功。
- [ ] 检索查询、片段和最终最小上下文遵守账户级授权；不会读取其他账户相同哈希文件。
- [ ] 消息中的检索与引用卡具有中文 loading、empty、error、permission 和 recovery 状态，可重试且不重复用户消息。
- [ ] 刷新和重启后引用仍指向相同来源版本；索引重建不会让历史引用静默漂移。

## Verification

- 在 Conda `agent` 环境运行作用域、BM25/向量融合、来源配额、去重、冲突和充足性合同测试。
- 用相同文件跨两个账户、项目内外文件和关闭知识库开关验证隔离及授权。
- 运行前端类型检查和桌面 E2E，覆盖检索过程、引用展开、精确跳转、无依据、索引失败与重试。
- 固定测试集核对引用页码/章节与原文一致，并验证不存在引用幻觉。

## Non-goals

- 不在本 Issue 调用 DuckDuckGo、arXiv 或其他联网来源。
- 不实现独立证据工作台，也不新增重排模型。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 14：对话模式与思考摘要](./14-deliver-conversation-modes-and-thinking-summary.md)
- [Issue 17：文档摄取与版本化索引](./17-deliver-document-ingestion-and-versioned-index.md)
- [Issue 19：文件夹式学习项目](./19-deliver-folder-learning-projects.md)

## Comments

来源顺序表达作用域优先级，不要求机械拼接所有来源。融合结果仍需保留来源配额和完整出处。

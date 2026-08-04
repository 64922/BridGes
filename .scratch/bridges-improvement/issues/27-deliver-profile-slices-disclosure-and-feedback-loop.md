# 27 — 交付最小画像切片、披露与反馈闭环
Status: ready-for-human (验收通过，等待用户确认)
Blocked by: [20](./20-deliver-layered-retrieval-and-citations.md), [23](./23-deliver-learning-mode-teaching-gate.md), [26](./26-deliver-profile-candidates-and-permissioned-updates.md)
Covered requirements: PROFILE-01, A-01, A-02, CHAT-09, IMP-01, BONUS-02, SCORE-01, SCORE-02, DESKTOP-01
ADRs: [0002](../../../docs/adr/0002-tiered-profile-writing-and-emotion-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0021](../../../docs/adr/0021-auditable-profile-not-animated-avatar.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

为日常陪伴与学习模式建立任务级最小画像切片编译器。每轮只选择与当前任务相关、仍有效且授权范围匹配的记录；用户可在发送前关闭画像使用。发送到 Qwen 的上下文说明只包含必要切片，不上传完整画像中心或未确认候选。

在回答中提供可展开的“本次上下文说明”，列出使用的画像类别、材料类别和用途。反馈入口让用户区分“这次回答有问题”和“画像记录有误”，并完成“回答—反馈—修正画像或策略—下一轮验证”的 A 方向多轮闭环。

## Acceptance criteria

- [x] 切片编译按任务、模式、适用范围、授权、敏感度、冻结/撤回状态和有效期筛选，只返回最小必要记录。（模式即本轮任务类型，维度白名单为可解释筛选代理；单维度≤2、整卷≤6、值截断 80 字；SENSITIVE 默认不进聊天切片）
- [x] 未确认候选、已撤回、已冻结禁止调用或范围不匹配的记录不会进入模型上下文。（编译器过滤 + 测试矩阵覆盖）
- [x] 用户可在发送前关闭本轮画像使用；关闭后模型请求、审计和上下文说明均不含画像切片。（use_profile 开关；请求捕获/审计/披露三处验证）
- [x] 每条使用画像的回答提供可展开中文说明，展示类别、用途、来源记录链接和本次使用时间，不暴露隐藏提示或原始思维链。（ContextNoteCard 可展开披露卡，深链 /account/profile?assertion=…）
- [x] 回答只披露材料类别及可访问引用，不把完整私人材料、完整画像或秘密凭据复制到审计日志。（PROFILE_SLICE_USED 审计只记类别/切片 ID/条目数/授权快照）
- [x] 用户可标记“回答不合适”并给出偏好反馈，也可标记“画像有误”并直接修正、冻结或撤回对应记录。（反馈表单 + 修正动作；幂等去重）
- [x] 画像修正后的下一轮回答使用新版本；历史回答保留当时切片版本，能够回放修正前后差异。（多轮回放测试：版本 1→2，历史快照保留）
- [x] 学习模式使用相关目标、知识状态和学习证据调整教学；日常模式只使用必要偏好与情境，不强制教学结构。（模式维度映射 companion/study 分离）
- [x] 多账户并发和后台调用均按稳定账户 ID 编译切片，不共享缓存或审计内容。（scoped() 账户隔离 + 隔离测试）
- [x] 上下文说明与反馈流具有中文 loading、empty、error、permission 和 recovery 状态，失败不丢失用户反馈。（五态披露 + 反馈草稿保留/重试/修正提示）

## Verification

- 在 Conda `agent` 环境运行任务/范围矩阵、撤回冻结、过期、敏感候选排除、最小化和账户隔离测试。
- 捕获模型适配器请求，确认只包含期望切片，关闭画像后不出现任何画像内容。
- 运行前端类型检查和桌面 E2E，覆盖上下文说明、回答反馈、画像修正、下一轮适配与失败恢复。
- 用固定多轮场景展示“初始回答—用户纠正—画像更新—后续回答改变”，并记录相关切片召回、无关注入和授权违规结果。

## Non-goals

- 不显示系统提示、原始思维链或逐令牌内部推理。
- 不根据单次反馈自动扩大画像授权，也不保证所有回答都必须使用画像。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 20：分层本地检索与引用](./20-deliver-layered-retrieval-and-citations.md)
- [Issue 23：学习模式教学证据门](./23-deliver-learning-mode-teaching-gate.md)
- [Issue 26：画像候选与分级许可更新](./26-deliver-profile-candidates-and-permissioned-updates.md)

## Comments

“越来越懂用户”必须由用户可见的反馈闭环与可复现测试证明，不能以注入更多画像文本作为替代指标。

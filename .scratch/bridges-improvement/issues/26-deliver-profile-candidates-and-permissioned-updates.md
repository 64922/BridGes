# 26 — 交付画像候选与分级许可更新
Status: ready-for-agent
Blocked by: [11](./11-deliver-persisted-streaming-chat.md), [25](./25-deliver-profile-center-and-static-avatar.md)
Covered requirements: PROFILE-01, A-01, A-02, IMP-01, BONUS-02, SCORE-01, DESKTOP-01
ADRs: [0002](../../../docs/adr/0002-tiered-profile-writing-and-emotion-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0021](../../../docs/adr/0021-auditable-profile-not-animated-avatar.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把聊天观察转化为可治理的画像候选与更新流程。用户明确说“记住”时，按其指定类别和适用范围写入证据化记录；阶段目标、兴趣偏好和表达习惯等低风险信息，只有用户预先为对应类别与范围开启自动更新许可时才可自动写入，并立即通知和提供一键撤回。

情绪趋势、重要经历、正在面对的问题及其他敏感推断只能形成候选，必须由用户明确确认后才能跨会话使用。单次情绪仅作为当前会话情境信号，结束后不进入长期画像。授权范围只能由用户设置，智能体不得从沉默、语气或历史行为推断授权。

## Acceptance criteria

- [ ] 聊天中的明确“记住/不要记住/只在本对话使用”意图映射到可见类别、范围和证据，不依赖模糊模型猜测。
- [ ] 用户可按画像类别与适用场景开启、查看和关闭低风险自动更新许可；默认关闭且授权不能由模型代开。
- [ ] 获许可的低风险目标、兴趣和表达习惯可自动写入，每次都有中文通知、来源和一键撤回。
- [ ] 情绪趋势、重要经历、正在面对的问题及敏感推断只进入候选箱，未确认前不得编入跨会话画像切片。
- [ ] 单次情绪仅保存在当前会话情境中；会话结束、重新登录或新建对话后不会成为长期画像事实。
- [ ] 候选页面支持确认、编辑后确认、拒绝和批量处理，并显示为何提出、来源消息和将要适用的范围。
- [ ] 冻结类别不接收自动写入；撤回许可只停止未来自动更新，不静默删除既有记录。
- [ ] 重复观察通过稳定去重与证据合并处理，不因同一句话重复生成候选或通知。
- [ ] 所有候选、许可、写入、拒绝和撤回均按账户隔离并写入审计，后台任务不能串号。
- [ ] 通知与候选 UI 具有中文 loading、empty、error、permission 和 recovery 状态，失败操作可安全重试且不重复写入。

## Verification

- 在 Conda `agent` 环境以标注对话集测试明确记忆、低风险许可、敏感候选、单次情绪和授权禁止推断。
- 运行幂等、冻结、许可撤回、候选拒绝、事务失败和跨账户后台任务测试。
- 运行前端类型检查和桌面 E2E，覆盖许可开关、自动写入通知、一键撤回、候选确认/拒绝及错误恢复。
- 重启服务并新建会话，确认单次情绪未进入长期画像，已确认记录和授权历史仍可追溯。

## Non-goals

- 不自动诊断心理疾病、人格类型或其他敏感身份属性。
- 不把用户未回应候选视为默认同意，也不允许模型自行扩大授权范围。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 11：持久化流式聊天](./11-deliver-persisted-streaming-chat.md)
- [Issue 25：画像中心与静态头像](./25-deliver-profile-center-and-static-avatar.md)

## Comments

“情绪变化趋势”要求跨时间的用户确认或充分证据；一条消息中的情绪表达始终只是会话情境信号。

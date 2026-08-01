# 11 — 交付持久化真实 Qwen 流式聊天纵向切片

Status: ready-for-agent
Blocked by: [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md), [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md), [10 — 交付账户级 Qwen 凭据与固定能力真实探测](./10-deliver-account-qwen-credentials-and-probes.md)
Covered requirements: CHAT-01, CHAT-02, CHAT-03, CHAT-04, AUTH-03, MODEL-01, MODEL-02, ACCOUNT-01, IMP-03, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0005](../../../docs/adr/0005-per-account-qwen-key-and-capability-probes.md), [ADR-0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [ADR-0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付“注册/登录 → 配置并探测 Key → 新建对话 → 发送消息 → 接收真实 Qwen 流式回答 → 保存 → 重启恢复”的首个完整产品切片。对话、消息、生成状态和模型运行锁持久化到当前账户；断流、取消和重试均保留可理解状态，不重复用户消息，也不把占位文本或节点 ID 当作模型回答。

## Acceptance criteria

- [ ] 已登录且核心模型探测可用的用户可以创建对话、发送中文消息并看到真实模型正文逐步呈现。
- [ ] 每个对话、用户消息、助手消息、生成状态、耗时与固定模型运行锁持久化并绑定稳定账户 ID。
- [ ] 重启完整运行时后，用户可以重新打开同一对话并看到顺序、正文和状态一致的历史消息。
- [ ] 发送期间有明确中文生成状态和停止入口；连接断开会保留已接收正文并显示可重试错误，不重复插入用户消息。
- [ ] 重试创建新的助手尝试并保留审计关系，不静默改写历史；失败不会注册为成功回答。
- [ ] 无 Key、Key 无效、核心能力不可用、网络失败、供应商拒绝和限流分别显示可操作中文提示。
- [ ] 第二账户不能读取、订阅、重试或推测第一账户的对话和消息；切换账户时旧流立即与界面隔离。
- [ ] 生产路径不调用 Stub，不把工作流节点 ID、调试字段、系统提示或凭据输出给用户。
- [ ] 聊天页面完整覆盖 loading、empty/new conversation、streaming、error、permission/unauthenticated 和恢复后正常状态，不出现占位页。
- [ ] 电脑端仅用键盘可聚焦输入框、发送、停止、重试并返回；流式更新不会抢夺焦点或让屏幕阅读器逐 token 重复朗读。

## Verification

```powershell
conda run -n agent python -m pytest -k "conversation or message or streaming or qwen"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

在显式提供测试账户 Key 的人工冒烟环境中完成一次真实问答、停止/重试和进程重启恢复；检查日志、响应与数据库不包含 Key。

## Non-goals

- 不在本 Issue 完成最终侧栏、空白态建议卡、双模式或思考摘要；由后续 Issue 完成。
- 不实现附件、联网搜索、画像调用或插件执行。
- 不实现移动端聊天页面。

## Blocked by

- [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)
- [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)
- [10 — 交付账户级 Qwen 凭据与固定能力真实探测](./10-deliver-account-qwen-credentials-and-probes.md)

## Comments

这是后续功能的主干 tracer bullet。若真实供应商测试需人工 Key，自动测试仍应通过协议级替身验证流式边界，但生产依赖注入必须指向真实适配器。

# 16 — 交付两种模式的安全聊天附件
Status: ready-for-agent
Blocked by: [05](./05-build-clean-sqlite-and-object-storage.md), [11](./11-deliver-persisted-streaming-chat.md), [13](./13-deliver-new-chat-composer-and-blank-state.md)
Covered requirements: CHAT-07, CHAT-09, IMP-03, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在日常陪伴和学习模式共用的“+”菜单中交付“上传文件/图片”入口，并完成从选择文件、校验、持久化、消息关联、授权下载到删除的端到端闭环。文件正文与对象路径不得直接暴露给浏览器；数据库只记录稳定账户归属、对象元数据、内容哈希和会话关联。

上传过程必须校验真实文件类型、扩展名、大小、空文件、危险文件名和路径穿越。桌面输入区显示上传进度、成功、失败、取消与重试状态，失败不得生成看似成功的附件消息。

## Acceptance criteria

- [ ] 两种对话模式的同一“+”菜单都能选择受支持文件或常见图片，并保持当前会话与模式。
- [ ] 服务端通过内容嗅探校验类型和大小，拒绝可执行文件、伪造扩展名、路径穿越、空文件和超限文件。
- [ ] 每个对象以稳定账户 ID 归属，并记录内容哈希、原始显示名、安全媒体类型、大小、创建时间和关联消息。
- [ ] 上传中的附件可取消；失败项显示中文原因并可重试；重试不会创建重复对象或重复消息。
- [ ] 上传成功后消息显示文件名、类型、大小和处理状态，刷新与重启后仍可见。
- [ ] 下载必须经过授权接口；响应和页面不泄露宿主绝对路径、对象密钥或其他账户元数据。
- [ ] 用户可从消息中删除附件；确认后同步解除引用并按归属和引用计数安全清理对象及派生数据。
- [ ] 越权查看、下载、删除或把其他账户对象绑定到当前消息均被拒绝，且不泄露资源是否存在。
- [ ] 上传区域与附件卡片具有中文 loading、empty、error、permission 和 recovery 状态，按钮均连接真实后端。
- [ ] 桌面键盘可完成打开文件选择、取消、重试、下载和删除，状态变化通过可访问文本呈现。

## Verification

- 在 Conda `agent` 环境运行对象归属、类型嗅探、哈希去重、引用计数、路径穿越和事务回滚测试。
- 运行前端类型检查，并以桌面 E2E 覆盖两种模式上传、取消、失败重试、刷新恢复、下载和删除。
- 使用两个账户和伪造对象 ID 验证读取、绑定与删除均有账户隔离。
- 重启 `BridGes start`，核对附件消息、对象元数据和授权下载仍正常。

## Non-goals

- 不在本 Issue 完成文档分块、Embedding 或知识库索引。
- 不把任意上传文件当作可执行 SKILL 或 MCP。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 05：干净 SQLite 与对象存储](./05-build-clean-sqlite-and-object-storage.md)
- [Issue 11：持久化流式聊天](./11-deliver-persisted-streaming-chat.md)
- [Issue 13：新聊天输入区与空白态](./13-deliver-new-chat-composer-and-blank-state.md)

## Comments

上传完成只代表对象安全保存，不代表文档已经解析或可检索；解析和索引状态由后续 Issue 单独管理。

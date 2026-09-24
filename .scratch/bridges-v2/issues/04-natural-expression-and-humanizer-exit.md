# 04 — 自然表达与专用人味化退出

**What to build:** 用户在日常与学习回答中得到自然、符合其语气的表达；旧文章人味化任务停止接受新执行，已有结果继续可读。

**Blocked by:** 01 — 日常普通对话

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 轻量表达规则放在两种模式和各模块共用的最终生成链，优先级低于用户语气、篇幅与任务合同；引用、数值、代码、公式和工具结果保持准确。
- [x] 专用文章人味化入口、继续执行及重试写路径关闭，不再进行二次全文改写。
- [x] 旧人味化消息和结果仍能查看与导出；调用方检查后移除不再使用的编排依赖。
- [x] 用包含精确事实、来源和代码的例子验证表达调整不会造成事实漂移。

## Comments

### 2026-09-25 — 实现摘要（agent）

- **AC1**：`ChatLightweightPolicyCompiler` 升级为 `global-chat-lightweight-v2`：受保护区固定句加入「数值」，新增优先级声明（低于用户本轮语气/篇幅要求与任务合同）。注入点不变（`assemble_payload` 系统消息，普通聊天/学习/论文/生涯共用）；引用、数值、代码、公式、链接、JSON 与工具结果由既有 `restore_protected_regions` 确定性恢复兜底。
- **AC2**：三处写路径关闭——发送/首轮/创建会话遇 `bridges-humanizer` 返回 410 `humanizer_capability_retired`（其他 SKILL/插件/MCP 返回 410 `user_extensions_retired`）；遗留排队运行在 `stream_generation` 领取时收敛为退役错误，不发起模型调用；重试返回 410 `humanizer_retry_retired`。删除 `_stream_humanizer`、`HumanizerOrchestrator` 协议、repo 两个人味化写方法及 `ChatService`/应用装配的 `humanizer_service` 依赖。
- **AC3**：查看与导出保留（消息 `skill` 列投影、SSE 重放、`/data/export`）；前端建议卡、重试按钮与模板页入口移除。`HumanizerService` 暂保留，供评测框架与结项验证直接驱动（issue 21 负责整体退役）。
- **AC4**：新增 `tests/chat/test_expression_fact_drift.py`——原句含精确数值（299792.458 km/s）、公式、行内代码、代码围栏、链接与编号引用，模型漂移改写后落库正文在两种模式下逐字恢复、漂移写法不残留。

**验证**：全量 258 failed / 3739 passed / 42 skipped（main 基线 289 failed / 3729 passed / 40 skipped；失败仅减少、名称级比对无新增）；mypy 108 = 基线；前端 `tsc --noEmit` 与 58 个单测通过；`tests/humanize_eval` 独立运行 210 通过。

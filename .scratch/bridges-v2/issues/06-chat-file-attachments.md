# 06 — 聊天文件附件

**What to build:** 用户可上传受支持的文件，在同一聊天中针对文件内容提问并看到可定位的处理结果。

**Blocked by:** 05 — 聊天照片附件

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 文件选择、拖入、发送草稿及失败恢复沿用会话附件行为；实际支持类型和体积上限由代表材料解析测试确定并在界面说明。
- [x] 解析中、可用、失败与无法识别的状态可见；不支持或过大的单个文件给出中文原因，不清空其他草稿。
- [x] 回答仅引用本轮确实解析成功的内容和可定位片段，不能把无法读取的文件说成已读。
- [x] 文件按账户、会话和消息隔离，不自动进入全局知识库；删除与导出行为经过验证。

## Comments

### 实现摘要（2026-09-25，分支 `v2/06-chat-files`，worktree `../BridGes-06-chat-files`）

**架构：文件附件走既有摄取/检索流水线，照片路径不变。**
草稿域（Issue 05）扩到「照片 + 可解析文件」：上传即按内容嗅探定类型、
入队文档解析，解析状态随草稿投影（`ingestion_status`/`ingestion_error`）在
发送前就可见；发送时仍按 Issue 05 的原子绑定把草稿变成会话附件。文件正文
不做「整篇塞进提示词」的捷径，而是复用摄取（解析 → 分块 → 版本化索引）
与分层检索：恢复 ATTACHMENT 层，按会话绑定附件为作用域（有界取最近 10
份），引用带文件名、页码/章节与可核对片段，交给既有的上下文预算与引用
合同。因此 AC3 的「可定位片段」与 AC4 的「不进知识库」都由同一份
`document_records.source` 承担（`chat_attachment` ≠ `knowledge_base`）。

**后端**
- `chat/attachments.py`：接受类型扩到 `CHAT_ATTACHMENT_MEDIA_TYPES`（PDF/
  DOCX/TXT/Markdown + 四种照片），`_enqueue_parse` 在新建与两条幂等重放
  路径都补入队；新增 `SUPPORTED_MEDIA_TYPE_HINT` 作为错误文案与界面说明
  的唯一措辞源。
- `ingestion/service.py`：`enqueue` 增加 `source` 参数（校验合法来源）。
  修正此前硬编码 `source="knowledge_base"` 导致聊天附件会出现在知识库材料
  列表的缺陷——这正是 AC4 的边界。
- `retrieval/service.py`：ATTACHMENT 层按会话作用域恢复；附件存在时不被
  「本轮不检索」决策短路（决策只控制知识库层），无就绪材料时如实记
  「附件仍在处理中或暂无可检索内容」；引用可访问性改为会话级实时校验
  `bound_in_conversation`（解绑或换会话即视为授权已变）。
- `chat/turn.py`：`attachment_scope_note` 按文件名把引用核对到具体附件，
  对「未解析成功」与「已解析但本轮无片段」逐份下禁令（同轮另一份命中不
  代表这一份可读），作为系统块注入本轮提示词。
- `lifecycle/catalog.py`：删除顺序补 `model_run_lock_links`。缺此行时任何
  产生过运行锁的账户都无法删除（`FOREIGN KEY constraint failed`），是本票
  验证删除行为时暴露的既有缺陷，属于 AC4「删除行为经过验证」的必要修正。

**前端**
- `lib/chat-attachments.ts`：附件类型/体积/状态文案的单一事实源，照片与
  文件分流（照片缩略图，文件文档卡片）。
- `Composer`：接受 PDF/DOCX/TXT/Markdown，文件卡片显示图标、类型、大小与
  解析状态芯片（失败另有中文原因），非终态按 3 秒轮询刷新且保留本地页序；
  被拒文件逐个给中文原因（说明支持范围），不影响其他草稿与正文。
- `MessageList`：文件附件出文档卡片（文件名/类型/大小/页序 + 解析状态 +
  详情 + 下载原件）；对话页在还有非终态文件附件时按 3 秒刷新对话（全部
  终态即停，有界兜底 2 分钟），避免卡片停在「排队解析中」。
- 契约同步：`openapi.json`、`packages/contracts/src/generated.ts` 重生成。

**验证**
- 全量 `pytest tests --ignore=tests/humanize_eval -q --tb=no -rfE -p no:randomly`
  并 deselect 三条挂死的 `test_start_fails_*`：分支（`002f789`，含并回的
  main `d1acc83`）**255 failed / 3759 passed / 42 skipped / 3 deselected**；
  main 同期同命令 **260 failed / 3740 passed / 40 skipped / 3 deselected**。
  失败与错误名称集合**双向比对：分支独有为空**；main 独有的 5 条正是本票
  恢复附件检索层而转绿的用例（`test_generation_runs_retrieval_injects_context_and_persists_citations`、
  `test_kb_disabled_round_has_no_kb_candidates`、`test_citation_detail_accessible_with_download_entry`、
  `test_citation_detail_permission_changed_when_attachment_unbound`、
  `test_use_knowledge_base_false_excludes_kb_candidates`）。通过数 +19 = 新增
  16 例 + 转绿 5 例 − 2 例（worktree 无生产构建，`NEEDS_WEB_BUILD` 两条由
  通过变跳过，环境性差异）。
- 新增 `tests/chat/test_v2_06_file_attachments.py` 16 例：支持类型集合 =
  解析流水线 = 界面说明三者一致（含界面文案/白名单的跨端核对）；代表材料
  （3 页 PDF、带标题样式 DOCX、Markdown、接近 10 MB 的 TXT）解析到 ready 且
  页码/章节可定位；超限与不支持类型的中文原因与其他草稿保留；草稿与已发送
  附件四种状态可见；回答只用命中片段、未命中/未解析文件逐份如实说明；跨
  账户跨会话隔离；知识库材料列表为空 + 会话删除与账户删除级联（解析数据、
  分块与对象一并清理）；照片不入解析队列；幂等重放会补齐解析任务。
- 前端：`tsc --noEmit` 通过、`vitest` **111 passed / 17 files**、`eslint`
  0 error（3 条 warning 与 main 基线相同）。
- `mypy src/` 115 errors / 23 files，错误集合与 main 逐条一致（`diff` 为空）；
  `ruff check .` 无新增 finding（差异仅为 main 侧并行票留下的 F401）。
- `tests/contracts/test_openapi_sync.py` 通过。
- `/code-review` 两轴评审后修订一轮（逐份如实说明、已发送附件状态收敛、
  `CONTEXT.md`/`docs/table-owners.md` 同步、删除本票产生的孤儿方法与死代码）。

**已知取舍**
- 检索作用域是「本会话已绑定附件（最近 10 份）」而非「仅本轮消息的附件」：
  票面要求「在同一聊天中针对文件内容提问」，同一会话追问同一份文件仍须可
  引用；跨会话/跨账户仍严格隔离，解绑后引用打开即判 `PERMISSION_CHANGED`。
- 聊天侧状态措辞（「已解析，可引用」「无法识别正文」）经由
  `AttachmentIngestionInfo` 的 `labels` 覆盖知识库用词；两套措辞同源同义，
  暂未合并成一张表（知识库语境用词保持不变）。
- 界面白名单与服务端 hint 由跨端用例核对；`CHAT_ATTACHMENT_EXTENSIONS` 仍是
  TS 侧字面量（服务端以内容嗅探为准，客户端只做提前拦截）。
- 与 ADR-0026「聊天附件退役」的冲突按 ADR-0030 处理，已在
  `docs/table-owners.md` 显式标注例外，未新增 ADR。

# 05 — 聊天照片附件

**What to build:** 用户可在日常聊天中添加、预览和排序多张照片并就其内容提问；附件只属于该消息和会话。

**Blocked by:** 02 — 可恢复的对话运行

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 桌面端支持选择、拖入及粘贴图片；发送前可预览、移除、调整页序，键盘也能完成排序。
- [x] 缩略图显示文件名、类型和页序；不支持类型、超限、读取失败或重复页在对应附件旁显示中文原因，不清空其他草稿。
- [x] 上传草稿经类型、体积和账户校验；失败或发送失败保留文字、照片与顺序，成功后幂等绑定消息和会话。
- [x] 图片确实进入本轮多模态回答；纯附件消息可发送并由助手询问用途，处理前不显示为已识别。
- [x] 他人无法读取附件；聊天照片不进入全局知识库，也不弹出加入知识库的建议；删除、导出及过期草稿处理符合会话数据约束。

## Comments

### 实现摘要（2026-09-25，分支 `v2/05-chat-photos`，worktree `../BridGes-05-chat-photos`）

**架构：账户域草稿 + 发送时原子绑定。** V2 禁止空会话（首轮必须原子创建），
照片草稿因此不挂在会话上，而是新增账户级 `chat_attachment_drafts` 表
（迁移 v49→50）；发送时在同事务内把草稿按请求顺序迁移为
`chat_attachments` 绑定行（含新增 `ordinal` 列）并删除草稿，任何一步
失败整体回滚，草稿原样保留。

**后端**（`tests/chat/test_v2_05_photo_attachments.py` 19 例全过）：
- `attachments.py`：`upload_draft` 原始字节流式接收，内容嗅探只放行
  PNG/JPEG/GIF/WebP，10MB 上限，中文原因（类型/体积/数量/重复）；
  `X-Bridges-Upload-Id` 幂等重放返回既有草稿（同 ID 不同内容 409），
  同名同内容去重；`validate_draft_ids` 发送前校验数量/重复/归属。
- `repository.py`：绑定块 SELECT→逐条 INSERT（ordinal=请求顺序，
  created_at 沿用草稿时间）→DELETE 草稿，与消息同事务。
- `turn.py` `_user_history_entry`：仅当前轮把照片转成 OpenAI 兼容
  image_url data-URL 注入模型；纯附件注入「确认看到照片并询问用途」
  提示；全部/部分照片不可读取时如实告知，不声称已识别。
- `api/chat.py`：`POST/GET /chat/attachment-drafts`、`.../content`
  （inline 预览）、`DELETE .../{object_id}` 与 `.../by-upload/{id}`；
  发送/首轮端点接收 `attachment_ids`。
- `runtime/executor.py` 按 TTL（复用未绑定附件同款）清理过期草稿；
  `lifecycle/catalog.py` 把草稿表纳入账户删除与导出分类。

**前端**（`apps/web`，vitest 13 例全过，tsc/eslint 干净）：
- `Composer.tsx`：选择/拖入/粘贴三入口，客户端预校验与服务端同款
  中文原因；缩略图显示文件名、类型、页序，上移/下移按钮键盘可达
  （边界禁用），移除即删草稿；挂载时恢复账户草稿；纯附件可发送，
  发送失败保留文字与照片，成功后清空。
- `api.ts`：`uploadChatAttachmentDraft`/`listChatAttachmentDrafts`/
  `chatAttachmentDraftContentUrl`/`removeChatAttachmentDraft`，
  `createChatRun`/`startFirstTurn` 透传 `attachment_ids`；幂等身份
  纳入照片集合（同文字换照片换新键）。
- `MessageList.tsx`：用户消息气泡按页序渲染已绑定照片缩略图（同源
  授权下载地址）；聊天照片全程不出现任何「加入知识库」建议或
  「已识别」文案。

**验收核对：** 跨账户草稿/附件互不可见（404 不泄漏）；删除会话级联
删除照片；草稿刷新/重启可恢复；知识库材料列表与 document_records
在照片会话后保持为空。全量回归与 main 基线一致（pytest 失败清单、
mypy 115 处同文件同错误码、ruff 同文件同规则——三项逐项比对无新增）。

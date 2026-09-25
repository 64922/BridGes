# 数据库表属主清单

bridges.db 是单机权威数据库（ADR-0013），本文件回答一个问题：**「这张表谁说了算？」**
——schema 变更先找属主模块，跨域读取经属主 repository 的小接口，不新增直连。

> 退役边界：`learning_projects`、用户扩展/MCP、提醒和旧画像治理表是 ADR-0026 规定的迁移兼容面。它们的属主只负责历史读取、停用、导出和清理；不得为退役能力新增写路径或用户入口。新文件与本地检索默认只归 `knowledge_base`/摄取与检索域所有。
>
> 例外（ADR-0030 取代 ADR-0026 的对应条款）：V2 的聊天附件是独立于知识库的会话级能力——发送前为账户级草稿、发送后随消息绑定会话，写路径只在 `chat/` 的草稿与附件仓库；知识库仍是唯一按账户归集的材料来源，聊天附件不进入其材料列表。

## 纪律

- **新代码禁止在 service 层直接 `.scoped().execute(...)`**；repository 层是唯一允许
  直连数据库的层级（`storage/database.py` 本身除外）。
- 写路径走属主 repository，并保持既有事务语义（写仍包在 `database.transaction()` 内，
  只改调用路径不改 SQL）。
- 跨域读取（如检索读 conversations、附件投影 JOIN objects）经属主 repository 的
  小接口，不新增直连；repository 内部允许对非己表只读 JOIN（见「跨域只读白名单」）。
- 存量直连见「遗留直连声明」，随后续 issue 按本清单渐进收编。

## 属主清单

| 表 | 属主 module | 属主仓库 / 说明 |
|---|---|---|
| `conversations` / `messages` / `mode_events` / `answer_feedback` / `model_run_locks` | `chat/` | `ConversationRepository`（`src/bridges/chat/repository.py`） |
| `chat_attachments` / `chat_attachment_cancellations` | `chat/`（历史兼容 + V2 会话附件） | `AttachmentRepository`（V2 起接受草稿绑定后写入；历史行只读、原子清理与迁移） |
| `chat_attachment_drafts` | `chat/`（V2 会话附件） | `AttachmentDraftRepository`（发送前账户级草稿；上传、幂等重放、过期清理与发送时绑定） |
| `retrieval_rounds` / `message_citations` | `retrieval/` | `RetrievalRepository` |
| `index_active` / `index_versions` / `index_vectors` / `document_chunks`（含 `*_backup`） | `retrieval/`（读）＋ `ingestion/`（写） | 索引版本由摄取状态机写入，检索域经 `RetrievalRepository` 读取 |
| `document_records` | `ingestion/`（写）＋ `retrieval/`（读） | 写路径在摄取状态机；检索域经 `RetrievalRepository` 的只读方法（就绪/重建/引用校验） |
| `document_parse_cache` | `ingestion/` | 摄取解析缓存 |
| `objects` / `accounts` | `storage/` | `BridgesObjectRepository`；跨域只读经 `object_status` / `object_metas`，删除标记经 `mark_pending_cleanup` |
| `account_deletions` | `lifecycle/` | 账户删除编排（`lifecycle/deletion.py`） |
| `learning_projects` | `learning_projects/`（退役兼容） | 历史项目迁移、只读引用与审计 |
| `skill_packages` / `account_skill_states` | `plugins/`（退役兼容） | 扩展包隔离、清单导出与清理 |
| `mcp_servers` / `mcp_calls` | `mcp/`（退役兼容） | MCP 清单停用、历史调用审计与清理 |
| `image_assets` / `image_tasks` / `image_versions` | `image/` | 文生图资产管理 |
| `video_assets` / `video_tasks` | `video/` | 文生视频资产管理 |
| `reminders` / `reminder_settings` / `reminder_deliveries` | `reminder/`（退役兼容） | 取消未来任务、保留历史导出、清除 SMTP 凭据 |
| `profile_*`（断言/候选/观察/许可/切片/通知） | `profiles/` | 四维画像写入、最小切片与旧治理对象封存 |
| `eval_*`（套件/用例/报告/盲评/运行锁） | `evaluation/` | A/B 科学评测 |
| `task_claims` / `workflow_runs` | `workflows/` | 领取型任务契约 |
| `schema_meta` | `storage/` | 数据库迁移元数据 |

## 跨域只读白名单

以下 JOIN 是已知的跨域只读投影（repository 层内，不改写非己表，schema 变更时以属主
模块为准）：

- `AttachmentRepository` 的附件投影查询 JOIN `objects`（storage 域）与
  `document_records`（ingestion 域），用于展示对象元数据与摄取状态。
- `RetrievalRepository` 的就绪/重建查询 JOIN `objects`（storage 域），用于活跃性过滤。

## 遗留直连声明

以下文件仍在 service 层直接 `.scoped()`（多为域内直连，因该域尚无 repository），
为已知遗留，随后续 issue 按本清单渐进收编，不在本声明外新增直连：

- `chat/selections.py`、`ingestion/service.py`、`knowledge_base/service.py`、
  `learning_projects/service.py`、`lifecycle/catalog.py`、`lifecycle/deletion.py`、
  `mcp/service.py`、`plugins/service.py`、`reminder/service.py`、
  `image/service.py`、`video/service.py`

> `profiles/sqlite_repository.py` 是 profiles 域的 repository，属合法直连层级，不在此列。

## Issue 45 已收编

- `retrieval/service.py`：不再直读 `conversations`（经 `ConversationRepository`）、
  `chat_attachments`（经 `AttachmentRepository`）、`objects`（经
  `BridgesObjectRepository` 注入）、`index_active` / `document_records` /
  `index_vectors`（经 `RetrievalRepository`）。
- `chat/attachments.py`：不再绕过 repository，4 张相关表（`chat_attachments` /
  `chat_attachment_cancellations` 属主 `AttachmentRepository`，`objects` /
  `document_records` 经属主或只读 JOIN）读写全部经仓库。

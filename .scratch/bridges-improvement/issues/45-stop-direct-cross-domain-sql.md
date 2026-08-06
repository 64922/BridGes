# Issue 45 — 服务不再直读不属于自己的表

Status: ready-for-agent
Type: task
来源：架构评审候选 4（Worth exploring）。评审报告：architecture-review-20260806-231807.html
词汇：module / interface / depth / seam / adapter / leverage / locality

## 问题（当前状态，含证据）

`BridgesDatabase`（`storage/database.py`，1555 行，26 版迁移）是单机权威数据库
（ADR-0013），**但表的读写没有属主边界**：18 个文件直接调
`self._database.scoped(account_id).execute(...)`，其中仅 6 个走 repository 模式：

- `retrieval/service.py:113-123`：`LayeredRetrievalService` 持直接 DB 引用，直读
  `conversations`（属 chat）、`index_active`（属检索/摄取）与整个 FTS/向量管线——
  它查询的 `conversations` 表由 chat 域拥有，schema 变更会波及 retrieval。
- `chat/attachments.py`：60+ 处直连 `chat_attachments`、`objects`、`document_records`、
  `chat_attachment_cancellations`，绕过 `ConversationRepository`。
- `BridgesObjectRepository` 直连 `objects` 表（跨账户清理等，合法例外）。
- 同类服务级 `_iso()`/`_now()` 时间助手在 30+ 文件重复（见 Issue 49 小摩擦）。

摩擦点：**表 schema 变更必须改非属主模块**；新开发者不知道「这张表谁说了算」。

## 方案：按域收编读写进属主 repository，跨域读取过 interface

不迁移数据、不拆库（ADR-0013 不动），只收编**读写代码**：

### 1. 明确属主表清单（先写进文档再动代码）

| 表 | 属主 module |
|---|---|
| `conversations` / `messages` / `chat_attachments` / `chat_attachment_cancellations` / `mode_events` | `chat/` |
| `index_active` / `index_versions` / `document_records` / `retrieval_rounds` / `citations` | `retrieval/` + `ingestion/`（先定一个，推荐 retrieval 拥有索引版本、ingestion 拥有文档） |
| `objects` / `account_deletions` | `storage/` / `lifecycle/` |
| 其余（reminders/画像/…） | 现状属主（不逐个动） |

### 2. 收敛两个最高摩擦点

- **retrieval 读 conversations**：`LayeredRetrievalService` 需要「当前对话所属
  project_id」以限定项目层检索范围。在 `ConversationRepository` 上提供小接口
  `project_id_for_conversation(account_id, conversation_id)`，retrieval 经构造注入
  该 repository 调用——删除对 conversations 表的直读。
- **retrieval 读 index_active**：这是 retrieval 自己的域，把 `index_active` 的
  SELECT 收进 `RetrievalRepository`（retrieval 域内既有 repository 模式，补齐缺失
  方法即可）。
- **attachments 绕过 repository**：`ChatAttachmentService` 对 `chat_attachments` 等
  4 张表的读写收编进 `ConversationRepository`（或独立 `AttachmentRepository`，
  同属 chat 域，由 chat 模块拥有并导出）。attachments service 只经 repository
  访问数据库。
- 跨域读（chat → storage 的 objects 列表、media → document_records）通过属主
  repository 的小接口，不新增直连。

### 3. 纪律

- 新代码禁止在 service 层直接 `.scoped().execute()`；repository 层是唯一允许
  直连的层级（`storage/database.py` 本身除外）。
- 在 `docs/` 或 `CONTEXT.md` 补一段「表属主清单」章节，供新开发者查询。

### 验收标准

- [ ] `grep -rn "\.scoped()" src/bridges/` 中，service 层（非 repository/storage）
      不再出现（除已声明的合法例外并注释）
- [ ] retrieval 不再直读 `conversations`；attachments 不再绕过 repository
- [ ] `tests/retrieval/`、`tests/chat/test_chat_attachments.py` 等全绿 +
      全量 pytest 无回归
- [ ] 表属主清单已入文档
- [ ] ruff 干净

### 风险与开放问题

- **范围控制**：18 个直连文件不可能一次收完。本 issue 只承诺
  retrieval→conversations、retrieval→index_active、chat/attachments 三处
  （评审点名的摩擦），其余直连留待后续按属主清单渐进收编——**不要**在本次
  扩散到所有模块，避免大爆炸回归。
- SQL 事务边界：收编时保持既有事务语义（写仍走 `with transaction()`），
  只改调用路径不改 SQL。
- 性能：repository 小接口不加额外查询（复用现有 SQL 文本）。
- 与 ADR-0013 一致：单库、单进程权威不变。

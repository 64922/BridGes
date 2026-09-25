# 08 — 原子画像

**What to build:** 用户可查看、修改和删除逐条长期信息；系统只从有原话证据的消息中提取可复用事实，并尊重用户修改。

**Blocked by:** 03 — 长对话上下文

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 旧四类画像以可对账、可恢复的迁移方式转成无固定类别的原子列表；每行始终可见修改和删除，行内编辑有保存／取消，删除先确认，空态说明提取边界。
- [x] 普通消息回答后异步提取零条或多条明确、稳定的信息，记录来源并处理去重与冲突；不推断人格或心理标签。
- [x] “记住／忘掉”本轮立即生效；用户编辑优先于自动提取，删除后从下一轮上下文消失且不会因旧消息重放而复活。
- [x] 只将当前任务必要的画像切片纳入上下文；列表、来源及删除墓碑均按账户隔离。

## Comments

### 2026-09-25 — 实现摘要（agent）

- **AC1 迁移与无类别列表**：新增原子画像域（`src/bridges/contracts/atomic_profile.py`、`src/bridges/profiles/atomic.py`）与落盘表 `profile_items` / `profile_item_migrations`（`bridge.db` schema v51，迁移函数只追加不改写旧表）。四维记录仍是自动提取与冲突消解的写入引擎，原子条目镜像它的结果并额外承载用户编辑时间、删除墓碑与迁移批次。`POST /profiles/items/migration` 一次性把旧四类记录转成原子列表并返回可对账报告（`migrated`/`duplicated`/`tombstoned`/`skipped` 计数、覆盖的来源记录标识、`reconciliation_digest` 摘要，均不含正文）；`GET /profiles/items/migration` 读最近一次报告，`POST /profiles/items/migration/{run_id}/rollback` 只删本批次新建条目、旧四维记录保持可用。重复执行幂等（新增 0、重复 N）。页面侧 `AtomicProfileCenter.tsx` 每行始终可见「修改」「删除」，行内编辑有保存／取消，删除先经 `window.confirm` 确认，空态写明「只从明确说过、能引用原话的消息里整理稳定事实，不推断人格或心理状态」；列表不分组、不展示类别、主题提示与去重哈希（`tests/profiles/test_v2_08_atomic_profile_api.py` 逐字段断言禁止出现 `dimension`/`category`/`topic_hint`/`identity_key`/`owner_account_id`/`status`）。
- **AC2 异步提取、来源与去重冲突**：普通消息的抽取沿用既有的四维自动提取路径（本轮回答后在请求内异步预处理，失败进入有界重试队列），新增镜像步骤把 0..N 条结果写成原子条目并记录来源消息标识；去重键是「账户 + 规范化正文」的 sha256，同键只保留一条并合并来源，同一底层记录的值被更新时旧正文退休、按新正文重新去重。页面上每条自动整理的条目显示「来自 N 条对话记录」（来源对用户可见，而不是只存在内部字段）。系统提示词只携带结果状态，不携带正文，也不出现人格或心理标签。
- **AC3 记住／忘掉与用户权威**：`AtomicProfileService.remember/forget` 在本轮提交前同步执行：`remember` 立即成为用户权威条目并解除同键墓碑，`forget` 命中即删除并写入墓碑，未命中如实返回 `unresolved`（模型被禁止声称已删除）；结果经 `AtomicProfileMemoryResult.context_metadata()` 的脱敏元数据进入本轮上下文（`turn.py` 的 `profile_memory_context`）。用户编辑把 `write_origin` 转为 `user` 并记 `user_edited_at`，自动抽取不再改写；**编辑换键后旧正文写入抑制键（墓碑）**，因此旧消息或旧记录重放不会把被改掉的值作为新条目带回，用户再次明确「记住」旧正文仍可恢复（`test_modify_item_suppresses_the_old_text_from_later_extraction`）。删除后条目从下一轮上下文的切片中消失。
- **AC4 最小切片与账户隔离**：`compile_chat_slice` 只取与当前任务相关的少量条目（`MAX_SLICE_ITEMS=4`，按把握度与更新时间排序），普通提取的条目**下一轮**才进入上下文（本轮刚整理出的自动条目带本轮证据时先排除，写入 `UnusedSliceItem` 的「本轮刚整理，下一轮才使用」原因；用户「记住」不受此限，本轮即生效）。切片与上下文编译器共用预算：`compile_turn_context` 返回 `(compiled_messages, context_budget)`，预算经图状态传到 `stream_turn` → `profile_block_within_budget`，超限时先裁画像材料而非挤掉当前请求与证据。原子条目对模型也不带类别（`dimension=""`）。条目、墓碑、迁移报告与四维记录全部按账户作用域读写（`test_items_routes_are_account_scoped`、`test_items_tombstones_and_reports_are_account_scoped`）。

**验证**：全量 `pytest tests --ignore=tests/humanize_eval -q --tb=no -rfE`（分支）239 failed / 3696 passed / 41 skipped / 2 errors；main 基线（同一命令、同期实测）239 failed / 3649 passed / 39 skipped / 2 errors。失败与错误的名称集合差异只剩两类**工作树环境产物**，均已定位：① `tests/closeout/test_api_boot.py` 的 2 例在分支失败、main 通过——worktree 没有仓库 `.venv`，收尾夹具回退到 conda Python 后子进程 `python -m bridges.cli.main` 无法导入 `bridges`；用 `PYTHONPATH=src` 跑同一模块得 2 passed。② `tests/runtime/test_runtime_contract.py` 的 2 例在 main 失败、分支跳过——它们带 `NEEDS_WEB_BUILD`（需要 `apps/web/.next/standalone/server.js`），worktree 无生产构建故跳过，main 上则失败。除此之外双向 `diff` 为空，`ERROR` 名称集合逐条相同（两条 runtime smoke 服务用例）。多出的通过数 47 = 本票新增 49 个 pytest 用例减去上述 closeout 2 例环境产物。`mypy src/` 分支与 main 同为 115 errors / 23 files 且错误集合逐条一致（仅行号位移）；ruff 全仓对比 573 errors，findings 集合逐条一致（新增文件零告警）。前端 `tsc --noEmit` 通过、`eslint` 通过、`AtomicProfileCenter.test.tsx` 5 例通过；`tests/contracts/test_openapi_sync.py` 通过（`openapi.json` 与 `packages/contracts/src/generated.ts` 已按新路由重生成）。

**刻意保留 / 已知边界**：

1. **迁移入口是显式路由，不在读列表时隐式写入**：迁移是账户级、幂等、只读旧记录的操作，入口留给运维或后续管理界面（`POST /profiles/items/migration`）；`GET /profiles/items` 保持纯读，避免每次列举都触发迁移。
2. **旧四维页面组件未删除**：`apps/web` 旧画像页与 `/profiles/four-dimensions` 路由继续保留给既有客户端与迁移对账，退役清理由 issue 21 负责；本票只新增无类别列表与入口。
3. **列表不做模式分区**：原子条目对陪伴与学习模式共用一份列表，切片按任务相关性取用；按模式分区需要产品决定，本票不擅自引入。
4. **墓碑对外不可见**：`GET /profiles/items` 只返回活动条目，墓碑（包括编辑换键产生的抑制键）只在仓库层可见，报告只给计数与摘要、不留正文。
5. **Windows 时钟粒度影响列表顺序**：系统时钟约 15.6ms 一拍，同一刻度内写入的两行 `updated_at` 相同，`ORDER BY updated_at DESC, profile_item_id DESC` 由 id 兜底（确定性但不严格按先后）；测试断言按集合比较，不断言跨行顺序。
6. **评审提出但刻意不改**：`profile_item_migrations` 台账与四维记录并存（迁移要可对账、可恢复，就必须留来源标识）；`SqliteAtomicProfileRepository.transaction()` 复用本模块的 `_joined_transaction` 而不是改造共享数据库事务设施（单连接 SQLite 不能嵌套 `BEGIN IMMEDIATE`，自动抽取在写四维记录的事务里镜像原子条目，必须并入外层事务）；新增投影字段 `source_message_ids`/`write_origin` 由页面消费（来源标注与「你手动记住／自动整理／旧列表迁移」区分），不是投机字段。


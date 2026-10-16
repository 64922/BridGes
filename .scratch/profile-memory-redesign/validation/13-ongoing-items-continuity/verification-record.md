# 13 — 跨会话接续事项并管理暂停和完成：验证记录

**分支：** `codex/issue-13-ongoing-items-continuity`（基点 `631a4952`，main；
验证过程中并入 `main@8dca8ecd` 的工单 12 改动——无迁移号冲突，本票迁移 87
不变；合并后 `openapi.json` 与 `packages/contracts/src/generated.ts` 重生成
并核对与 API 一致、零 diff）。
**环境：** conda `agent`（Python 3.11.15）；前端 Node v24.14.0 / npm 11.9.0。
**证据：** 本文 + `evidence/demo-walkthrough-20261016T0930Z.txt` +
`tools/walk_issue13_ongoing_demo.py`（可复跑；37 项检查全部 PASS，FAIL 0）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/13-ongoing-items-continuity/tools/
        walk_issue13_ongoing_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

（确定性走查约 20 秒；运行库在 `.tmp/issue13-demo-*`，只使用临时库与内存组合，
不读取或记录任何凭据与真实数据。已知坑：`remember`/`set_state` 返回的是库内
同一条可变条目对象，脚本里跨请求复用的版本号必须先取 `int` 快照，否则暂停
请求的原地更新会同步改变脚本持有的「旧版本」引用。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 新会话接续正确事项及进展，不要求重复全部背景或携带无关事项 | 走查 [1]（「继续上次的考研英语准备」→ 目标/进展/待续问题整组按 `ongoing` 依据采用，basis=task_rules+keyword+ongoing / keyword+ongoing / ongoing；无关爱好排除原因「与当前问题无关」且不是靠接续依据排除）；`test_issue13_ongoing_items.py::test_continuation_compile_adopts_whole_matter_with_ongoing_basis`、`::test_continuation_is_withheld_when_ambiguous_or_unrelated`、`::test_semantic_hit_on_entry_continues_its_matter` |
| 并行事项独立，暂停/完成/恢复不误覆盖其他目标 | 走查 [2]、[4] 与 [5]（HTTP 陈旧版本 409）；`::test_pause_resume_and_complete_touch_only_the_exact_goal`（暂停 A 后 B 保持进行中；完成只有精确目标变化；幂等重复操作不推进版本；歧义不猜）；`test_issue13_ongoing_api.py::test_state_route_pause_complete_conflict_and_scope`（409 冲突 / 422 非目标 / 404 越权） |
| 待续问题有真实交流依据，摘要不成为新增用户事实来源 | 走查 [3]（投影与条目原文逐字一致、携带真实来源消息标识；「还没…」判定为待续问题）；`::test_projection_groups_goal_progress_and_pending_without_inventing_texts` |
| 删除依赖事实、关使用、源消息变化使近期上下文及时失效，迟到重建不能覆盖新状态 | 走查 [4]（修改立即失效并在读取重建、迟到重建世代 CAS 失败、两个并发重建后者被拒、关使用后不重建不再采用、重开按当时依赖重建）；`::test_context_invalidates_on_edit_delete_and_control_change`、`::test_source_message_change_invalidates_context`、`::test_late_rebuild_cannot_overwrite_new_state`；`test_issue13_ongoing_api.py::test_controls_route_invalidates_context_and_keeps_management` |
| 页面加载、空态、失败重试、保存结果和键盘访问完整 | `OngoingItemsCard.test.tsx` 8 项：列表与来源数量、空态与关使用提示（仍可管理）、暂停/继续按精确条目提交并在成功后展示结果、完成需确认且确认后消失但有反馈、取消不提交、失败回读真实状态并给出重试入口、领域引用未接通如实标注、操作控件均为原生按钮（聚焦后触发动作，键盘可达的直接证据）；加载/操作结果用 `role="status"`，失败用 `role="alert"` 与重试按钮 |
| 新状态在本票完成迁移、并发更新、重启恢复、导出和账户删除验证 | 走查 [6][6b]（v86 → v87 升级 + 幂等、上下文不进导出类别与逻辑摘要、备份含行、账户删除清空、删除顺序先于 `profile_items`）；`test_schema_v87_ongoing_context.py`（2 项）；`::test_sqlite_context_store_epoch_cas`（SQLite 存储 + 新服务实例读取持久化行，重启恢复；CAS 并发提交语义）；`::test_accounts_are_isolated`（空态与账户隔离） |

## 2. 确定性走查（最终代码，20261016）

1. **两会话接续**：会话 A 写入目标/进展/待续问题 + 无关爱好；会话 B
   「继续上次的考研英语准备。」→ 三件整组采用（基础渠道另含任务规则/关键词），
   爱好不注入。
2. **多事项独立**：目标 A 暂停 → B 不受影响、A 不再接续；恢复 → 重新可接续；
   「考研英语和计算机三级怎么安排？」同时命中 → 不猜；B 完成 → 从事项区消失
   但事实仍保留。
3. **真实依据**：投影文本逐字取条目原文，待续问题来源 = 真实消息标识
   （`msg-pending`），无摘要改写。
4. **失效与守卫**：首读建上下文（version=5，历史写入已累计失效代数）；修改
   进展 → 立即失效、读取时重建且指纹变化；重建开始后新增目标 → 迟到提交 CAS
   失败、读取给出含新状态的上下文；双并发重建先到者生效；关使用 → 上下文
   不再可用、接续通道关闭、事项仍可管理；重开 → 按当时依赖重建。
5. **生产组合 HTTP**：`create_app()` 内存模式 + TestClient，注册后经真实路由
   读事项、暂停/陈旧版本 409/恢复/领域引用（未接通如实 unresolved）/关开使用/
   完成（事项区清空、记录保留）。
6. **迁移与派生边界**：v86 库升级补两列 + 上下文表、幂等；上下文不进导出
   类别与逻辑摘要；备份快照含行；账户删除清空；`ACCOUNT_TABLES` 顺序先于
   `profile_items`。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue13_ongoing_items.py`（12 项）：投影不发明
  文本、整组接续采用与 ongoing 依据、歧义/无关不接续、语义命中条目接续其
  事项、暂停/恢复/完成精确到目标且幂等、领域引用只存引用且实时解析、编辑/
  删除/控制失效、来源消息变化失效、迟到重建不可覆盖、账户隔离、SQLite
  上下文 CAS（含新实例重读）、迁移 87。
- 新增 `tests/profiles/test_issue13_ongoing_api.py`（4 项）：列表投影、状态
  路由（暂停/完成/409/422/404/账户作用域）、领域引用设置与清除、controls
  路由的联动失效与仍可管理。
- 新增 `tests/storage/test_schema_v87_ongoing_context.py`（2 项）：v86 → v87
  升级补列补表与幂等；派生性（导出/逻辑摘要不在、备份可见、账户删除清空、
  删除顺序）。
- 既有更新：`tests/storage/test_schema_v86_profile_recall_index.py` 版本断言
  由固定 86 放宽为 `SCHEMA_VERSION >= 86`（本票顺延占用 87，断言语义不变）。
- 聚焦（最终合并态）：`pytest tests/profiles tests/storage tests/lifecycle
  tests/contracts` → **969 通过**；三个新增文件单跑 **18 通过**。
- 全量（`-n 4 --ignore=tests/career_plan --continue-on-collection-errors`；
  最终合并态）：
  - 分支：**327 failed / 5688 passed / 37 skipped**；main@`8dca8ecd` 同命令：
    **329 failed / 5668 passed / 37 skipped**。
  - 逐条差集：**分支独有失败 0 项**；main 独有失败 2 项为
    `tests/closeout/test_api_boot.py` 环境性 flaky（分支通过；06/08/09/11/12
    记录同现象）。通过数 +20 = 本票新增测试（12+4+2）加上 2 项在 main 本次
    运行失败而分支通过的同款 flaky，无其他状态变化。
  - 合并前对照（main@`631a4952`，即并入工单 12 之前）：分支 327 failed /
    5654 passed / 37 skipped，main 329 failed / 5634 passed / 37 skipped，
    同为分支独有失败 0。合并后全量与走查重跑结论不变。
  - `tests/chat` 的既有失败（与本票无关，main 完全相同）保持原样。
  - 中途一次全量曾出现分支独有失败
    `tests/contracts/test_openapi_sync.py`（路由拆分为独立模块后 OpenAPI
    路径顺序变化）；把 `include_router` 放到 `api.py` 文末并去掉子路由
    tags 后 `openapi.json` 与 `generated.ts` 均恢复零 diff，该测试通过。

## 4. 静态检查

- `ruff check`（全部改动文件 + 新增测试/走查脚本）：本票新增 0 问题；
  `src/bridges/profiles/commit.py:70` F401 与 `src/bridges/storage/database.py:1671`
  E501 为 main 既有同项，未触碰。
- `mypy src`（最终合并态）：**141 errors / 23 files**（main@`8dca8ecd` 同命令
  同为 141/23；检查文件数 685 → 691，+6 为本票新增模块），无新增错误。
- `pip check`：No broken requirements found。
- 前端（worktree，`apps/web`）：`npm run typecheck` 通过；`npm run test:unit`
  35 文件 / 302 测试通过（含 `OngoingItemsCard.test.tsx` 8 项）；`npm run lint`
  仅既有警告；`npm run build` 成功。

## 5. 接口与实现摘要

- 契约 `contracts/ongoing_profile.py`：`OngoingItemsProjection`（usage 开关 +
  说明 + 事项）、`OngoingItemProjection`（目标/进展/待续问题/领域引用）、
  `OngoingEntryProjection`（原文、角色、真实来源标识、时间）、
  `OngoingContextSnapshot`（state/version/指纹/事项，version 默认 0 兼容
  「从未建立」）、`OngoingItemStateUpdateRequest`（action + version，extra 禁
  止）、`OngoingDomainReferenceUpdateRequest`（reference 可 null 清除）。
  `contracts/atomic_profile.py`：条目新增 `related_goal_item_id` 与
  `domain_reference`（`AtomicProfileDomainReference` 只存 domain/reference_id，
  不复制状态）。
- 原子画像 `profiles/atomic.py`：`is_ongoing_goal_item`、`ongoing_entry_role`
  （`_ONGOING_PENDING_RE` 未决词表 → PENDING_QUESTION / PROGRESS）、
  `ongoing_goal_mentioned` 与 `_resolve_ongoing_goal_link`（唯一被提及的进行
  中目标才链接，`_MIN_ONGOING_GOAL_OBJECT=2` 防单字误配；多命中/无命中不链
  接）、`RECALL_BASIS_ONGOING`（编译切片依据标签）；仓库读写两列；
  `commit_fact`/`remember`/`modify_item`/`correct_fact` 写入后链接、`forget`/
  `delete_item`/生命周期信号随之失效；`link_saved_facts` 批内补链（与
  `_ongoing_link_target` 共用 `_active_ongoing_goals` 取候选目标）；
  `set_ongoing_state`（乐观锁 + 幂等空操作 + 完成不可逆停/继续）与
  `set_domain_reference`（只目标可设、null 任意可清）；监听/提供方注入接缝
  （未注入时写入照常，失效失败仅告警）；`compile_adopted_slice` 经提供方把
  整组候选项按 tier 1、basis `ongoing` 参与采用，门禁照旧。
- `profiles/ongoing_projection.py`（纯投影层）：事项分组（`_group_items`、
  `_OngoingGroup`）、条目投影、上下文新鲜度与时间基准、关闭使用提示文案；
  不持有存储/控制/领域依赖。
- `profiles/ongoing.py`（`OngoingItemsService`）：事项投影（原文直
  取、来源标识、时间）、状态操作委托、领域引用实时解析（无提供方→
  unresolved）、上下文 `read_context`/`begin_context_rebuild`/
  `commit_context_rebuild`（指纹含合同版本、使用开关、目标/条目 (id,version,
  goal_state)、来源消息 (status, 内容哈希)；失效 epoch+1；重建 CAS，迟到
  提交失败；`_ONGOING_REBUILD_ATTEMPTS=2`）、`continuation_group`、账户空态
  （无行且无事项不落空行）。
- `profiles/ongoing_repository.py`：`OngoingContextStore` 协议 +
  内存/SQLite 实现（`profile_ongoing_context` 单行/账户，`joined_transaction`
  并入调用方事务，同成同败）。
- 提交边界 `profiles/commit.py`：`write_facts` 批内 `link_saved_facts`；
  `profiles/ongoing_api.py`：三条 `/profiles/ongoing*` 路由（并入
  `profiles/api.py` 同一前缀与标签，文末 `include_router` 保持 OpenAPI 顺序
  零变化）；`profiles/api_errors.py`：四维/原子共用的错误映射；
  `api/app_setup_models.py` 装配 Sqlite/内存上下文存储并双向接线。
- 存储 `storage/database.py`：迁移 87（`profile_items` 加
  `related_goal_item_id`/`domain_reference_json`；建 `profile_ongoing_context`），
  `SCHEMA_VERSION` 86 → 87 并登记 REQUIRED/MIGRATION_ADDED；`lifecycle/catalog.py`
  登记 `ACCOUNT_TABLES`（先于 `profile_items`）。
- 前端：`contracts/profile.ts` 类型别名、`lib/api/profile.ts` 三个函数、
  `OngoingItemsCard`（列表 + 暂停/继续/完成确认 + 失败重试 + 引用展示）与
  profile 页面集成；契约经 `openapi.json` 重生成同步
  `packages/contracts/src/generated.ts`。

## 6. 剩余限制与边界

- 接续匹配是确定性的「目标/条目文本唯一被提及」：用户只说「继续」而未点名
  事项时不接续（宁可不猜）；多目标同时命中一律放弃，交由用户澄清。
- 暂停的目标不参与接续（active-only）；恢复需页面操作或聊天生命周期信号，
  本票不主动提醒（明确不做：提醒调度/任务执行器）。
- 待续问题按词表（还没/尚未/没定/…）确定性判定，不引入模型判断；词表之外
  的未决表达按普通进展展示。
- 领域引用只存引用标识并由组合根注册的提供方实时解析；本票未接通任何真实
  领域（学习对接是 14 票），页面如实显示未解析。
- 上下文指纹包含来源消息内容哈希：来源被编辑会让指纹变化并触发重建；
  同一正文的不同来源消息不区分（指纹取消息标识与哈希组合的字段摘要）。
- 走查脚本曾出现「陈旧版本未被拒绝」的假阴性：脚本跨请求复用返回对象导致
  `version` 引用随暂停原地更新，属脚本问题（已改为 int 快照），生产代码无
  此行为（HTTP 响应均为新对象）。

## 7. 代码评审与修复

实现完成后按 `code-review` 技能做了双轴评审（规范轴 + 规格轴，各由独立
子代理执行），发现与处置：

- 文件行数超限（规范硬性）：`ongoing.py` 502 行、`api.py` 584 行超过共享
  规范「单文件不超过 500 行」。已拆分：纯投影层进
  `profiles/ongoing_projection.py`；三条路由与依赖进
  `profiles/ongoing_api.py`（并入父路由、文末 include、无自带 tags，OpenAPI
  与生成类型零变化）；错误映射进 `profiles/api_errors.py` 供两模块共用。
- 重复代码（经验判断）：`_ongoing_link_target` 与 `link_saved_facts` 重复
  「列出进行中目标」逻辑，已提取 `_active_ongoing_goals` 共用。
- 规格缝隙（规格轴）：空态文案曾写「新对话里说『继续』即可接上」，与实现
  「需点名事项才接续」矛盾；已改为「再提到这件事即可接着它的进展」。
- 未使用代码（规格轴）：前端 `updateOngoingDomainReference` 无调用方，属
  规格外新增；已移除，后端路由与合同类型接缝保留给工单 14 的学习对接。
- 键盘访问证据不足（规格轴）：新增「操作控件均为键盘可达的原生按钮」测试
  （原生 `button` + 聚焦后触发动作），前端单测 7 → 8 项。
- 保持不动的评审意见：`OngoingItemsService.set_state`/`set_domain_reference`
  的委托是组合根接缝（服务同时承载上下文与提供方注册），非多余中间人；
  `commit_rebuild` 参数成组属存储协议形状；`atomic.py` 行数为既有现状，
  本票不扩大重构范围；待续词表的误报边界已在 §6 记录。
- 评审后的回归：聚焦 969 通过（合并态）、全量分支独有失败 0、走查 37 项全
  PASS、`openapi.json`/`generated.ts` 零 diff（见 §3、§4）。

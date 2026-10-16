# 15 — 提供有依据的整体概览与开放主题浏览：验证记录

**分支：** `codex/issue-15-overview-and-topic-browsing`（基点 `ffcbe07f`，main；
验证过程中并入 `main@6661d47c` 的工单 14 改动——冲突仅在页面组成、
API 客户端与生成产物，手工并集后重生成 `openapi.json` 与
`packages/contracts/src/generated.ts`，无迁移号冲突，本票迁移 88 不变；
合并后聚焦/全量/静态检查/E2E 与走查全部重跑，结论见 §3、§4）。
**环境：** conda `agent`（Python 3.11.15，用直接解释器 `C:\Users\33755\anaconda3\envs\agent\python.exe`，
`conda run` 有插件错误不可用）；前端 Node v24.14.0 / npm 11.9.0。
**证据：** 本文 + `evidence/demo-walkthrough-20261016T131855Z.txt`（合并后重跑）+
`tools/walk_issue15_overview_demo.py`（可复跑；39 项检查全部 PASS，FAIL 0）+
新增浏览器 E2E `apps/web/e2e/issue15-overview-topic-browsing.spec.ts`（1 项通过）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/15-overview-and-topic-browsing/tools/
        walk_issue15_overview_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

（确定性走查约 30 秒；运行库在 `.tmp/issue15-demo-*`，只使用临时库与内存组合，
不读取或记录任何凭据与真实数据。已知坑：`remember`/`modify_item` 在内存仓库里
返回同一条可变对象，脚本与测试跨请求复用版本号前先取 `int` 快照；直接首导入
`bridges.profiles.overview_api` 会触发与 `ongoing_api` 相同的既有循环导入，
正常 `create_app()` 路径无碍；跑 pytest 前确保 `.tmp` 目录存在（basetemp 依赖）。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 每项概览陈述有可访问事实依据，不出现无证据人格、情绪或能力评价 | 走查 [1]（陈述正文 = 各段原文用「；」连接、逐段携带条目标识与版本、与当前条目逐字一致、全部条目恰好出现一次）；`test_issue15_profile_overview.py::test_statements_are_verbatim_compositions_with_versions`；E2E `issue15-overview-topic-browsing.spec.ts`（真实页面：概览先原样包含「我每晚只能抽出半小时」）。实现上陈述文本不可能是新句子：`build_overview_statements` 只做分组与 `"；".join` |
| 未知主题不丢条目，多主题不复制事实，分类调整不改变身份和授权 | 走查 [2]（多主题归到规范顺序更靠前的主题且只出现一次；自定义/未知主题原样成组；无主题陈述最后；近义只在折叠空白后精确命中时归一；调整主题只推进版本、标识与授权不变）；`::test_primary_topic_grouping_single_appearance_custom_and_untopiced`、`::test_topic_alias_normalization_is_exact_and_identity_neutral`、`::test_projection_canonicalizes_legacy_topics_for_consistent_browsing`（旧数据的浏览标签与新写入一致）；前端 `AtomicProfileCenter.test.tsx`「按主题筛选：多主题条目只出现一次，无主题单独成组」 |
| 编辑/删除后旧概览不再作为当前事实显示，迟到生成不能恢复旧内容 | 走查 [3]（编辑后重建出新正文；迟到重建世代 CAS 失败不写回；两个并发重建先提交者生效；删除后不再包含）；`::test_overview_rebuilds_after_edit_and_delete_never_serves_old_content`、`::test_late_rebuild_cannot_restore_old_content`、`::test_sqlite_overview_store_epoch_cas`；HTTP `test_issue15_overview_api.py::test_overview_route_follows_edit_and_delete_without_stale_content`；E2E：页面行内修改保存后概览即时显示新句、不再含旧句，删除后回到空态 |
| 无记忆、整理中、部分可用、失败和重试状态诚实，加载失败不显示伪造概览 | 走查 [4]（空态无陈述且不落空行；重组竞争只保留依赖一致的旧陈述并提示「部分」；全部失效返回「整理中」零陈述；关使用仍可管理并如实提示）；`::test_empty_state_writes_no_overview_row`、`::test_rebuild_competition_returns_partial_and_rebuilding_honestly`、`::test_usage_control_change_is_honest_and_keeps_management_view`；前端 `ProfileOverviewCard.test.tsx`（空/整理中/部分/关使用/加载失败+重试 8 项）；E2E `issue21-desktop-acceptance.spec.ts`「画像空态、设置与模型验证结果」三个视口通过 |
| 长条目和多主题在三个既有桌面视口可用，键盘及辅助技术状态完整 | E2E `issue21-desktop-acceptance.spec.ts` 画像用例在 1280×720 / 1440×900 / 1920×1080 各跑一遍（3/3 通过）；E2E `issue38-a11y.spec.ts`「画像中心页（/account/profile）结构合规」通过（无未命名控件/仅图标按钮/无 alt 图片/空链接）；前端测试覆盖键盘可达（原生 button、`aria-pressed` 主题筛选、`aria-expanded` 依据开关、`role=status/alert`、定位后 `tabIndex=-1` 聚焦与 `scrollIntoView`） |
| 概览不成为提取证据或默认全量上下文，账户和用途边界与原子记忆一致 | 走查 [5]（HTTP 账户作用域：注册后经真实路由读取概览）、[6c]（概览不进导出类别与逻辑摘要）、[6d]（不装概览监听器也能编译回答，编译无概览依赖）；`::test_account_isolation_and_store_rows_are_per_account`；`test_issue15_overview_api.py::test_overview_route_is_account_scoped`、`::test_overview_route_requires_login`。概览不参与 `compile_adopted_slice`，只服务页面 |
| 新派生状态的迁移、清理、账户删除、重启和重建均有验证 | 走查 [6]（v87 → v88 升级建表幂等、列齐全）、[6b]（重启后概览持久化可读、编辑后仍失效重建）、[6c]（备份含行、账户删除清空、删除顺序先于 `profile_items`）；`test_schema_v88_profile_overview.py`（2 项） |

## 2. 确定性走查（最终代码，20261016）

1. **可追溯依据**：三条目（称呼/兴趣爱好/现实约束）冷启动生成 current 概览；
   每条陈述逐段等于条目原文与当前版本，全部条目恰好出现一次。
2. **开放主题**：多主题条目只出现一次且归属规范顺序更靠前主题；未知主题
   （志愿活动）原样成组；无主题单独成组且最后；`normalize_topics` 精确归一
   与未知保留；调整主题仅版本 +1。
3. **失效与迟到守卫**：编辑小猫条目 → 概览重建显示「小白」；捕获世代后再次
   编辑 → 迟到提交 CAS 失败、读取给出「团团」；并发重建先提交者生效；删除
   后概览不含该条、其余照常。
4. **状态诚实**：空账户空态零陈述；模拟提交持续失败 → 部分可用（仅保留依赖
   一致旧陈述）；全部条目变更 → 整理中零陈述；恢复提交能力后重建回 current；
   关使用 → 提示文案且陈述仍可管理。
5. **生产组合 HTTP**：未登录 401；注册后 GET 概览逐段可追溯；PATCH 条目后
   概览显示新文；DELETE 后移除；关使用仍列出并提示。
6. **迁移/重启/边界**：v87 → v88 建表幂等；重启后新旧服务实例均能读概览并
   在编辑后失效重建；概览不进导出与逻辑摘要、备份可见、账户删除清空；
   不装监听器时回答编译照常。
6. **合计 39 项检查全部 PASS。**

## 3. 自动化测试

- 新增 `tests/profiles/test_issue15_profile_overview.py`（13 项）：陈述原文
  拼接与版本追溯、主主题分组/多主题不复制/未知保留/无主题成组、近义精确
  归一与身份不变、旧数据投影浏览归一、编辑删除不残留、迟到重建 CAS、来源
  消息变化失效指纹、关使用诚实、空态不落行、部分/整理中竞争、账户隔离、
  SQLite 存储 CAS。
- 新增 `tests/profiles/test_issue15_overview_api.py`（5 项）：GET 路由可追溯
  陈述、编辑删除跟进、未登录 401、账户作用域、controls 联动失效与仍可管理。
- 新增 `tests/storage/test_schema_v88_profile_overview.py`（2 项）：v87 → v88
  升级与幂等；派生性（导出/逻辑摘要不在、备份可见、账户删除清空、顺序）。
- 前端新增：`ProfileOverviewCard.test.tsx`（8 项）；`AtomicProfileCenter.test.tsx`
  增设「按主题筛选」「概览定位事件」「修改/删除广播数据变化」3 项（共 24 项）。
- 浏览器 E2E（合并态重跑，真实 API）：`issue15-overview-topic-browsing` 1/1、
  `issue20-profile-evidence-disclosure` 2/2、`issue21-desktop-acceptance -g 画像`
  3/3（1280×720 / 1440×900 / 1920×1080）、`issue38-a11y -g 画像中心页` 1/1，
  合计 7/7 通过。新增 issue15 spec 覆盖：概览原样汇总真实条目 → 点击段落定位
  （行高亮 + 依据展开）→ 行内修改保存后概览即时替换为新句 → 删除唯一条目后
  概览回到空态。
  运行方式：`BRIDGES_PYTHON=<conda agent python>` + `PYTHONPATH=<worktree>/src`
  后 `npx playwright test ...`（本机 3000/8000/8025–8027/8143 端口需空闲）。
  说明：引用 08 版之前旧画像中心的陈旧 spec（`issue25-profile-center`、
  `issue38-visual` 的「数字分身画像」）在 main 上即已失效（src 已无该页面），
  属既有漂移，本票不扩大范围。
- 聚焦（合并态，终态）：`pytest tests/profiles tests/storage tests/lifecycle
  tests/contracts` → **996 通过**（含本票 20 项与 14 票 7 项）。
- 全量（合并态，`-q -n 4 --continue-on-collection-errors`）：
  - 分支：**345 failed / 5818 passed / 39 skipped / 4 errors**；
    main@`6661d47c` 同命令：**344 failed / 5801 passed / 37 skipped / 4 errors**。
  - 逐条差集：分支独有失败 3 项均为 `tests/closeout/test_arxiv_worker_reliability.py`
    的 xdist 并发计时抖动（同文件在分支上串行 **14/14 通过**，且在合并前后两次
    串行全量中均未失败）；main 独有失败 2 项为 `test_api_boot` 环境性 flaky。
    无与本票改动相关的分支独有失败。通过数 +17、跳过数 +2 为并发调度下的
    抖动与新增用例共同结果，无状态异常。
  - 合并前对照（基点 `ffcbe07f`，串行全量）：分支 342 failed / 5816 passed /
    37 skipped；main 346 failed / 5792 passed / 37 skipped；分支独有失败 0。
  - 收集错误的 4 个 `tests/career_plan/test_issue29_*.py` 在 main 上完全相同
    （`CareerBackgroundLoader` 导入失败），为既有状态。

## 4. 静态检查

- `ruff check .`：合并态分支 **453** 与 `main@6661d47c` 同为 453，本票新增 0
  问题（交付前修复了 3 处新引入：1 处 F401、2 处 I001；导入事故期间发现的
  F821 已修复，见 §7）。
- `mypy src`：合并态 **141 errors / 23 files**（检查 702 个源文件；main 同命令
  141/23、检查 697 个），无新增错误。
- `pip check`：No broken requirements found。
- 前端（worktree，`apps/web`，合并态）：`npm run typecheck` 通过；
  `npm run test:unit` 37 文件 / **320 测试通过**（含 14 票的学习引用卡）；`npm run lint`
  无错误（仅既有 img/useEffect 警告）；`npm run build` 成功（含 `/account/profile` 页面）。

## 5. 接口与实现摘要

- 契约 `contracts/profile_overview.py`（88 行）：`ProfileOverviewProjection`
  （state/usage_enabled/notice/statements/generated_at）、
  `ProfileOverviewStatementProjection`（statement_id/topic/text/segments）、
  `ProfileOverviewSegmentProjection`（profile_item_id/version/text）、
  `ProfileOverviewState`（current/partial/rebuilding/empty）、合同版本
  `profile-overview-v1`。`contracts/atomic_profile.py` 新增
  `PROFILE_TOPIC_ALIASES` 与 `canonical_profile_topic`（精确近义归一）。
- `profiles/overview_projection.py`（162 行，纯投影）：`_primary_topic` 归属
  规则（默认主题按定义序在前、自定义按名称、无主题 None 最后）、
  `build_overview_statements`（原文「；」拼接、组内更新倒序、statement_id =
  `overview-` + sha256(topic)[:16]）、`parse_overview_payload`、
  `valid_remaining_statements`、`is_fresh`、三条稳定提示文案。
- `profiles/overview_repository.py`（215 行）：`StoredProfileOverview` +
  `ProfileOverviewStore` 协议 + InMemory/SQLite 实现（`profile_overview`
  单行/账户，失效 epoch+1，重建按捕获世代 CAS，同成同败进调用方事务）。
- `profiles/overview.py`（235 行，`ProfileOverviewService`）：`list_overview`
  （指纹一致直接投影；否则重建最多 `_OVERVIEW_REBUILD_ATTEMPTS=2` 次；仍失败
  按「部分可用/整理中/空态」诚实降级）、`invalidate`、`begin/commit_overview_rebuild`；
  依赖指纹含合同版本、使用开关与版本、条目 (id, version, topics)、来源消息
  (status, 内容哈希)。
- `profiles/overview_api.py`（48 行）：`GET /profiles/overview`（并入
  `profiles/api.py` 前缀与标签，文末 `include_router` 保持 OpenAPI 顺序）。
- `profiles/atomic.py`：`normalize_topics` 应用精确近义归一；新增
  `ProfileOverviewInvalidator` 接缝与 `set_profile_overview_listener`；
  10 处写入/撤回/控制路径改经 `_invalidate_derived_views` 同时通知事项与
  概览（失败仅告警，不回滚写入）；`project()` 对展示主题同样做浏览归一
  （旧数据与新写入标签一致，不改事实身份与授权）。
- 存储 `storage/database.py`：迁移 88 建 `profile_overview`（含 epoch、
  指纹、payload、失效标记列），`SCHEMA_VERSION` 87 → 88；
  `lifecycle/catalog.py` 登记 `ACCOUNT_TABLES`（先于 `profile_items`）。
  前端：`lib/api/profile.ts` + `fetchProfileOverview`、类型别名；
  `ProfileOverviewCard`（按主题陈述 + 逐句定位 + 诚实状态）、
  `AtomicProfileCenter`（主题筛选：全部/各主题/无主题，多主题不复制；
  定位事件滚动聚焦展开）、`AtomicProfileItemRow`/`EvidencePanel`（行与依据
  从原 737 行组件拆出）、`profile-events.ts`（window 事件，沿用
  `recent-conversations.ts` 约定）、`profile-format.ts`（共享格式化与文案）；
  契约经 `openapi.json` 重生成同步 `packages/contracts/src/generated.ts`。

## 6. 剩余限制与边界

- 部分可用路径（重建连续竞争失败）只按条目 (id, version) 复核旧陈述：来源
  消息可用性/内容变化在正常路径经依赖指纹整体失效，但连续竞争降级时该陈述
  仍可能短暂展示；陈述文本本身仍等于当前条目原文，且点击进入的依据面板
  按实时来源状态展示，不会把不可读来源冒充可读。这是有意的降级边界。
- 页面级失败态：加载失败与重试由前端单测覆盖；后端存储层抛错的模拟不在
  本票（无伪造路径：读取异常向页面报错，不返回空概览冒充成功）。
- 浏览器 E2E 覆盖主路径（生成/定位/编辑/删除）；主题筛选的交互逻辑由组件
  测试覆盖，未单独写浏览器用例（真实提取主题不稳定，避免脆测）。
- 引用旧画像中心（08 前）的既有 E2E spec 在 main 上即已失效，属既有漂移。
- 走查脚本文档性超长（644 行 > 500 行规范）：`.scratch` 验证工具有既有先例
  （13 票 654 行、10 票 832 行），未拆分。

## 7. 代码评审与修复

实现完成后按 `code-review` 技能做了双轴评审（规范轴 + 规格轴，各由独立
子代理执行），发现与处置：

- 重复实现（规范轴）：`ProfileOverviewCard` 内联的 `formatUpdatedTime` 与
  新建 `profile-format.ts` 逐字重复——已改为复用共享实现。
- 机制分歧（规范轴）：`profile-events.ts` 曾用手写 Set 总线，与仓库既有
  window 事件约定（`recent-conversations.ts` 的 `CHAT_LIST_CHANGED_EVENT`）
  并存两套通知机制——已改为 `bridges:profile-data-changed` /
  `bridges:profile-item-locate` window 事件（定位事件用 `CustomEvent`
  携带条目标识），调用方不变。
- 数据一致性（规格轴）：迁移不回填旧数据，旧条目未归一的近义主题在条目
  筛选与概览分组间可能显示两种标签——已在 `project()` 展示层做同样的浏览
  归一并新增测试；写入层归一 + 展示层归一保证新旧数据一致，均不改事实身份
  与授权。
- 导入事故修复：评审前一次导入块编辑误删 `ProfileOverviewProjection`/
  `ProfileOverviewState` 导入（ruff 全量对比 453 → 466 暴露 F821 ×11），
  修复后重新全量 pytest 与走查均重跑（§3 数据均为终态）。
- 规格轴其余处置：E2E 已补（新增 issue15 spec + 既有 profile/视口/a11y
  spec 回归，见 §3），三视口与 a11y 由 issue21/issue38 真实浏览器覆盖；
  失败态为前端重试覆盖率与后端降级语义共同覆盖（§6 记录存储异常不模拟）。
- 保持不动的评审意见：`ProfileOverviewInvalidator` 与
  `OngoingContextInvalidator` 两个协议保持分离（各带自身接缝文档，合并会
  把 13/15 两票耦合进一个派生视图抽象）；`overview_projection` 导入
  `StoredProfileOverview` 与 `ongoing_projection` 导入 `StoredOngoingContext`
  是同包既有先例（纯 dataclass，无存储句柄），不扩面重构；`normalize_topics`
  写入期归一由 PRD「近义主题可归一化」支持，并与概览分组一致所需；
  `AtomicProfileCenter.test.tsx` 770 行超出既有 666 行现状（组件本体已拆分，
  测试集中在同一组件文件便于对照）。
- 评审后的回归：并入 main@6661d47c 后重跑——聚焦 996 通过、全量差集无与
  本票相关的分支独有失败（仅 xdist 计时抖动，见 §3）、走查 39 项全 PASS、
  ruff 453 = main、mypy 141 = 基线、E2E 7/7 通过（§3、§4）。

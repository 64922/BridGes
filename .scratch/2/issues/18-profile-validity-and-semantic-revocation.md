# 18 — 治理画像范围、有效期和语义撤回传播

**What to build:** 过期或被忘掉的信息停止用于回答；普通近义重述与旧任务恢复不会复活，明确重新记住才允许恢复。

**Blocked by:** 02 — 补齐画像剩余规格与验收决策；07 — 分开画像记录与使用控制，保证即时撤回；16 — 以完整事实身份保存画像并处理并存更新

**Status:** ready-for-human

**优先级：** P0

## 背景与需求

一年后的“下周考试”仍可使用，删除跑步后普通“慢跑”会重新出现；撤回还需阻止旧切片/派生物用于后续调用。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/用户画像/复核脚本.py](../../../docs/用户画像/复核脚本.py)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。

## 任务内容

1. 落实 02 的适用范围、期限、暂停/完成/替代和续期合同。相对时间以来源消息时间为锚，不每天重解释，不编造未给日期；长期偏好没有统一短 TTL。
2. 区分这次要求与长期默认；本次简短/详细不改长期。过期、冲突未解决、可靠度不足或被替代条目在召回前过滤。
3. 墓碑/编辑抑制绑定可证实的事实身份与旧证据，确定同一近义普通提及不恢复；不确定身份不自动合并或恢复；明确重新记住按确认范围解除抑制并保存新来源。
4. 删除/撤回/纠正使依赖切片、摘要、个性化结论与旧运行后续调用失效；复用前重查权限与版本。已发送云端上下文无法收回，反馈不得宣称收回。
5. 按 02 的明确决定处理历史原文回补/摘要中的被忘事实，保持聊天原文保留/删除的独立合同；不从上下文工程范围自行推导新删除语义。
6. 用户编辑权威与自动可信门分开，LOW 或迁移/镜像降级来源不能绕过使用门；依赖无此画像的公开证据保留，不全任务重跑。

## 跨票接缝与责任

拥有生命周期/抑制与失效通知；13/14/19/22/29/37 在每次新调用和复用时消费有效性。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 过时“下周考试”不注入，来源时间锚可查；长期偏好不因统一 TTL 丢失。
- [x] 删除跑步后旧证据、普通慢跑自述和重试不恢复；明确重新记住保留新授权来源。
- [x] 修改换身份后旧值被抑制；暂停/完成/替代按 02 精确执行。
- [x] 撤回发生在运行中时阻止后续调用继续用旧切片，缓存失效按依赖传播。
- [x] LOW 镜像/迁移条目不作为确定事实召回，用户明确编辑的权威独立保留。
- [x] 历史阅读、导出、墓碑审计与账户隔离符合已确认合同。

## 验证与交付证据

用可控消息时钟、语义同一/不确定候选、后台提交竞争和旧运行恢复验证；检查模型实际输入。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-02）

**代码/合同版本**：分支 `codex/issue-18-profile-validity-and-semantic-revocation`，基线 `main@83c27f2f`；`ATOMIC_PROFILE_MIGRATION_VERSION` 保持 `profile-atomic-v1`；数据库 `SCHEMA_VERSION` 66 → 67。开发/验证均在 conda `agent`（Windows，显式 `PYTHONPATH=src`、UTF-8）。

### 实现说明

- **有效期**：`parse_validity_window` 在写入时按来源消息时间锚（`source_at`，缺省为写入时刻）把相对时间一次解析为绝对 `valid_from/valid_until`，并保留 `validity_anchor_at/validity_phrase` 供审计；后续使用日不重解释。长期偏好没有统一 TTL。「下周」按来源时间取周一 00:00:00 到周日 23:59:59 的整秒边界（内存与 SQLite 结果一致）。
- **生命周期**：`parse_goal_lifecycle_signal` 确定性解析明确暂停（暂时/先不…、暂停…）、完成（…完了/完成/结束）、恢复（继续/恢复/重新）；否定前缀（不想继续/别/没/莫）不当作信号。信号只对账既有目标（恢复不为过期目标续期，期限只有新日期才更新），不写新事实；没有可对账目标时按普通事实或既有抽取流程继续。
- **语义撤回**：删除/忘掉写墓碑并抑制同文本；编辑换身份写旧文本与旧事实抑制键；GOAL 的近义抑制与生命周期定位改用严格判等/包含（考研/考公不互相误伤），其他关系保留包含 + 字符重合候选；不确定候选只暂缓自动写入，用户明确「记住」解除抑制并只保存新授权来源。
- **召回门与重查**：`compile_chat_slice` 过滤过期/暂停/完成/被替代/LOW 且非用户编辑条目，原因串 `RECALL_LOW_CONFIDENCE_REASON`（低置信由用户编辑豁免）；切片带 `revocation_version`（活动条目 id/version/状态摘要），`is_slice_current` 供运行中复用前重查；`turn.py` 在轨迹汇编前重查，失效则降级为 EMPTY 披露。撤回经 `ProfileRevocationListener` 按来源消息通知（会话摘要 `invalidate_conversation`）。
- **迁移与回补**：v67 为 `profile_items` 增加 5 列（`valid_from/valid_until/validity_anchor_at/validity_phrase/goal_state`，`goal_state` 默认 `active`）；`migrate_account` 以 `_backfill_item_validity` 幂等回补既有条目的期限与目标状态，不改正文/来源/版本，计数写入日志。
- **自审修复**（送验前）：监听通知移到事务提交之后（编辑/替代/自动收回同此），避免摘要消费方各自事务与写入事务嵌套；停止记录期间的明确生命周期信号仍即时生效，没有可对账目标时按普通阻止并落墓碑；「作业搞定了」这类无目标措辞按普通事实写入；恢复信号复用运行记录时来源恒标 `local_rule`；暂停已完成目标不再计入 `matched_count`。

### 接口/迁移变化

- 合同：`AtomicProfileItem` 新增 `valid_from/valid_until/validity_anchor_at/validity_phrase/goal_state`；`AtomicProfileItemProjection` 同步；`ProfileSlice` 新增 `revocation_version`；新增 `ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL`、`AtomicProfileGoalState`、`ProfileRevocationListener` 接缝。
- `openapi.json`（309 paths）与 `packages/contracts/src/generated.ts` 已重生成并校验可再生成。

### 跨票接缝

- **13/14/19/22/29/37**：复用切片前用 `is_slice_current(account_id, revocation_version)` 重查，失效重编；依赖画像的摘要按监听器失效。
- **17**：生命周期措辞由本票在 `preprocess_message` 钩子先行消费，抽取侧无需重复建模。

### 验证结果

- 新增 Issue 18 测试：画像 54 项 + 自动入口/重试 4 项，内存与 SQLite 同场景全绿；`tests/storage/test_schema_v67.py` 覆盖 v66→67 无损升级与新列；既有语义撤回/缓存失效测试补充传播断言。
- 回归：`tests/profiles tests/storage tests/contracts` + 聊天子集 608 passed / 1 既有失败（`test_chat_correction_uses_latest_record_and_is_idempotent` 在 `main@83c27f2f` 独立复跑同样失败）；`tests/api`+`tests/lifecycle`+`tests/invalidation` 144 passed / 5 既有生命周期失败（main 同名同因）；`tests/chat` 全目录失败名单与基线逐名一致（100 = 100）。
- mypy：98 errors，与 main 基线一致且无受改文件新增；ruff：变更实现文件零新增（automatic.py 13 项 E501 与 main 完全相同），新增测试文件零诊断。

### 限制与边界

- 仅确定性规则与桩验证机制；真实模型抽取质量与外部可得性按 17/41 评测票。
- 嵌套否定（如「我不认为我考完了」）未识别为信号；非 GOAL 关系的近义判据仍是字符重合启发式。
- study 路径 teaching 审计在切片重查前取一次 `profile_items`，属审计展示的轻微时序边界。
- 既有 Windows 环境失败（聊天纠正平局、生命周期 409、chat 全目录 100 项等）与本票无关，未修复。


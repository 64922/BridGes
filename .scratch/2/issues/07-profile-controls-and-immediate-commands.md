# 07 — 分开画像记录与使用控制，保证即时撤回

**What to build:** 用户关闭自动记录后仍可记住、纠正、忘掉和删除；独立选择是否使用已有长期画像，本轮控制指令在生成前生效。

**Blocked by:** 02 — 补齐画像剩余规格与验收决策

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

停止自动记录的账户拦截发生在显式忘掉之前，造成用户已要求忘掉但条目仍存在；记录控制与使用控制也需分开。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/用户画像/复核脚本.py](../../../docs/用户画像/复核脚本.py)。
- [docs/人味化/讨论记录.md](../../../docs/人味化/讨论记录.md)。

## 任务内容

1. 先解析并受控执行用户管理意图，再判断普通自动记录是否允许。记住/忘掉/明确纠正本地处理，目标有实质歧义时只澄清必要对象。
2. 分别持久化自动记录开关与长期画像使用开关；关闭记录只阻止自动新增/更新，关闭使用不删除信息，主动管理操作仍可用。页面/聊天文案解释各自含义。
3. 生成前完成明确指令并读取更新后已提交信息；失败/未定位结果如实反馈，不声称操作成功，也不自动重复提取同一指令。
4. 关闭使用时不读取长期画像正文；当前用户原文仍参与正常回答。图片、助手文本、第三方材料和模型节点不得直接写用户画像。
5. 本票修复已确认控制和时序，不自行扩展“忘掉”到删除聊天原文；历史回补的语义由 02、18 承接。

## 跨票接缝与责任

提供 17 的队列许可检查与 19 的使用许可；现有逐条页面删除保持可用，新增控制走版本化 API 和账户隔离。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 记住跑步→停止记录→忘掉跑步能生成真实删除结果，列表和后续画像切片均移除。
- [ ] 记录关/使用开、记录开/使用关、两者关、两者开四种组合行为独立，跨进程读取一致。
- [ ] 停止自动提取期间明确记住/修改有效；关闭使用期间忘掉和删除有效。
- [ ] 纠正生成前生效，本次临时要求不改写长期偏好，模糊删除不批量误删。
- [ ] 请求幂等、账户隔离、状态导出与错误反馈验证；不增加逐条确认弹窗。

## 验证与交付证据

将停止记录后忘掉探针接到正式聊天与设置 API，检查模型真实输入和两账户隔离。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实现记录（2026-10-01）

### 实现说明

- **P0 修复（先解析管理意图，再判断记录许可）**：`preprocess_message`（`src/bridges/profiles/automatic.py`）中账户级「停止记录」阻止检查原先先于记忆指令解析，导致停止记录后无法忘掉。现重排为：停止记录指令分支 → 已有运行检查 → 显式记忆指令解析（`parse_memory_directive`）→ 记录阻止判定。阻止仅拦截「无指令且非纠正」的普通自述流；记忆指令（记住/忘掉）与明确纠正流在阻止期间仍可达。
- **两个开关分开持久化**：自动记录开关的权威状态沿用既有账户级隐私阻止规则（`profile_extraction_privacy_blocks` scope='account'，migration 43）；长期画像使用开关为新表 `profile_account_controls`（migration 62），行级默认开启、从未变更为版本 0。四个组合行为互相独立，跨进程读取一致。
- **生成前完成明确指令**：记忆指令在墓碑提交后本轮成立（沿用 06 机制），下一轮生成读到更新后的已提交信息；失败/未定位返回 `UNRESOLVED` 等如实状态，不声称成功。
- **关闭使用不读取画像正文**：`src/bridges/chat/turn.py` `_compile_profile_slice` 在编译切片前读取 `is_profile_usage_enabled`，关闭时跳过注入并写入 ContextNote（`ContextNoteState.OFF`）与审计；仅门控切片注入，当前用户原文与任务材料照常参与回答（R07 保留历史可读、R08 关闭使用同时停用历史间接个性化）。
- **不扩展忘掉**：忘掉仍只作用于画像条目（墓碑 + 来源撤回），不删除聊天原文（归 18）。
- **前端**：`apps/web/src/components/account/profile/ProfileControlsCard.tsx` 两个 `role="switch"` 开关（自动记录 / 回答使用长期信息），文案分别解释含义（关闭记录仍可手动记住/修改/忘掉/删除；关闭使用信息全保留）；失败时重拉真实状态并如实报错；不加逐条确认弹窗。既有逐条删除（`AtomicProfileCenter`）未改动。

### 接口/迁移变化

- `SCHEMA_VERSION` 61 → 62：新表 `profile_account_controls`（`account_id` 主键、`profile_usage_enabled` CHECK 0/1 默认 1、`controls_version='profile-controls-v1'`、`usage_control_version` 默认 1、`updated_at`）；纳入 `REQUIRED_TABLES` 启动完整性清单；`lifecycle/catalog.py` 账户表删除目录与画像导出类别同步登记（备份沿用既有迁移前自动备份机制）。
- 合同 `profile-controls-v1`（`src/bridges/contracts/profile_extraction.py`）：`ProfileAccountControlsProjection`（`recording_enabled`/`usage_enabled`/`usage_control_version`≥0/`usage_updated_at`）与 `ProfileAccountControlsUpdateRequest`（extra="forbid"，两字段 `bool|None`，至少一项否则 422/`ValueError`，文案同源 `PROFILE_CONTROLS_EMPTY_UPDATE_MESSAGE`）。
- 版本化 API：`GET/PUT /profiles/controls`（SubjectDep 账户隔离）。幂等语义：同值写入不推进版本；`usage_control_version` 0=从未变更，首次变化写 1。审计动作 `profile_controls_update` 记录目标值与生效值。
- openapi.json 与 `packages/contracts/src/generated.ts` 已再生成（309 paths）；`apps/web/src/lib/api.ts` 新增 `fetchProfileControls`/`updateProfileControls`。

### 跨票接缝

- **19（用途切片）**：接缝为 `AutomaticProfileService.is_profile_usage_enabled(account_id)` 与 `account_controls` 投影；本票 turn 门控是第一个消费者。
- **17（队列许可）**：重试任务执行前对记录许可的复查为既有行为，服务注释标明该接缝；队列侧无需改动。
- **11/18（澄清交互与删除聊天原文）**：模糊/含混删除目标的澄清交互不在本票；忘掉多命中沿用 06 已验收语义（显式复合目标多删并如实计数），本票以钉子测试交付「过短目标、未定位目标不删除任何条目」。

### 验证结果（conda `agent`，worktree @ 分支头）

- 新增后端测试 29 项全绿：`tests/profiles/test_issue07_profile_controls.py` 15、`tests/profiles/test_issue07_profile_controls_api.py` 7、`tests/storage/test_schema_v62.py` 4、`tests/chat/test_issue07_profile_usage_control.py` 3（含 P0 探针：停止记录→记住→忘掉真实删除；阻止期间纠正有效；阻止后普通自述 last_error 如实；重开记录恢复抽取；四组合独立；sqlite 跨进程双向一致；版本 0→1→2 幂等推进；账户隔离；第三方/转述材料不写画像）。
- 回归：`tests/profiles/test_issue06_profile_recovery.py` 与 `tests/contracts` 合并复跑 44 passed；官方复核脚本 `docs/用户画像/复核脚本.py` 的「停止记录后忘掉」场景输出 `{"kind": "forget", "status": "forgotten", "matched_count": 1}`。
- 前端 vitest：`ProfileControlsCard.test.tsx`（3）+ `AtomicProfileCenter.test.tsx`（6）共 9 passed。
- mypy/ruff 与主仓同命令对账：ruff 错误名单两侧逐名一致（全部预存在）；mypy 受检 4 文件唯一错误 `turn.py:4452` 主仓同在（预存在）。
- 全量对账：主仓基线与分支头两侧同跑法（同 deselect 3 条 `test_start_fails_*`）、失败名单逐名双向 diff，结果见验收合并记录。

### 限制与边界

- R08「关闭使用停用历史间接个性化」由 turn 切片门控单点实现；更完整的间接个性化治理由 18 接管。
- 记录开关无版本号：其权威状态是既有 privacy_blocks 行（migration 43 无版本列），版本号仅覆盖使用开关；如需对记录开关做乐观并发，需另行迁移。
- 忘掉不删聊天原文（归 18）；模糊删除的澄清交互归 11/18。
- 测试中的模型响应为确定性桩，只证明门控与持久化机制；真实模型体验按评测票验证。


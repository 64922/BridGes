# 07 — 区分当前状态、历史经历与有效期

**What to build:** 系统识别当前仍适用的事实和过去经历；明确完成、暂停或过期后，不再按旧目标和约束安排回答。

**Blocked by:** [06 — 修订与冲突](06-revision-conflict-and-deduplication.md)。

**Status:** ready-for-agent

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D01、D03、D05；T07、T12、T19。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

用户说下周考试并说明每周三晚的固定安排，随后完成考试；跨日重试不改日期，后续计划不再围绕已完成考试，但明确回顾时仍可找到经历。

## 依赖与实施范围

06 区分纠错与状态变化，本票完成时间和生命周期对实际回答的影响。

- 分别保留来源时间锚、原时间表达、明确起止和周期条件；不以执行日解释历史消息。
- 实现活动、被替代、过期、暂停和完成状态的持久行为、详情与基本管理反馈。
- 当前状态变化与历史事件分开，未明示期限保持未知；具体计划依赖无期限目标时按合同核实有效性。
- 采用前过滤不适用状态，历史仅在相关回顾用途使用；变更使已编译采用与派生依赖失效。

## 验收标准

- [ ] 相对日期、来源时区和周期安排解释正确，跨日/周重试不漂移。
- [ ] 完成、暂停、恢复只操作明确定位的目标，歧义不批量修改。
- [ ] 「继续」不自动延长旧期限，续期需新的明确依据。
- [ ] 过期约束不进入当前计划，历史事实仍可按适当用途读取。
- [ ] 未使用不自动过期，召回频率不增加事实可信度，不设统一遗忘 TTL。
- [ ] 页面、存储和采用一致；生命周期和时间数据支持迁移、导出、账户删除及恢复。

## 验证与交付

固定来源时间执行跨日/周、周期条件、重试、恢复和明确完成旅程；新会话验证不沿用旧目标。交付时间锚、状态转换、当前/历史使用规则供召回、事项和重整复用。

## 明确不做

不增加日历提醒，不从沉默或访问频率推断放弃，不迁移其他领域的目标所有权。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

2026-10-16：实施与验证完成（分支 `codex/issue-07-time-and-current-state`，基点 `14cea5f6`）。

- 交付：契约（`AtomicProfileRecurrenceKind`/`AtomicProfileRecurrence`、条目与证据投影
  `recurrence`）、`parse_validity_window` 产品时区（Asia/Shanghai）与周期解析、
  生命周期定位（精确优先/歧义空操作）、回顾用途采用（`DECISION_REVIEW_HISTORY`，
  tier 2 只填剩余预算）、`is_adopted_slice_current` 回顾历史豁免修复、迁移 83
  （`profile_items.recurrence_json`）、聊天采用标签「既往经历（回顾用途）」、
  前端周期证据展示、openapi 与 TS 契约再生成。
- 测试：新增 34 项用例（profiles 28 + chat 回归 2 + storage 2 + lifecycle 2），
  更新 2 个工单 18 文件的时区预期；聚焦复跑 2283 项，失败集合与既有失败逐条
  一致（chat 94 项逐名差集为空），profiles/storage/lifecycle/contracts 0 失败；
  回顾注入修复先失败后通过并经真实管道复证。
- 走查（真实模型）：时区窗口与重锚、周期结构化、暂停/恢复不续期、复盘载荷
  含「既往经历（回顾用途）」且用而不改条目、歧义不批量修改——全部通过；
  基线 deterministic 全 pass（recall-academic-control 为已知 D11 缺口），
  real 全 pass（同）。
- 验证记录：`.scratch/profile-memory-redesign/validation/07-time-and-current-state/verification-record.md`；
  走查证据 `.../evidence/demo-walkthrough-20261016T022737Z.txt`（+`-model-outputs.json`）。

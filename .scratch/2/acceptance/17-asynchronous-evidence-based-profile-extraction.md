# 工单 17 独立验收

日期：2026-10-02。交付提交 `d779585d`（代码与测试）、`a1ba85e2`（实施记录，状态 `ready-for-human`），原基点 `main@83c27f2f`。开发和测试使用 conda `agent`。

## Standards 轴

- **复核脚本缺交付（已修复）**：票面“验证与交付证据”要求“复核脚本扩展逐事实反例”，原交付未修改 `docs/用户画像/复核脚本.py`。已补充语义偏好/时间约束候选的正反例、语义预检放行清单和逐片段反例；脚本运行通过。
- **既有测试适配复核**：6 个既有测试的改动均为异步时机与逐片段/观察门禁变化所必需，未发现削弱断言。`test_issue04` 由精确列表改为集合断言后，仍保留“message-2 重试耗尽且不复活被删事实”的核心行为；`test_issue07` 的 EXHAUSTED 任务记账符合异步调度所需的审计语义。
- **Lint/类型**：变更文件 ruff 无新增诊断（automatic.py 13 处 E501 为基线既存）；mypy 全项目 98 错误与基线一致，变更文件无新增。

## Spec 轴

- **P1：语义候选被旧确定性分类整批否决（已修复）**。任务 5/8 要求「以后先给结论」「每天 30 分钟」等本地词表未命中的明确偏好/约束可由语义模型提出候选并依法提交，且“不能让过时的确定性分类标签再次否决所有有效语义候选”。原实现 `_resolve_candidate_action` 只按片段确定性分类放行：这类候选的片段分类为 `no_signal`，最终被 `IGNORE`，既不写入也不留观察（实测运行结果为 `succeeded_empty`），语义预检调用模型的接缝失去意义。
  - 修复：带精确区间的模型创建/更新候选，只要片段非硬禁止、非行为/模糊分类，候选文本自身通过可复用偏好/约束预检（`has_reusable_signal`），且证据支持规范值、否定与明示时间，即按明确事实提交；写入阶梯同步把这类候选计为明确事实。无关片段（第三方/无偏好文本）的候选仍零写入，硬禁止片段、行为/模糊候选与无区间历史路径行为不变。
  - 回归：新增 4 项语义候选测试（精确支持写入、明示时间保留写入、无关取值零写入、无关片段零写入）与 1 项邻近原文“仅回指、不得作为事实证据”载荷测试。
- **任务 1–7 复核通过**：异步登记幂等（账户+消息+抽取器版本+原文哈希）且仅回答 `DONE` 登记；模型调用在写事务外、提交前短事务复核 run/墓碑/原文哈希/许可/版本；逐候选精确区间核对否定、时间与取值支持；停止记录、墓碑、来源失效零写入；观察不自动晋升；消息重试不重复证据计数；账户隔离与有界重试沿用既有队列设施。

## 验证

- 本票新增测试：**18 passed**（原 13 项 + 验收修复新增 5 项）。
- `tests/profiles`：**429 passed / 1 failed**；失败 `test_chat_correction_uses_latest_record_and_is_idempotent` 为 Windows 时钟平局，在基线 worktree 全量复跑同样失败（单项复跑通过），与本票无关。
- `tests/chat`：**826 passed / 100 failed / 1 xfailed**；失败名单与基线 worktree 逐名 diff 完全一致（0 差异）。
- `tests/api` + `tests/contracts` + `tests/runtime` + `tests/observability` + `tests/ai`：**323 passed / 5 failed / 2 skipped**；5 项失败名单与基线 worktree 逐名一致。
- `mypy src`：**98 errors / 20 files**，与基线一致且 `automatic.py`/新增测试无错误；`ruff check` 变更文件无新增。
- `docs/用户画像/复核脚本.py` 运行通过：语义偏好/时间约束候选“精确区间支持→写入、无关取值/丢弃时间→零写入”，语义预检放行清单与逐片段反例输出符合预期。
- 真实模型抽取质量未在本票验收，按 41 评测执行。

## 验收边界

- 未向网关模型提供已存事实的标识/有效性（任务 3 表述为“只提供标识/有效性”的上界）；补证/更新由提交层的完整事实身份（Issue 16）完成，回指只使用邻近用户原文，邻近内容不进入事实证据（新增测试锁定该边界）。
- 永久失败保留一条 EXHAUSTED 重试任务记账，属异步调度的审计语义，不会被再次执行。
- JavaScript 真实模型抽取质量、成本参数与体验由 41 评测，不构成本票机制验收的通过证据。

## 合入与清理（2026-10-02）

- 分支先接入 `main@58962ddd`（Issue 18）与 `main@2b3dc57c`/`0af74c80`（Issue 21）；合并冲突仅 `src/bridges/profiles/automatic.py`（Issue 17 已删除的旧内联提取片段），按保留重构结构、移植 Issue 18 生命周期与 `source_at` 改动解决。
- 合并后回归：issue17 18 passed；`tests/profiles` 497 passed / 1 failed（时钟平局，`main@2b3dc57c` 同样失败）；`tests/chat` 874 passed / 100 failed / 1 xfailed，失败名单与 `main@2b3dc57c` 逐名一致；`tests/api`+`contracts`+`runtime`+`observability`+`ai`+`storage` 427 passed / 5 failed / 2 skipped，失败名单与基线一致；mypy 98 errors / 20 files 按文件计数一致；ruff 变更文件与基线一致。
- 已以 `--no-ff` 合入 main：`47dece1d merge: 验收合入 Issue 17 异步证据画像提取`；合并树与已验证分支树一致（tree `275f754e`）。
- 清理：删除 worktree `.worktrees/17-async-profile-extraction`、分支 `codex/issue-17-asynchronous-evidence-based-profile-extraction` 与验收 `baseline-check` worktree；`git worktree prune` 后仅剩主工作树。
- 推送：`github.com:443` 持续不可达，多次重试 `git push origin main` 失败；待网络恢复后重推。

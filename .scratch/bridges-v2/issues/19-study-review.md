# 19 — 逐题复盘与暂停恢复

**What to build:** 用户表示学完本节后，可一次回答一道覆盖本节知识面的复盘题；暂停辅导后从未问题继续。

**Blocked by:** 18 — 本节辅导

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 用户明示学完后触发 `tutoring → review`，由 `study.plan_review` 依据书页知识点覆盖矩阵生成可变题量与未问题计划；每次只向用户暴露一道题并持久等待。`study.grade` 保存题 ID、用户回答、正确／不完整／错误判定、标准答案和解释后才推进游标。`review → tutoring → review` 保留已问题与判定；新增同节页先合并知识范围，只重排未问题。结构不合法或失败时不消费题目，也不靠内存等待下一条消息。

- [x] 复盘仅由用户明示启动，题量随本节知识密度变化，覆盖主要概念且只考当前书页材料。
- [x] 每次只展示一题；回答正确给必要反馈，错误、不完整或不知道时立即给正确答案及简短解释，不要求补答同题。
- [x] 已问题、答案与判定持久保存；回辅导后继续未问题，追加同节照片时仅重排未问题并覆盖新增知识。
- [x] 判定失败、超时或结构不合法时停在原题并可安全重试；刷新或 SSE 重连不跳题、不重复判定。

## Comments

### 2026-09-26 实现与合并

- 从 main 的 `bd02191` 创建 `codex/19-study-review`，工作树位于 `.worktrees/19-study-review`。实现提交：`11ce975`、`e67f6b3`、`cb1a201`。
- 出题只吃本节书页：`study.plan_review` 按未问核心知识点生成可变题量的题目，题目越界、重复或与知识点依据不匹配即判结构不合法，不消费题目。`study.grade` 核验判定后才写题 ID、用户回答、正确／不完整／错误、标准答案与解释并推进游标；一次只向用户暴露一题。
- 阶段切换由显式意图驱动：`学完本节／开始复盘` 进复盘，`暂停复盘／回辅导／再讲讲` 回辅导，疑问句、否定句和普通作答不改变阶段。`review → tutoring → review` 保留已问题与判定；追加同节页只重排未问题并重算知识覆盖。
- `StudyState.review` 挂在既有 JSON 状态上，无数据库迁移；题库留在服务端，接口只回传已展示题与其判定（`public_view`）。判定与游标在同一消息终态事务内提交，失败回滚不留半截状态。
- 桌面端在辅导／复盘阶段显示「开始复盘／继续复盘／暂停复盘回辅导」按钮与状态说明，复用设计系统 Button；补上复盘子图节点的中文标签。

### 验证与审查

- 本票新测 `tests/chat/test_v2_19_study_review.py` 22 项，覆盖明示启动（含疑问句、未学完反例）、逐题暴露与单次判定、错误／不完整立即给答案、失败与超时停在原题可重试、结构不合法不消费题目、暂停恢复保留已问题、追加页只重排未问题并保留判定、提交失败回滚、旧回答不能判定后一题、停止不推进、重启与 SSE 重放不跳题且账户隔离。
- 分支全量（工作树，`pytest tests`，与 18 票同口径，两侧都不做 `--ignore` 且都带 `PYTHONPATH=src`）：**259 失败 / 4300 通过 / 43 跳过 / 3 deselect / 0 error**（4602 用例）。与 18 票分支全量报告（`.tmp/issue19/issue18-full.xml`）的失败名称集合**双向比对完全相同**（0 新增失败），用例差集恰为 +20（含本票 20 项，全部通过）。**注意此口径的 259 与 12／13／15／16 票那套「`--ignore=tests/humanize_eval` 且不设 `PYTHONPATH`」的 273／277 不可互比**（那套会多出约 20 条子进程导入失败）。分支原始报告随工作树一并删除，留档见 `.tmp/issue19/`（含 18 票报告与比对脚本，比对脚本可复现差集）。
- 该全量 16:23 启动，而末提交 `cb1a201`（16:31）另改了 `review_intent`／学习子图入口／聊天重试守卫并补 2 项用例。合并后在合并树上补跑定点：`tests/chat tests/contracts tests/closeout/test_api_boot.py` → **450 通过 / 101 失败 / 1 跳过**（`.tmp/issue19/post-merge-spot.xml`），101 条失败**全部落在上述既有失败名单内**，含 `cb1a201` 新增的两项用例通过。
- 前端：`vitest run` **22 文件 / 187 例**通过（`StudyProgress.test.tsx` 6 例覆盖按钮与状态文案），`tsc --noEmit` 干净。
- 桌面交互：Playwright `v2-19-study-review.spec.ts` 在 1280×720、1440×900、1920×1080 三档通过，`.last-run.json` 为 passed（截图原在 worktree 的 `.tmp/review-browser-results/`，随工作树删除；脚本本身在本票提交里）。界面用受控 API 替身，只证明桌面交互，不证明真实供应商可用。
- 迁移与撞号：本票无数据库迁移，`SCHEMA_VERSION` 仍为 58，迁移键 44–58 连续无缺号；下一票用 59。

### 合并记录（2026-09-26）

- 分支以 `--no-ff` 并入 main：合并提交 `a48c1bf`。合并树与分支树**逐字节相同**（`git rev-parse ^{tree}` 两侧同为 `e5d1651…`），因此分支侧结论直接适用于合并树。
- 合并时 main 与分支点同为 `bd02191`（期间无并行票推进），合并无冲突，未产生额外的冲突解决提交。
- 收尾实证：`apps/web/node_modules` junction 已拆链（主仓 `node_modules` 361 条完好）、工作树目录已删除、分支 `codex/19-study-review` 已删除、`git worktree prune --dry-run -v` 无输出、`git worktree list` 只剩主工作区、远程只剩 `main`。工作树里有 4 个目录（`.pytest_cache`、`.tmp/final-review`、`.tmp/pytest-basetemp`、`.tmp/review-fix`，共 567 个测试产物文件）带着实现会话沙箱进程留下的受限 ACL，普通权限与 `takeown` 都被拒绝（`git worktree remove` 因此只摘了注册、目录删不掉）；最终用提权脚本（`takeown /r` + `rmdir /s /q`）删除，日志留档 `.tmp/issue19/worktree-cleanup-elevated.log`。

### 待人工验收

真实 Qwen 出题与判定质量、真实书页材料的复盘覆盖面仍需人工验证；本次模型结果使用受控替身，不记为真实服务验证。总结由 20 票交付。

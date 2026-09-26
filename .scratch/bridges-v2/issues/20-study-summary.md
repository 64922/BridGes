# 20 — 学习总结

**What to build:** 完成复盘后，用户得到依据本节材料和实际作答形成的学习总结，并可继续追问同一小节。

**Blocked by:** 19 — 逐题复盘与暂停恢复

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 仅在所有计划题目已有持久判定后触发 `review → summary` 与 `study.summarize`。总结输入限于本节书页范围、辅导问答和已判定题，输出逐项关联证据；生成失败保留原阶段与题目记录。`summary → tutoring` 只处理同节追问，不重开新小节或改写历史判定。

- [x] 所有计划题目完成后才进入总结；结果清楚分述学过的知识、已掌握内容和待补的理解点。
- [x] 掌握判断与漏洞均可追溯到实际题目、答案或书页片段，不夸大没有验证的能力。
- [x] 总结后可返回辅导追问同节内容；学习新小节需要另开会话，旧阶段与总结可重开查看。
- [x] 失败重试不重复生成互相矛盾的总结或改变已判定题目；账户隔离和导出经过验证。

## Comments

### 2026-09-26 实现

- 从 main 的 `a10e674` 创建 `codex/20-study-summary`，工作树 `.worktrees/20-study-summary`。实现提交：`9619049`。
- 阶段机加入 `summary`：`StudyState` 新增 `summary: StudySummary | None`，`stage` 联合增一档；契约新增 `StudySummaryPoint`（`kind` 为 `learned`／`mastered`／`gap`，逐条带 `question_ids`／`fragment_ids`）与 `StudySummary`，`openapi.json` 与 `packages/contracts/src/generated.ts` 同步再生（`tests/contracts/test_openapi_sync.py` 守同步）。状态仍挂在既有 JSON 列上，**无数据库迁移**。
- 触发点：复盘最后一题经 `finish_review` 在**同一条消息的终态事务**内生成总结，门是 `state.review.complete`（计划题目已全部问出）。与合同「所有计划题目已有持久判定」的措辞差异记录在案：暂停跳过、已问未答的题**不阻塞**进入总结，而是如实计入「还需补的点」——服务端核验要求 `已问 − 判定正确 ⊆ gap`，未作答的题不可能被写成掌握；Spec 复核认为属措辞而非功能缺口。
- 不夸大能力由服务端核验兜底：条目不得空依据、不得引不存在题 ID 或本节之外的书页片段；`learned` 每条必须引本节片段；`mastered` 只能引判定为 correct 的题，且判定为 correct 的题必须全部计入 `mastered`；未答对（incomplete／incorrect／未作答）必须全部计入 `gap`；缺 `learned` 段即失败。结构不合法与核验不过统一映射 `study_summary_invalid`，超时与证据超预算映射 `timeout`。
- 失败与重试：总结与最后一题判定同事务提交，总结失败整轮回滚——原阶段仍是 `review`、题目与判定记录保留，重试恰好重跑一次判定加总结，不会留下互相矛盾的总结或改写已判定题目；更早的题永不被重新判定。
- 追加／替换同节书页会作废旧总结（`state.summary = None`）并只重排未问题，重排后重新生成，新总结覆盖新片段与全部已判定题。
- `summary → tutoring`：总结后追问回辅导答疑，总结随状态回放可见；学新小节仍须另开会话（入口守卫把 `summary` 与辅导、复盘同等对待为同节内阶段）。
- 导出：`study_states` 此前未登记，本票新增「学习小节状态」导出类目（按行导出 `state_json`，含阶段、书页、题目判定与总结），账户隔离由导出用例验证。**遗留观察（未改）**：`ACCOUNT_TABLES`／`DELETION_ORDER` 仍未列 `study_states`，删除账号时靠外键 CASCADE 兜底，属既有行为、不在本票范围。
- 桌面端：`StudyProgress` 在总结阶段显示三段（学过的知识／复盘已掌握／还需补的点），每条附「依据：」标签（题目标签为 `第N题「…」判定为正确／不完整／错误／未作答`，书页标签为 `上传第N页（书上第M页） · 位置 · 用户补录`），并按设计系统补 `study.summarize` 节点中文标签「整理本节总结」；总结阶段不显示阶段按钮。

### 验证与审查

- 本票新测 `tests/chat/test_v2_20_study_summary.py` 11 项（12 例）：末题判定当轮出总结且重放不再调模型、掌握与漏洞逐条追溯到真实题号与书页片段、导出含小结状态且跨账户不可见、五类失败（超时／结构不合法／越界题 ID／夸大掌握／缺书页引用）保判定并可重试一次后不再重生成、总结后追问回辅导且总结保留、追加页作废旧总结并重排后再生成、总结失败不能判定后续题（409）、总结中停止不改状态。`tests/lifecycle/test_exports.py` 加 1 例（`study` 类目 1 条、`state_json` 含阶段与总结、另一账户的小节不出现）。19 票夹具 `ReviewGateway` 扩了 `summarize`（末题判定现在会流入总结）。
- 定点回归：`tests/chat tests/contracts tests/closeout/test_api_boot.py` → **461 通过 / 101 失败 / 1 跳过**（`.tmp/issue20/branch-spot.xml`）。与 19 票合并后定点（`.tmp/issue19/post-merge-spot.xml`）逐用例比对：**新增失败 0、消失失败 0、结果变化 0**，用例差集 +11 恰为本票 `test_v2_20` 的 11 例（全部通过）。
- 分支全量（提交 `9619049` 之后，工作树，`PYTHONPATH=src`、仓外 basetemp、按既有跑法 deselect 三条本机挂死的 `test_start_fails_*`）：**259 失败 / 4316 通过 / 41 跳过 / 3 deselect / 0 error**（4619 用例）。与 18 票分支报告（`.tmp/issue19/issue18-full.xml`）逐用例比对：**新增失败 0、消失失败 0、减少用例 0**；用例差集 +34 = 19 票的 22 例 + 本票的 12 例，全部通过；另有 2 条 `tests/runtime/test_runtime_contract.py` 启动用例在本跑法下由 skip 变 pass（子进程需 `PYTHONPATH=src`），即 Δ跳过 −2 的来源。留档 `.tmp/issue20/`（含比对脚本 `compare_outcomes.py`，可复现差集）。
- 静态检查：mypy **115 errors in 23 files**、ruff **594**，与 main 基线逐数字相同（本票新文件 0 新增）。
- 前端：`vitest run` **22 文件 / 189 例**通过（较 19 票 +2，均在 `StudyProgress.test.tsx`：三段与依据标签、全对时的空段兜底文案、总结阶段无按钮）；`tsc --noEmit` 干净。
- 桌面交互：Playwright `v2-20-study-summary.spec.ts` 与 19 票 `v2-19-study-review.spec.ts` 共 6 例，在 1280×720、1440×900、1920×1080 三档全通过（`.tmp/issue20/e2e-final.log`）。界面用受控 API 替身，只证明桌面交互，不证明真实供应商可用。
- 两轴 code-review（Standards + Spec 并行子代理）已跑，据其修：`learned` 必须逐条引本节片段、删掉与公开契约重复的私有模型、`_section_sources` 命名与「空问题不参与相关性排序」注释、TSX 去掉非空断言、依据标签与服务端渲染逐字对齐（vitest 与 e2e 同步改）。**有意保留未改**：`summary.py` 与 `review.py` 的 `_call` 结构重复、阶段白名单在多处重复、证据超预算被折叠成通用文案——留待后续重构票。
- 迁移与撞号：本票无迁移，`SCHEMA_VERSION` 仍为 58，迁移键 44–58 连续无缺号；**下一票用 59**。

### 合并记录（2026-09-26）

- 分支以 `--no-ff` 并入 main：合并提交 `a1d04ad`。合并树与分支树**逐字节相同**（`git rev-parse ^{tree}` 两侧同为 `b0f0c20…`），因此分支侧的全量与定点结论直接适用于合并树。
- 合并时 main 与分支点同为 `a10e674`（期间无并行票推进），合并无冲突，未产生额外的冲突解决提交。
- 合并后定点复核：`tests/chat tests/contracts tests/closeout/test_api_boot.py` → **461 通过 / 101 失败 / 1 跳过**（`.tmp/issue20/post-merge-spot.xml`），与分支定点逐用例比对 **0 新增失败、0 消失失败、0 结果变化**；前端 vitest 22 文件 / 189 例通过、`tsc --noEmit` 干净。
- 收尾实证：`apps/web/node_modules` junction 已拆链（主仓 `apps/web/node_modules` 361 条完好）、工作树目录已删除、分支 `codex/20-study-summary` 已删除、`git worktree prune --dry-run -v` 无输出、`git worktree list` 只剩主工作区、远程只剩 `main`。工作树内 `.tmp`（e2e 运行目录）、`.pytest_cache`、`.mypy_cache`、`.ruff_cache` 本票未出现 19 票那种受限 ACL，`git worktree remove` 一次成功（无需提权脚本）。

### 待人工验收

- 真实 Qwen 生成的总结质量（三段是否切题、是否只是复述题目与答案）与真实书页材料下的覆盖度仍需人工看；本次模型结果使用受控替身，不记为真实服务验证。
- 建议人工顺序：拍照开小节 → 辅导追问 → 说「学完本节／开始复盘」→ 逐题作答（含一题错误、一题暂停跳过）→ 看总结三段与依据标签是否与实际作答一致 → 追问同节内容 → 再拍同节新页看总结是否重算。

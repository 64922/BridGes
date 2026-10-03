# Issue 27 独立验收、修复与合入记录

日期：2026-10-03。验收固定基线 `main=68357a208f3e22b6fdea0445377ea383a80a9fcb`。原交付 `5a960f3d`、`c5c42a53`；分支 `codex/27-question-driven-tieba-evidence`，工作树 `D:\BridGes\.worktrees\27-question-driven-tieba-evidence`。编码代理报告只作定位线索，以下来自独立代码审查、反例与当前测试。

用户已授权修复、合并、经网络代理推送与清理本票工作树/分支。其他任务工作树及主工作树已有未跟踪文件保留。

## 审查来源与前置

使用 `code-review` 技能，以 `git diff 68357a20...HEAD` 及提交列表固定范围，分别由两个只读代理审查规范轴和需求轴。读取票面必读文档、AGENTS/CONTEXT、工作流交付与核验合同、人味化实施方案和 V2 外部可得性门。

前置 10/12/15/21 已在 main：10 的独立验收与 `7621f1c6`，12 的验收修复 `43d45c6c` / `d3a3861c` 及模块续接集成 `605d256c`，15 的 `5d95d7c3` / `395e255d`，21 的 `2b3dc57c`；核对票内实际记录、Git 历史和现有内核/路由/任务上下文/表达接缝，未以状态字段代替实施证据。

## Standards：规范符合性

初审 3 项硬违反：官方主体/用途/未知时效被关键词命中替代；没有时间或校区不适用仍宣称新规定；引用和冲突核验只检查部分字段。依据 `daily-workflows.md` 的主体/校区/用途/有效期及明确替代合同、`orchestration.md` 的引用/裁决核验及人味化方案的引文保真要求，均阻止初始放行。

修复后逐项关闭。复审额外发现终态事务补 `completed_at` 后与证据投影不一致；现在仅规范化终态时间，其他字段和正文严格比较，正常交付与篡改拒绝均有回归。规范轴最终未发现阻断项。

判断性建议：重复名词合并和诊断计算属于维护建议，不为本票扩大重构；无独立职责的 `_confirmed_post` 转发已删除。两路串行符合“可并行”的许可措辞；生成合同对齐不构成无关产品改动。

## Spec：需求符合性

初审 5 项缺陷：官方适用性过度放行；没有明确替代依据的冲突裁决；`records`/`queries` 恢复字段不一致；回复日期、引用位置及完整冲突未经核验；成功交付允许归纳候选且父图只认 DONE。另复验发现年月不符通知仍因年份相同而放行。均已修复，需求轴最终未发现范围内阻断。

| 票面验收 | 实际代码与独立证据 | 结论 |
| --- | --- | --- |
| 三类顺序、来源与共享预算 | 七节点配方；规定类官方先行、体验类跳过官方、混合独立串行；API 全链检验官方查询先于贴吧；同一账本 4 次调用且 active=0 | 通过 |
| 只有标题/摘要不总结、不称共识 | 未读候选只能进入帖链投影；确认归属需真实页面；段落按真实回复重建并精确核对 URL/楼层/日期；A14 与访问受限测试 | 通过 |
| 时间/范围冲突如实分列、域名不自动放行 | 本校/主管部门、用途、全部校区、学生类型、年月日与明确受理期限核对；未知相对时效、转载他校、新闻、过期通知保留未核实；只有明确调整/替代、完整生效日、更早的每条引用日期及适用性均成立才采用新规定；否则双方保留；A15 及反例 | 通过 |
| 只查贴吧与真实降级 | 父图传来源硬条件，官方核验零搜索/抓取；访问受限不绕过，未读不包装为回复总结 | 通过 |
| 引用一致、恢复不重复有效取证 | 回复模型、读取元数据、段落及冲突全字段比对；真实持久载荷 `records` 恢复成功空查询；官方保存发现记录与候选，已成功查询/已读页面续用；QUALIFIED verify、哈希、版本、依赖链与父图最终正文复验 | 通过 |

## 修复内容与合同

- `evidence.py` 收紧官方适用性和替代裁决，保留原始生效日；相同否定原文不制造冲突；有明确截止范围的过期通知不能当当前规定。
- `lexicon.py` 保留日期到日，年月不符不能只靠年份通过。
- `kernel.py` 完整核对取证与最终引文/裁决；`verified_projection` 拒绝缺 verify、非 QUALIFIED、哈希/依赖不一致和篡改投影。
- `service.py` 成功交付只认可信 verify；父图根据本轮收据复验投影与正文。停止/失败保留候选产物以诚实降级，不能借此成功交付。
- 官方路径新增带查询记录与候选的内部 `OfficialOutcome`，恢复不会重复有效发现查询；搜索恢复读真实 `records` 字段，兼容旧 `queries` 载荷。
- 官方、搜索、综合、核验能力版本提升到 v3；配方 `tieba-recipe-v1`、对外计划/核验合同保留默认兼容字段。数据库 SCHEMA_VERSION 仍为 68；复用既有节点产物/收据生命周期和账户作用域，无新增表或迁移。
- 原提交生成的 `openapi.json` 与 `generated.ts` 成对保留。main 的 API 合同漂移由独立同步测试复现，本分支同步通过；没有新增前端行为。

## 验证方法与结果

全部开发/验证使用 conda `agent`（Python 3.11.15）。固定工具和模型替身证明机制，不作为真实页面可得性或模型体验证据。使用独立 `--basetemp`，避免固定目录文件锁。所有失败与 `main@68357a20` 独立复跑逐名比较。

原始主审反例先有 9 failed，修复后通过；补入真实恢复字段、完整引用、可信交付、年月日/过期/受众反例。两位复审代理独立跑两专项均通过（最终 26 项）。主审最终专项/合同/架构组合 51 passed；贴吧/合同/架构组合 117 passed / 2 failed，两个失败均在 main 复现：`test_other_modules_still_rejected_and_no_silent_search`、`test_plain_chat_suggests_tieba_without_searching`。

main 集成基线：`tests/chat tests/kernel tests/contracts tests/api tests/tasks tests/commute tests/github tests/resources` 为 1586 passed / 51 failed。本票同组最终结果及逐名差异保存在 [summary.json](../validation/27-review/summary.json)，以完成后的记录为准。

`ruff src/bridges/tieba tests/tieba` 全部通过；共享接缝的已有 ruff 诊断与 main 比较，无新增。`mypy src/bridges/tieba --no-incremental` 基线 22 错误，本票最终同为 22（新增可空服务/日期元组错误已修复），贴吧模块零错误；逐条归一化行号后比较记录在 summary。

首次集成运行受固定 pytest 临时目录锁影响，大量 setup error；这些不计为产品回归，已采用分离目录完整重跑。验收中新增测试曾错用 `assistant['id']`，已改为真实 `message_id` 并复验；没有降低产品断言。

## 剩余限制

1. 两路独立但串行，`parallel_evidence=False`；工单没有要求并行。共用总预算，不额外发起人味化或分类模型调用。
2. 官方/冲突检查采用确定性保守规则。不能证明任意自然语言通知的语义适用性；缺主体、日期、范围或明确替代证据则呈现原文并保持未核实/冲突，不对未知作确定断言。
3. 真实贴吧楼层、官网可得性和真实模型体验由工单 42 验证；本票未访问真实取证站点、不承诺完整抓取。
4. UI 结果卡扩展归工单 38；未运行前端构建，已有生成类型与后端合同同步已验证。
5. 保留已证实的 main 测试和类型/规范诊断，不将其混入本票修复。

## 合并、推送与清理

合并前经用户现有 `socks5h://127.0.0.1:7890` 命令级代理 fetch；验收当时 `origin/main` 与本地 main 同为 `68357a20`。不修改全局/仓库代理配置。

### 执行完成（2026-10-03）

- 验收修复提交 `3c13a30c`；main 合并提交 `0fc77251`。合并时本地 main 已由并行会话推进到 `9d2b31bf`（含 Issue 28/31），推送前 fetch 后远端为 `5d0aec96`（Issue 28/31 收尾记录）。
- 自动合并仅在 `src/bridges/chat/task_materials.py` 冲突：main 侧新增的 `"career"` 声明与本分支新增的 `"tieba"` 声明落在同一插入点，解决为两条声明都保留；解决后导入、`git diff --check` 与 ruff 均通过。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 自动合并后按合并树重生成（`PYTHONPATH=src scripts/regenerate_openapi.py` → 311 paths；`openapi-typescript@7.13.0`），`tests/contracts/test_openapi_sync.py` **2 passed**。
- 合入后在最终 main 复跑专项组合 `tests/tieba tests/contracts/test_openapi_sync.py tests/architecture`：**118 passed / 2 failed**，两个失败与基线同名同因（`test_other_modules_still_rejected_and_no_silent_search`、`test_plain_chat_suggests_tieba_without_searching`，已在合并前 main `9d2b31bf` 独立复现）；`mypy src/bridges/tieba --no-incremental` 22 errors 与基线一致，共享接缝 ruff 114 条无新增无消除。
- 推送：`git -c http.proxy=socks5h://127.0.0.1:7890 push origin main`（`5d0aec96..0fc77251`）；`git ls-remote` 核对 `main == origin/main == 0fc772512340200ed2c1785031c47df9c286d726`。
- 确认 Issue 工作树无未提交改动（HEAD `3c13a30c`）后删除 `.worktrees/27-question-driven-tieba-evidence` 与分支 `codex/27-question-driven-tieba-evidence`；`git worktree prune` 无额外失效记录。
- Issue 28、31 的工作树与分支已由各自并行代理在其收尾提交（`0e8e22c5`、`5d0aec96`）中完成清理，本票未触碰；`.worktrees/26-github-requirement-evidence-matrix` 残留目录与主工作树未跟踪验证文件（`24-main-baseline.xml`、`24-main-integration.xml`、`27-review/main-*.xml`、`ruff-main.json`、`25-main-baseline.xml`）保留。

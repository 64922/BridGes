# 工单 28 独立验收

日期：2026-10-03。原交付为 `f99e9299`，分支 `codex/28-auditable-job-sample-analysis`，固定基线为 `main@68357a208f3e22b6fdea0445377ea383a80a9fcb`。开发及验证使用 Windows、conda `agent`、Python 3.11.15。编码代理报告仅用于定位；以下结论来自工单、正式合同、实际代码和本次运行。

结论：原交付未达标；修复并复验后，工单确定性机制验收通过。真实招聘来源可得性和真实模型体验仍由工单 42 验证。

## Standards 轴

按 `code-review` 技能由独立规范审查代理执行，发现 2 项硬违规，均已关闭：

1. 失败投影离开提交守卫事务后独立写回，违反 `docs/workflow/delivery-and-validation.md` R03 与发布不变量。现失败投影、正文及 ERROR 终态通过既有 `finalize_message` 与 `guard.verify()` 共用一个写事务，提交后再抛模块错误。无守卫的 `_persist_failure` 已删除。
2. 新增职业显式入口回退将已判明确岗位意图的自然语言查询置为普通聊天，违反 ADR-0033、workflow D01/D15 及本批 README 的合同替代。现移除回退，真实 API 回归验证无 module_id 的明确岗位查询直接交付，并不转入个人规划。

复验：原硬违规全部关闭，未发现需处理的判断性代码异味。没有以代码长度或工具已检查的事项替代规范审查。

## Spec 轴

独立需求审查发现 4 项初始缺陷，均修复：

1. 全来源失败的采集/核验产物仍被标记完成，重试永久回填旧失败。现采集失败保留真实查询记录，提交失效产物和失败收据；API 失败→成功回归验证查询从 3 次增加到 6 次，parse/plan 产物复用，collect 与下游重新执行。
2. 失败写回存在租约/停止/任务版本竞争窗口。采用上述原子收敛修复，验证失败提交时事务真实开启，以及来源失败伴随租约转移时旧执行者不得写投影。
3. 港元、欧元、日元、ISO 币种与 JSON-LD 外币可能进入人民币统计。现保留币种，按币种和时间单位分组；JSON-LD 保留页面 currency，缺币种不比较；非人民币 K 金额没有周期时不推定月薪。
4. 显式 `12薪` 被当作默认值丢弃。现保留页面明确给出的月数，缺失不补值，不混入金额换算。

进一步逐项复验修复：

- “后端测试工程师”“后端测试开发工程师”“前端工程师（后端接口方向）”原会被“后端”泛词抢先纳入。现比较目标/相邻职位的具体匹配证据，明确相邻职位优先于泛词；合法“算法工程师（数据分析方向）”仍保留。
- parse 的背景摘要原只含条件，不含实际读取的任务目标，同一句“继续”可复用旧目标。现将目标纳入摘要，真实内核回归验证目标改变时解析不复用。
- 用户未指定城市时，页面缺城市的岗位原进入样本统计。按工单“城市未知样本不混统计”统一降级为未确认链接，保留缺失说明，不进入任何样本统计；不强迫用户填写城市，已知城市岗位可跨城市纳入。必要质量门也拒绝未知城市。
- 偶数样本原取较高中间值作为中位数。现使用两个中间值的中位数，与既有整数金额合同保持一致。

薪资新增回归修复前实际为 8 failed；失败来源 API 回归修复前实际停在 error；相邻标题的三个筛选回归修复前全部误纳入。修复后均通过。

## 逐项验收证据

| 工单要求 | 实现与本次验证证据 | 结论 |
| --- | --- | --- |
| 六节点登记，原话/职责/阶段/城市/经验，公共情报与个人规划分离 | `career_plan/kernel.py` 六节点配方；parse 与模块上下文；配方登记及自然语言 API 查询，无画像/简历、无 career_planning 投影 | 通过 |
| 登记公开来源、真实详情与链接，摘要不能成为分析 | `searching.classify_hits` 来源筛选；`collecting.parse_job_page` 与结构化字段；受限页及无详情链接降级 API 测试 | 通过 |
| 职责语义证据、相邻岗位排除、去重/过期/城市/经验核对 | `filtering.py`、`lexicon.py`；职责两票/相邻占优、三个修饰标题、去重、过期、未知城市/经验剔除回归 | 通过 |
| 币种/时间单位/月数/缺失保留，不混算，不称市场总体 | JSON-LD→parse→aggregate；多币种、外币缺周期、12薪、缺薪与中位数回归；确定性输出样本范围及小样本边界 | 通过 |
| 上海改杭州新版本，旧统计失效，恢复不全链重跑 | API 城市切换只纳杭州；输入依赖失效；成功重试零查询/读取；失败重试复用 parse/plan；任务目标变化不复用解析 | 通过 |
| 输出范围/能力分布/薪资口径与缺口，未知个人能力不判薄弱 | `analyzing.py`、`presenting.py`、`advising.py`；公开样本能力统计与推断标注；无个人能力事实写入 | 通过 |
| 29/37 接缝、账户隔离、历史读取与生命周期 | 公开 `CareerPlanProjection`；新增字段有默认值，旧投影兼容回归；消息既有 JSON 列与内核既有产物/收据表，沿用统一导出/删除/守卫与审计，不新增表结构；全量及关联任务/内核验证 | 通过 |

前置 10/12/15/21 已在固定 main 中有实际实现：共享节点内核与守卫、混合入口/任务关系、任务材料选择、表达策略；并查阅相应票据验收记录。相关代码路径通过本次关联回归复验，不仅依赖 Status 字段。

## 版本与迁移

配方 `career-job-sample` / `career-job-sample-recipe-v1`；最终能力版本 parse-v3、plan-v2、collect-v2、filter-v3、analyze-v3、verify-v2。能力版本变化阻止升级前旧产物误复用。领域新增字段为 add-only，不增加数据库列；旧结果通过字段默认值读取。个人规划的背景/差距归属 29，不将公开岗位要求写为用户事实。

## 验证结果与限制

1. 全量除挂起烟测：`python -m pytest tests -q -n 4 --ignore=tests/integration/test_runtime_smoke.py --basetemp=<独立目录> --junitxml=<输出>`。分支 **5032 passed / 213 failed / 39 skipped**；固定 main **5004 passed / 218 failed / 39 skipped**。失败 ID 逐项对账见 [比较记录](../validation/28-baseline-comparison.json)。
2. 全量唯一分支新增失败是 arXiv 工作者预热计时；main 另有同文件握手计时失败。相关生产代码和测试没有本票改动；负载降低后两边隔离复跑整文件均 **14 passed**。因此复验后无本票引入的未解决失败；其余共享 212 项失败属于 main 既有。
3. 全量运行后继续修复标题、目标摘要和未知城市边界；最终关联组合 `tests/career_plan tests/kernel tests/routing` 加 golden 路由、Issue 12 hybrid_entry/execution_guards、Issue 15 task_materials 为 **304 passed**，包含职业模块 **100 passed**。最后城市质量门及薪资独立组合 **17 passed**。没有以修复前全量通过数代替后续改动复验。
4. Ruff 本票领域及测试、understanding 全过，`git diff --check` 通过。相同 mypy 范围分支与 main 均 **22 errors / 10 files**，逐条诊断相同，无新增错误；职业模块零错误。
5. 初次不排除烟测的全量在分支/main 同一进度挂起，已中止；前置 21 的验收也记录该本机限制，本次没有声称该烟测通过。并发测试曾因仓库默认共享 basetemp 发生文件占用，后续所有验证均显式隔离；这类失败不计为产品回归。
6. `independent_review` 是可选门，记录 executed=false，未运行独立模型复核；确定性页面替身仅证明机制，不能证明真实招聘页面可得性或真实模型体验。多城市目前明确披露仅首城市检索；不进行汇率或年度薪资换算。

## 合并、推送与清理

验收修复提交 `fee2f01a`；main 合并提交 `72163cd7`，无文本冲突。本地 main 合并时已包含并行 Issue 31 的提交（`24516100`、`21f16543`、`9d2b31bf`）。

- 合入后在最终 main（`9d2b31bf`）复跑职业模块 `python -m pytest tests/career_plan -q`：**100 passed**，与验收阶段一致。
- 推送：`git -c http.proxy=socks5h://127.0.0.1:7890 push origin main`（HTTP CONNECT 直连不可用时沿用 SOCKS5），远端由 `68357a20` 前进到 `9d2b31bf`；`git fetch` 后核对 `main == origin/main == 9d2b31bf`。
- 确认 Issue 工作树无未提交改动（HEAD `fee2f01a`）后删除工作树；首次在工作树目录内执行 `git worktree remove` 时记录已注销但残留空目录（Windows 占用 Permission denied），改从主工作区删除空目录成功。本地分支 `codex/28-auditable-job-sample-analysis`（原指向 `fee2f01a`）已删除。
- `git worktree prune --dry-run --verbose` 与正式 prune 均无额外失效记录；当前 worktree 清单为 `D:\BridGes`（main）及并行代理的 Issue 27、31 工作树，未触碰。Issue 24/27/31 的验收与验证文件及主工作区未跟踪文件（`24-main-baseline.xml`、`24-main-integration.xml`、`27-review/`、`25-main-baseline.xml`、`31-merge-core.xml`）均保留。

# 29 — 依据最小背景区分个人差距与待确认项

**What to build:** 用户要求个人准备建议时，得到岗位证据与明确背景支持的差距和行动优先级，未知能力作为待确认项。

**Blocked by:** 19 — 生成前编译用途明确的完整画像切片；28 — 用可核验岗位样本交付职责与薪资分析

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

岗位技能要求不证明用户不具备该技能；个人建议需有背景证据、可执行约束和明确推断边界。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。

## 任务内容

1. 登记 career.background/gap/advise/verify；仅个人规划分支读取当前陈述、允许使用的 19 切片或相关简历片段。
2. 逐项对照岗位需求与用户证据，分别表示已有依据、明确待提升、待确认；自述/材料/推断来源分开，背景不足先交付岗位分析再问一个影响建议的关键问题。
3. 按照当前目标/时间/资源约束编排优先行动，建议标注直接依据与推断；兴趣只调整建议不替换本轮岗位目标。
4. 个人差距依据不足、关键综合判断按风险独立复核；失败不把未知改为不足，必要结论保持阻塞。
5. 用户目标含学习资料或实践项目时输出最小需求产物供 37 组合；简历原文/完整画像不发公网，岗位结果不反向自动写画像。
6. 背景编辑/删除/过期使依赖建议失效，公开岗位证据可保留；每次调用检查切片版本/来源/预算。

## 跨票接缝与责任

拥有个人差距量表与建议证据；19/18 拥有画像有效性，28 拥有样本，37 调度下游资料/GitHub。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 无技能依据时为待确认而非弱项，已确认差距能定位用户与岗位两侧证据。
- [x] 只岗位请求不进入个人背景流程；背景不足仍交付真实岗位部分。
- [x] 每天可用时间等约束实际影响行动可行性，当前要求覆盖默认偏好。
- [x] 删除/过期/错误画像不产生旧个人结论，公开样本无依赖部分复用。
- [x] 公网参数不含简历/私人原文，风险复核和有限修复符合内核规则。

## 验证与交付证据

固定岗位需求与有/无/过期背景场景比较实际建议，验证撤回与恢复；真实个性化效果在 41/42。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

### 2026-10-04：用户授权独立验收

code-review 规范轴与需求轴独立审查；初审发现的背景收据未核画像有效性、整句否定跨技能污染、个人复核门固定 PASS、全技能已有依据时漏组合需求、跨轮续接丢分支/时间、任务原话引用塞入私人摘要等缺陷已修复并补充回归。五条验收标准已逐项确认（复选框已勾）。最终能力版本 `career-job-sample-recipe-v3`，能力 parse-v4、plan-v2、collect-v2、filter-v3、analyze-v4、background-v2、gap-v2、advise-v3、verify-v3，替代下方实施记录中的早期版本号；无数据库迁移或画像写入口。冻结全量对账（211 项共享失败、1 项环境波动、0 项 main 独有）、烟测环境限制、main 基线与剩余限制见[独立验收记录](../acceptance/29-evidence-based-personal-career-gap.md)。合入推送与清理结果在完成后追加。

## 实施记录（2026-10-04）

分支 `codex/29-evidence-based-personal-career-gap`，工作树 `.worktrees/29-evidence-based-personal-career-gap`，基点 `809f05a5`（与本地 main 一致；网络代理 127.0.0.1:7890 不可达，未能 fetch）。开发与验证：Windows、conda `agent`、Python 3.11。

### 实现说明

- 配方扩展为 9 节点：`career.parse → plan → collect → filter → analyze → background → gap → advise → verify`；新能力版本 `career.load_background=career-background-v1`、`career.match_gap=career-gap-v1`、`career.advise_actions=career-advise-v2`；`career.analyze_jobs→career-analyze-v4`、`career.verify_delivery→career-verify-v2`；配方版本 `career-job-sample-recipe-v2`。
- 分支判定（`parsing.py`）：保守的个人准备意图正则；显式「只看／只要岗位、招聘、薪资等」优先覆盖为只岗位分支。每天可用时间只按「天/日」解析（数字、半、十以内中文数字），当前陈述优先于画像默认时段。
- 背景（`background.py`，新建）：仅个人分支经 `CareerBackgroundProvider` 读取；来源＝当前陈述与任务原话＋工单 19 采用切片（`snapshot_from_adopted_slice` 只带采用正文与来源，未采用正文不进入产物）；提供者缺失、使用开关关闭、切片失效、来源异常都降级为如实说明且不阻断公开岗位部分；`MAX_BACKGROUND_ITEMS=8`，整条采用不截断。
- 差距（`gap.py`，新建）：岗位要求原文 × 用户证据逐句对照，三分类；无依据一律 `to_confirm` 且备注含「不等于不足」；非待确认必须同时有岗位侧与用户侧可定位证据（防御性回退到待确认）；个人行动按「明确待提升 → 已有依据可转化 → 待确认先自测」排序；时间约束转成 pace 行动与 feasibility 说明并回显来源原话；兴趣只生成练习题材注记，不替换本轮岗位目标；目标含资料/项目时输出 `combination_requirements` 供工单 37；背景不足只问一个关键问题。
- 质量门（`kernel.py`）：新增必需门 `career.personal_evidence`（只岗位分支明确跳过；非待确认差距需 job_evidence＋background_evidence＋background_refs；待确认备注必须含「不等于不足」；直接证据类行动需 basis＋background_basis）与可选门 `career.personal_review`（如实记录本轮未执行独立模型复核）。`_build_projection`/`_run_verify` 扩展个人段；背景失败/跳过都有收据与证据边界文案。
- 接线（`api/main.py`）：新增 `CareerBackgroundLoader`，每次调用重读画像使用开关、按 `build_purpose(mode="companion", module_id="career")` 编译采用切片并核对撤回版本；任何失败降级。公开检索从不经过它，背景正文不进公网参数。
- 呈现（`presenting.py`）：个人分支新增「个人准备」段（背景来源、三分类差距、优先行动、组合需求、关键问题、边界），与卡片同一投影。
- 声明（`chat/task_materials.py`）：career 的 evidence_scope 更新为「仅个人规划分支经登记背景提供者读取允许的最小画像切片，公开检索从不携带画像或简历正文」。
- 无新增持久状态、无数据库迁移；职业模块没有任何画像写入口，岗位结果不写回。

### 接口/合同变化

- `CareerRequestAnalysis`：+`branch`、+`time_budget_minutes`。
- `CareerPlanProjection`：+`branch`、+`background`、+`gaps`、+`personal_advices`、+`combination_requirements`、+`follow_up_question`、+`personal_boundary`。
- `CareerAdviceItem`：+`priority`、+`background_basis`、+`feasibility`。
- 新增合同：`CareerBranch`、`CareerBackgroundSource/Item/Snapshot`、`CareerGapCategory/Item`、`CareerCombinationRequirement`。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 已按现有脚本重新生成（新增上述 schema/字段，无路由变化）。
- 能力版本升级使旧收据输入键变化：重跑即重算；公开样本节点（parse/plan/collect/filter/analyze）输入键不含背景产物，背景变化只影响 background→gap→advise→verify。

### 验证结果

- `python -m pytest tests/career_plan -q`：**115 passed**（含新增 `tests/career_plan/test_issue29_personal_career_gap.py` 15 例；原 `test_career_plan_kernel_acceptance.py` 的节点顺序/门集合已更新为 9 节点、4 必需门）。
- 相关回归：`tests/chat/test_improvement12_execution_guards.py`、`test_improvement15_task_materials.py`、`test_improvement15_module_acceptance.py` 共 **80 passed**；`tests/contracts/test_openapi_sync.py` 通过。
- 全量除挂起烟测（`python -m pytest tests -q -n 4 --ignore=tests/integration/test_runtime_smoke.py`）：本分支 **5096 passed / 214 failed / 39 skipped**；本地 main（`809f05a5`）**5083 passed / 212 failed / 39 skipped**。失败 ID 逐项对账：本分支独有 3 项中 `tests.contracts.test_openapi_sync` 已修复；其余 2 项（`tests.chat.test_issue02...disconnect_reconnect...`、`tests.chat.test_issue05...paper_route...budget`）与 main 独有的 `tests.chat.test_improvement13...bounded_cache...` 均为全量并发波动，隔离运行各连续通过。
- ruff：变更文件零新增诊断（`api/main.py` 剩余 E402 为该文件既有「导入后置」模式，main 基线同类）。mypy：`src/bridges/career_plan` 22 errors / 10 files 与 main 逐条一致；`src/bridges/api/main.py`＋`chat/task_materials.py` 97 errors 与 main 一致（仅行号位移）；无新增类型错误。
- 新增测试覆盖：待确认边界与两侧证据、只岗位不读背景（提供者零调用）、背景失败/失效仍交付岗位部分、时间约束优先级与 pace 行动、私人正文不进公开查询与链接、失效切片不复活旧结论、门规则（缺证据/弱化未知/无依据行动均阻塞）。

### 限制与说明

- 真实模型体验与真实招聘来源可得性不在本票，按评测票 41/42 验证。
- 个人分支的进入沿用主理解路由；正文若被判定为单一资料诉求会按既有规则走 resources 模块，显式「只看招聘信息」在本模块解析层覆盖为只岗位分支。
- 「实习」单独出现不再算作已有依据（避免求职原话误判），证据判定只认明确的掌握/经历表述。
- 验收标准 1–5 均有对应确定性测试；复选框留待独立验收确认。



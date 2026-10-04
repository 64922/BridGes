# 工单 37 独立验收记录

日期：2026-10-04。结论：**通过**（独立双轴两轮审查 + 阻断点终验后达成）。

审查基点固定为 `main@48d12a454bb3f759d8a1ffcdb9a5b5732073501a`；原交付为
`8c1785a2381570211aa842563c2d2522badbb47a`，验收修复为
`8c59d63155054a53450e2cbdd48cf3843f7a28c7`、`94d296dd`，本轮修复见「本轮修复」。
审查命令为 `git diff 48d12a454bb3f759d8a1ffcdb9a5b5732073501a`。
依据工单 37、AGENTS.md、CONTEXT.md、本批 README 和工作流编排/日常流程/验收合同。
编码代理报告仅作线索，以下结论来自实际代码、独立双轴审查和本次执行。

## 逐项验收

| 工单验收项 | 独立证据与结论 |
| --- | --- |
| 论文与资料并行、共享总预算、身份不清不宣称对应实现 | 达成。引擎 `Barrier(2)` 真并行 + 同一 09 账本；论文身份接缝 `graph.py::_selected_paper_identity` 要求 trust=qualified、字段完整、唯一或“第N篇”；未确认时 `identity_confirmed=False` 且带限定。真实公网并发峰值未测（见限制）。 |
| Java 后端目标组合岗位/背景/资料/项目，私人简历不发公网 | 达成。真实 `MainAgentUnderstanding` 的自然语句（检测顺序 `[github, resources, career]`）经计划拓扑排序后合法执行；私人 `background` 绑定为 PRIVATE/leaves_device=False；resources/github 只消费 career 已核验公开 `combination_requirements`；私人投影/正文/证据不进入父图检查点。 |
| 换杭州只重算相关依赖，公共材料复用 | 达成。条件键差异 + 参数指纹驱动失效/复用（`test_city_change_reruns_career_and_reuses_resources`、引擎同款）；修订轮无能力信号且条件变化（city/year_range）时从上一轮综合产物恢复计划重入复合（`test_revision_without_module_signal_restores_prior_plan`，独立探针 19/19 边界）。含模块词的修订仍按票 12 走单模块路径（见限制）。 |
| 支持不足的关键结论被门阻止，普通交流/工具不强制裁判 | 达成。生产 DONE 不再等同 qualified；结论从真实产物链重建并经 `EvidenceVerifier` 风险核验；伪造结论 BLOCK、篡改哈希不进入综合、未合格投影不提交；最终门失败经一轮受控修复后仍不过则整轮错误。 |
| 可选失败保留有效部分，必要失败阻塞依赖 | 达成。必要（含未核验 draft）上游阻塞依赖，独立分支失败保留有效部分；综合只收录合格结论并说明未完成项；综合产物 trust 按步骤计算（不再恒 QUALIFIED）。 |
| 循环/未知能力/硬条件/第二轮调整/旧版本迟到均拒绝 | 达成。计划校验拒绝码全覆盖（含依赖拓扑回退交校验、第二轮调整由 planner+账本双重拒绝）；租约转移、任务版本变化、消息已有终态均拒绝写入。 |

没有将前置票状态字段或历史通过数作为本票验收依据；未把未测的真实模型/外部可得性说成已通过。

## 第一轮双轴（判不通过，历史）

原始 Standards：3 项（P1 运行完成直接 trusted；P2 文案绕过注册表；可能重复分支 smell）。
原始 Spec：5 项实质缺口（生产质量门空转、跨轮复用未接入、岗位依赖未消费、选定论文身份无纵向路径、综合与调整不完整）。
修复与复验过渡版本为 `8c59d631`/`94d296dd`，其中 P1 缺口部分保留（见 `final.xml` 196 passed 与 3 个验收探针 failed）。

## 第二轮双轴（判不通过）+ 本轮修复

第二轮独立审查确认：P0 真实理解顺序导致 career 系组合必然被拒（Java 验收不可达）、career draft 需求仍被消费、
终态 EVIDENCE_BOUND 被升格 qualified、未合格投影/综合产物恒标 QUALIFIED、私人投影仍随检查点持久化、
裸条件修订轮不可达复合。本轮修复：

1. **计划拓扑排序**（`planner.py::_dependency_order`）：理解层能力顺序不再构成拒绝理由，缺失/循环依赖仍结构化拒绝。
2. **以核验状态驱动依赖**（`executor.py::_usable`）：调度完成且 qualified 才能被下游消费；draft/失败上游阻塞或跳过；
   `restore` 拒绝非 qualified 记录；`getattr(result,"trust_state","draft")` 失败关闭。
3. **终态可信白名单**（`evidence.py::_DELIVERABLE_TERMINAL_TRUST`）：paper 允许 EVIDENCE_BOUND（其成功态），
   resources/career/github 必须 QUALIFIED；组合层不再升格模块自身的证据状态。
4. **未合格不落交付**（`production.py::projection_updates` 跳过未合格 completed；`persist_synthesis_artifact` trust 按步骤计算）。
5. **私人投影退出检查点**（`graph.py::_invoke_composite_plan` 清理 evidence/summary/投影/正文；
   `production.py::hydrate_career_projection` 提交时从 `career.verify` 产物回填；专项回归证明 state 无私文、消息交付完整）。
6. **修订轮恢复复合计划**（`graph.py` 无能力信号 + `REVISE` + 上轮综合 + city/year_range 变化 →
   `production.py::previous_composite_modules` 重建模块列表重入复合，未登记组合/无条件变化/无上轮产物均不触发）。
7. **审计闭合**：步骤调整与门修复两条路径调用 `end_adjustment`，异常路径也在 finally 前补记；github topic 参数来源声明与运行一致；
   移除死代码；修复 mypy 新增项（graph.py 与 main 同参数同为既有 8 条告警）。

## 终验（阻断点独立复验）

第三位独立验证员对两个阻断点独立复造探针并主动证伪：

- 私人投影检查点：A/B/C 组 15/15；修复 D1（未核验 career 的 `summary` 回落正文）后 15/15；递归扫描 state 全字段，
  resume/background 原文不出现在 `composite_outcome`/`composite_draft`/`composite_gate`；消息 `career_plan` 完整回填，检查点重启路径可回填。
- 修订轮重入：19/19（无条件变化不触发、未登记组合不触发、capability_list 非空不劫持、无上轮产物不触发、
  仅 source_restriction 变化不触发、year_range 触发、真实理解「城市换成杭州」端到端触达）。
- 残留通道（已记录，非简历原文）：澄清/失败轮的模块文案必须送达用户，随检查点保留到终态提交；探针确认其中不含用户简历/背景正文。

## 实际验证

环境：Windows，`C:\Users\33755\anaconda3\envs\agent\python.exe`，Python 3.11.15，`$env:PYTHONPATH='src'`。

| 执行 | 结果与证据文件 |
| --- | --- |
| 最终相关集合：orchestration、37 三个聊天文件、12 hybrid/guards、golden routes、state_copy | **228 passed**，`final2.xml` |
| 模块域回归：github、resources、paper、career | **446 passed / 1 failed**，`broad2.xml` 含全集合：**673 passed / 1 failed** |
| 上述唯一失败（main 同环境同样失败） | `tests/github/test_github_module_flow.py::test_plain_chat_without_the_module_never_starts_github`；在 `D:\BridGes` main 工作树单跑同样失败 → 既有问题，非本票引入 |
| 生产验收探针（独立执行，不混入通用回归） | **4 passed**，`acceptance-probes-pass.xml`（DONE→qualified、父图 prior_results、选定身份、真实理解顺序） |
| Ruff：本票改动文件 | 通过；`graph.py` 两条 N818 与 main 输出相同，属既有命名告警 |
| Mypy：orchestration（follow-imports=silent） | 8 源文件无错误；graph.py 与 main 同参数均为既有 8 条（2 union-attr + 6 add_node），无新增 |
| `git diff --check` | 通过（含修复 `.scratch` EOF 空行后） |

失败归属：唯一失败在两个工作树同现，不能归入本票；未重新跑全仓，因此未独立确认历史全量失败清单。
禁止把不同范围的通过数相减当作精确新增覆盖数或全量非劣证明。

记录的未验证/限制：

- 真实模型体验、外部来源（arXiv/GitHub/OpenLibrary/招聘源）可得性与真实公网载荷内容未测（按评测票）。
- 含模块词的修订轮（“换成杭州，继续看 Java 岗位”）按票 12 单模块路由；本票仅在无能力信号的条件修订轮恢复复合计划。
- 澄清/失败轮必须送达用户的模块文案随检查点保留到终态提交（已探针确认不含简历/背景原文）。
- 生产计划步骤恒 `required=True`，可选步骤语义由引擎契约支持、未在生产组合启用；`remaining_budget_ms` 透传是名义的（共享账本用于并行宽度与调整轮次）。
- 来源限制的否定语义沿用 planner 既有行为，未在本票扩张。

## 合并、推送与清理（验收后执行）

- 验收通过后提交本轮修复、验证记录与工单更新；合并最新 `main`（含工单 41 合并）并复核合并后关键集合。
- 经代理推送，核对本地与远端 `main` SHA 一致（不在输出中暴露凭据）。
- 仅清理本票工作树与本地分支；保留 34/41 及其他任务内容与主工作树历史验证文件；`worktree prune` 仅清理失效记录。

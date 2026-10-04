# 工单 33 独立验收

日期：2026-10-04。原交付 `a28027bb7a0f20e622676994ea4b972a5fc97051`，固定基线 `main@809f05a5358568c09f3f656f54357fe971fe66b2`（审查基点，远端当时同基点）。独立验收起点 HEAD 为原交付提交、修复全部未提交。

结论：原交付存在规范与需求缺陷，已独立两轴审查、修复并复验；最终全量相对 main 零新增失败。工单转 ready-for-human。依据为实际工单、AGENTS.md、CONTEXT.md、批次 README、学习工作流、讨论、公共编排、交付验证与人味化实施方案；编码代理报告只用于定位，不充当验证证据。前置票 12 的代码评审记录与 31 的独立验收/合入记录已核对。未把合成网关响应当成真实模型教学质量证据。

## Standards

code-review 规范轴由独立只读子代理审查原交付，初审 3 项硬违反、0 项额外气味建议，全部修复。

1. 核验失败把未来题干与模型 `detail` 放进公开错误消息，可能经 API/SSE 泄露答案。修复：`ReviewPlanError.message` 改为固定安全中文文案，模型原始反馈存入 `repair_detail`，只供唯一一次内部修复提示使用；`exception.message` 上抛到 API/SSE 的路径均为安全文案。验收测试 `test_failed_verification_hides_private_details_in_api_and_replay` 用答案金丝雀与未呈现题干金丝雀断言 API 响应、错误信封、SSE 重放均不包含，且第二次计划提示仍收到原始反馈（证明私有与修复两条通路分离）。
2. `REVIEW_CAPABILITY_VERSIONS` 原为未被使用的常量，计算没有实际登记分派，合格计划没有持久产物/完成收据。修复：`_call` 拒绝未登记任务；新增 `src/bridges/study/review_kernel.py`，用真实 `RecipeRegistry`/`NodeKernel`/质量门 `_plan_gate`/`RunCommitGuard` 执行 `study.plan_review` 节点，合格计划写入私有产物（含 `calculations`、协议版本、能力版本）与完成收据；计算按实际登记版本分派并把调用结果随产物保存；事件只发节点状态。`test_plan_receipt_records_registered_calculation_without_replay_leak` 断言产物中的计算登记恰好一条、SSE 重放不含 `expression`/`core_points`。
3. `STUDY_GRAPH_VERSION` 未变，旧已完成检查点可能直接恢复未经新核验的 reviewed 输出并抬升为 v3。修复：图版本改为 `study-review-v3`；`chat/service.py` 把 `study-scope-v2` 也路由到 `StudyWorkflow`，由既有版本门在原子提交前以 `study_graph_version_changed` 结束并保留原书页与历史，显式重试进入新图。`test_old_graph_run_is_rejected_then_explicit_retry_uses_new_verification` 断言失败后状态不变、无计划/核验调用、重试后才走新核验。

## Spec

需求轴由另一独立子代理核对工单与代码，原交付 4 项加复验再发现的绑定缺口，全部修复，无剩余阻断项。

1. 模型省略 `calculation` 时数值题仍可通过。修复：每条核验必须声明 `requires_calculation`（合同必填），明显数值题（含“计算/求值/运算”加数字、或题干算术式）即使模型省略或声明 false 也必须有登记工具复算，否则按 `study_review_calculation` 拒绝并触发唯一一次修复。覆盖减法与中文答案场景（`9-3`、`9 减 3` 且答案“六”）。
2. 独立内容核验只收到知识点哈希 ID，缺少 ID 对应的标题、类型与依据，无法判断题目是否真的考该知识。修复：核验载荷加入被引用 `scope.units` 的完整语义（title/kind/fragment_ids/core 等）；验收测试构造同片段但标题不同的知识点，核验侧据标题区分并拒绝不匹配者。
3. `conditions` 不要求明确“题设”标签，也未向用户展示。修复：自设条件必须以“题设：”开头、题干不得声称教材原例；首次出题与当前题复述都展示 `conditions`，`public_view` 保留题目条件、仍隐藏评分依据与标准答案。
4. 非实数幂、溢出、零的负幂抛非 `ValueError`，绕过有限修复。修复：登记计算工具把 `ArithmeticError`/`TypeError`/`RecursionError`（含 `OverflowError`、`ZeroDivisionError`、复数幂的 `float()` 转换）统一为 `ValueError`；`(-1)**0.5`、`1e308**2`、`0**-1` 均有反例。
5. 复验追加发现的绑定缺口：`expected` 未绑定冻结答案、减法/中文答案漏检、计划实际来源与收据指纹范围不一致。修复：`calculation.expected` 必须与冻结 `canonical_answer` 中唯一阿拉伯数值硬比较（无法唯一绑定则拒绝/修复），复算表达式必须含真实算术 AST 操作（答案常量不算复算），来源限定 `scope.fragment_ids` 并计入私有产物输入指纹；`plan_review` 增加合法阶段守卫。

## 主审补充修复

- TypeScript 合同接缝：交付再生成的 `packages/contracts/src/generated.ts` 使新字段在 TS 中必填，`apps/web` 的 `StudyProgress.test.tsx` 夹具因此新增 5 项类型错误。已按既有夹具风格补齐 `scope_version_id/protocol_version/incomplete_basis/incorrect_basis/conditions/legacy`，`tsc` 结果与 main 基线为 20 = 20、逐源码位置一一对应（余下均为基线既有 `state_version` 夹具错误，文本差异仅来自合同枚举顺序与新增夹具字段）。
- 计算证据替身：原温度概念题改成真实数值题（`2+2`、`y=2`），避免无关算式充当计算证据；该替身收紧后暴露的原测试错误期待已同步修正。

## 逐项验收

| 票面验收项 | 实际代码与测试证据 | 结果 |
| --- | --- | --- |
| 开始意图否定/引用不进入复盘，只有合法阶段和有效范围才计划 | `review_intent` 只匹配明确开始/暂停/辅导；`plan_review` 增加阶段守卫并复用 `scope.verified`/知识点稳定 ID 门；原票意图与范围用例全部保留通过 | 通过 |
| 核心知识覆盖、评分依据和标准答案经内容门，不仅 ID/每页引用门 | 结构覆盖门 + 独立 `study.verify_questions` 内容门；核验载荷含被引用 units 语义；`_plan_gate` 二次校验后才提交私有产物与收据；同片段异知识点反例 | 通过 |
| 等价表述/推导有预先规则，评分要点作答前冻结 | `equivalents`/`core_points` 作答前冻结；新合同判定忽略模型返回的标准答案；未判定题 `public_view` 清空评分要点与答案；旧题保留原判定 | 通过 |
| API/重放不泄露未来题和提前答案，呈现事务和重复读取幂等 | 失败核验金丝雀在 API/SSE 重放均不出现；未来题与评分要点不外发；计划节点产物在终态失败后按输入键复用（计划/核验各只调一次）；生命周期导出/备份/恢复/删除/隔离 | 通过 |
| 核验失败不发不合格题、不推进阶段，旧题历史兼容可读 | 失败一次有界修复、持续失败按错误码终止且阶段不变；旧图版本安全结束、显式重试进新图；旧评分合同题保留历史判定与标准答案，不触发计划/核验 | 通过 |

## 实际验证

全部使用 conda `agent`（Python 3.11.15）、`PYTHONUTF8=1`；因 editable 安装 `_editable_impl_bridges.pth` 指向已不存在的旧 Desktop 路径，子进程类用例需显式 `PYTHONPATH=<工作树>/src`。定向复跑用独立 basetemp、`-p no:cacheprovider`、`--junitxml`。

- 定向组合（本票验收、原 9 项、v2_17/18/19/20、31 范围、本票生命周期、31 生命周期、contracts、kernel）：**122 passed**（`33-independent-final2-focused.xml/.txt`），含最后修复后的原失败用例 `test_self_set_conditions_must_be_labelled`。
- 最终全量（`33-independent-final3b-full.xml/.txt`）：**5369 collected / 199 failed / 5170 passed / 37 skipped / 2 deselected**；干净 main 基线同环境（`33-independent-main-full.xml/.txt`）：**5346 / 202 failed / 5144 passed / 39 skipped**。对照（`33-independent-final3b-comparison.json`）：本票独有失败 **0**；main 独有 3 项——2 项为下一段所述定向排除的基线失败、1 项 `test_issue01_chat_profile_correction` 在 main 失败而在本票通过。跳过差异 2 项：two `tests.runtime.test_runtime_contract` 全流程用例在 main 因前端不可用被跳过，在本票工作树实际运行并通过。历史全量（`33-independent-full.xml`，8:55 启动）因启动时最后代码/测试修复尚未完成，仅作线索；`33-independent-final2-full` 因缺 PYTHONPATH 产生 21 项环境失败；`33-independent-final3-full` 因下述基线用例在前端可构建时阻塞被终止，均不作为最终证据。
- 环境归因：`33-independent-cli-check.xml` 证明带 `PYTHONPATH=<本票 src>` 后 CLI/运行时契约 17 passed / 2 skipped。基线中 `test_start_fails_with_empty_global_key_before_spawning`、`test_start_fails_when_global_key_env_absent` 在 main 即失败（`start` 先走 Web 构建，npm 失败而提前退出）；在本票工作树前端可构建时 `start` 实际起服并阻塞，故对最终全量做 `--deselect` 定向排除并在逐项对照中保留为 main 独有失败。该行为与本票代码无关（交付未触碰 CLI/运行时代码），`review33_start_repro.py` 受控复现证明开发环境缺失 Key 文件时 `start` 仍以 rc=1 正确失败。
- Ruff：改动与新增 Python 文件 `All checks passed`（`33-independent-ruff-final.txt`）。
- Mypy：本票源码（含 `review_kernel.py`）与 main 对照同为 **5 项，全部位于既有 `src/bridges/chat/service.py` 相同行**，逐项一致（`33-independent-mypy.txt`、`33-independent-main-mypy.txt`）。
- TypeScript：本票与固定 main 前端快照同依赖运行 `tsc --noEmit`，**20 = 20** 逐位置对应，无新错误类型（`33-independent-typecheck-final.txt`、`33-independent-main-typecheck.txt`）。
- 浏览器：既有复盘 Playwright 用例 3 passed（1280×720/1440×900/1920×1080，API 替身，`33-independent-browser.json/.txt`）；仅证 UI，后端私有边界另有 API/SSE/事务级测试。
- 生命周期：新评分与旧评分题干/评分依据在导出、口令备份恢复、账户删除与账户隔离中保持正确（`tests/lifecycle/test_issue33_review_lifecycle.py`）。

## 剩余限制

- 计算目前只支持可明确绑定的单一阿拉伯数值答案与安全四则/幂；多值、符号或无法唯一绑定的答案会阻塞并进入一次修复，不宣称任意公式/算式正确性。
- 公式与题目语义关联仍依赖独立内容核验模型；合成网关只证明机制，真实模型出题/核验/判定质量与 OCR/视觉体验由评测票 42 验证。
- 未调用真实模型、未做真实 OCR；未注入进程级崩溃恢复。
- 最终全量对 2 项 main 既有失败用例做定向排除（原因见环境归因）；这不是“全部通过”，但本票代码零新增失败。

## 合并、推送与清理

（执行后补记）

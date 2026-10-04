# 工单 32 独立验收、修复与合入记录

日期：2026-10-04。原交付 `0b37e571d02e6e8065cd68bc01a6b3aafe843ab3`，验收固定基线 `main=809f05a5358568c09f3f656f54357fe971fe66b2`。分支 `codex/32-study-question-level-evidence-and-tutoring`，工作树 `D:\BridGes\.worktrees\32-study-question-level-evidence-and-tutoring`。用户已授权修复、合并、经网络代理推送与清理本票工作树/分支；其他任务工作树与主工作区未跟踪文件保留。

编码代理报告只作定位线索；以下来自独立代码审查、反例与当前测试。

## 审查来源与前置

- 使用 `code-review` 技能，以 `git diff 809f05a5...0b37e571` 固定范围，分别由两个只读子代理审查规范轴与需求轴；原发现修复后由子代理复审，复审发现的停止后写投影与恢复复用两个问题由主审补修并回归。
- 读取工单、AGENTS/CONTEXT、批次 README、`study-workflow.md`、`discussion.md`、`orchestration.md`、`delivery-and-validation.md`、人味化实施方案、上下文工程改进方案。
- 前置 15/21/31 的实际实现与独立验收记录已在基线核对，不以状态字段代替实施证据；本票跨票接缝（31/30 提供书页、33 消费书页范围、22 接入偏好）保持不变。

## Standards：规范符合性

初审 3 项硬违反（无额外重构型 smell），修复后复审无剩余阻断项。

1. **补证顺序违反 `study-workflow.md` §5 与任务 2**：原 `gather_evidence` 在首次评估后同时固定 KB/web 触发并连续执行两层，最后才复查，导致 KB 已补足仍可能联网、只有 KB 缺口且 KB 无命中/失败时可能不继续公网。现改为 KB→复查→按剩余缺口决定公网→再复查；初始 web 类缺口也先尝试已启用知识库；KB 空命中后的真实剩余缺口自动进入公网；公网查询使用最新剩余缺口。
2. **伪造支持点仍晋升硬约束**：原 `_normalize_assessment_gaps` 把伪造 `source_id` 降为 gap，但 `_finalize` 仍把首轮 supported 全部写入 supported_points，生成提示同时出现「已有支持」与「未核实」。现最终使用最近评估；supported_points 仅保留具有实际当前采用来源 ID 的条目；复查只接受本次编译采用的 ID，不再与首轮并集；空评估、只有 key_points 没有裁决不能 `sufficient=True`；无 gaps 但存在未裁决关键点时保守保留缺口。
3. **正文仅引用 ID 校验，无科学质量门**：原 `tutoring.py` 只校验 ID/种类/裸链接，合法引用仍可携带错公式、丢条件、缺口确定结论，model 段可新增无来源事实。现新增 `study.verify_tutoring` 结构化核验（`max_tokens=640`），逐段检查 supported、formulas_valid、conditions_preserved、gaps_respected；核验覆盖全部段落，缺段/重复段/调用失败阻塞产物；未通过段落删除、已支持部分仍可交付；全部段落被拒绝时不保存辅导产物；候选正文与 assessment 作为 ContextEvidence 数据块而非系统指令；核验上下文必须实际容纳候选及全部引用，否则阻塞；复用 `compile_turn_context` 与预算/停止守卫。

## Spec：需求符合性

初审 3 项（与规范轴部分重叠），复审补充 1 项，全部修复，无剩余范围内阻断。

1. **KB 冲突被正常命中分支吞掉**：原 `_knowledge_base_sources` 先 `if sources` 后 `elif sufficiency==CONFLICT`，含引用的冲突被归 used。现优先检查 CONFLICT；冲突引用不作为普通可采用来源；保留标签/片段/说明并进入 `assessment.conflicts` 与正文说明；冲突说明不再固定写成「联网冲突」。
2. **公网未完整接入外呼预算/截止**：现新外呼 `register_external_call`；预算不允许时不调用；传递 `provider_deadline` 与 `stage_deadline`；`finally release_external_call`；已固化投影只读复用不登记新外呼；异常说明不直接渲染原始内部异常正文。
3. **停止后的迟到公网结果仍可写投影**：现 `web.search` 返回后、`repo.update_message_web_search` 前再查 `stop_event`；停止后返回未保存说明，不写消息投影、不采用来源。
4. **耗尽自动调整额度会拒绝只读复用**（复审发现）：旧顺序先 `begin_adjustment` 再读 persisted projection。现 KB 使用 `round_projection` 先查已完成轮次，`_knowledge_base_sources` 增加 persisted 参数只读复用且引用仍实时 `citation_detail` 重验授权；公网先读取 `persisted_web_projection`；有已固化产物时不再次扣自动调整轮次；缓存缺失时仍需新补证预算，不放宽权限。

其他修复：

- 普通外部未核实缺口不再一概要求补拍；明确书页缺口仍请求补拍。
- 新 evidence 模块独立导入的循环 import 通过把 chat 相关导入推迟到使用函数、TYPE_CHECKING 保留类型注解修复，未更改 `chat/__init__.py`。
- 旧「关闭 KB」测试明确加入「不要联网」，避免把关闭 KB 误当成禁止公网；按 layer 查找 skipped KB attempt。
- 证据协议升级 `study-tutor-evidence-v2`；`STUDY_GRAPH_VERSION` 升级 `study-tutoring-v3`；`StudyEvidenceAssessment` 默认协议同步 v2；`openapi.json` 已重生成。
- 学习状态仍是兼容的可选 assessment 投影，没有新增数据库表或状态版本迁移；图版本升级用于避免旧未完成运行盲用新流程，历史领域状态保持可读。

## 逐项验收

| 票面验收项 | 实际代码与测试证据 | 结果 |
| --- | --- | --- |
| 书页足够时无额外联网，知识库/公网不足按真实缺口触发 | `gather_evidence` KB→复查→公网顺序；`test_sufficient_pages_do_not_search_knowledge_base_or_web`、`test_knowledge_base_resolves_gap_before_network`、`test_remaining_gap_automatically_reaches_web`、`test_empty_knowledge_base_falls_back_to_web`、`test_initial_web_gap_checks_enabled_knowledge_base_first` | 通过 |
| 用户来源限制不绕过，查询只最小公开参数 | 权限硬门 + `_web_gap_query` 仅用缺口公开术语并截断；`test_disabled_knowledge_base_and_no_network_keep_gaps_unverified`、`test_public_call_obeys_budget_and_releases_slot_after_failure[False/True]`、`test_explicit_network_request_survives_exhausted_automatic_budget` | 通过 |
| 补充失败交付已支持部分，不伪造来源或整体隐藏有效结果 | supported 仅当前采用 ID；复查失败保留缺口并允许公网；web 失败保留书页交付；`test_reassessment_failure_keeps_gap_and_uses_web`、`test_fabricated_support_is_not_promoted_to_generation_constraint`、`test_unjudged_key_point_never_counts_as_sufficient`、`test_rejected_segment_not_saved_but_supported_part_delivered` | 通过 |
| 教材/补充/推导来源和关键条件明确，冲突保留 | KB/公网冲突分列不合并；`verify_tutoring` 四门；`test_knowledge_base_conflict_is_not_an_adoptable_source[False/True]`、`test_conflicting_public_sources_are_listed_not_merged`、`test_rejected_formula_does_not_commit_tutoring_exchange`、`test_incomplete_failed_or_all_rejected_checks_block_save` | 通过 |
| 辅导不自动复盘或扩考查范围，合法学习生成事实/流式回归通过 | `test_page_only_delivery_without_review_or_scope_expansion`；图版本升级旧执行图安全终止/显式重试；学习链路 181 passed 与最终完整相关回归 | 通过 |

## 实际验证

全部使用 conda `agent`（Python 3.11.15，`PYTHONUTF8=1`），仓库 pythonpath 指向本工作树 src；每次使用独立 `--basetemp`，避免与其他进程的 Windows 文件锁冲突。确定性网关替身只证明机制，不作为真实模型质量证据。

- 原交付独立复验聚焦组合 **154 passed**（[initial-focused.xml](../validation/32-review/initial-focused.xml)）。
- 修复后学习链路综合复验 **181 passed**（[final-focused-isolated.xml](../validation/32-review/final-focused-isolated.xml)）。
- 停止/质量门/证据/契约聚焦 **38 passed**（[final-gates.xml](../validation/32-review/final-gates.xml)）。
- 恢复复用+独立反例 **17 passed**（[recovery-gates.xml](../validation/32-review/recovery-gates.xml)）。
- 最终完整相关回归 Issue 侧（`tests/chat tests/retrieval tests/knowledge_base tests/web_search tests/learning tests/contracts`，`-n 4`）：**1518 passed / 52 failed / 2 skipped**（[final-confirmed.xml](../validation/32-review/final-confirmed.xml)）。
- main 基线同组合：**1482 passed / 52 failed / 2 skipped**（[baseline/main-isolated.xml](../validation/32-review/baseline/main-isolated.xml)）；失败集合按完整测试 ID 逐项比较为 52=52、0 新增、0 消失（[failure-comparison.json](../validation/32-review/failure-comparison.json)，对照脚本 [compare_results.py](../validation/32-review/compare_results.py)）。
- mypy 同范围：Issue 与 main 均 **22 errors in 10 files**；去除行号并按「文件+消息」去重后 14=14、0 新增（[mypy-issue.txt](../validation/32-review/mypy-issue.txt)、[baseline/mypy-main.txt](../validation/32-review/baseline/mypy-main.txt)）。
- 本票全部改动 Python 文件 Ruff `All checks passed!`；`git diff --check` 无错误，仅 Git 的 LF→CRLF 提示。
- OpenAPI 以 `PYTHONPATH=src scripts/regenerate_openapi.py` 重生成；契约测试无新增失败。
- 52 项共同失败均为基线既有（退役 project/attachment、退役 career 路径、退役 MCP/plugin/selection、旧知识库附件路径等），完整名单见 failure-comparison.json；本票未修复这些既有失败。
- 验收过程中曾出现中间版 Issue 侧 55 项失败（3 项为本票测试与契约漂移引发），均已在最终代码修复闭合；最终结果为 52=52，不得用中间结果替代。

复跑命令（仓库根目录）：

```powershell
$env:PYTHONUTF8='1'
conda run -n agent python -m pytest tests/chat/test_improvement32_evidence_review.py tests/chat/test_improvement32_tutoring_gate.py tests/chat/test_improvement32_study_tutoring_evidence.py tests/contracts -q --basetemp=.tmp/32-core
conda run -n agent python -m pytest tests/chat tests/retrieval tests/knowledge_base tests/web_search tests/learning tests/contracts -n 4 -q --tb=line --basetemp=.tmp/32-full --junitxml=.scratch/2/validation/32-review/rerun.xml
python .scratch/2/validation/32-review/compare_results.py final-confirmed.xml
```

## 剩余限制

- 确定性网关只证明机制与保存门，不证明真实模型的充分性判断、科学核验准确率与辅导体验；真实模型与外部可得性由评测票 42 验证。
- 来源 ID 真实不等于语义支持；本次补充了独立正文核验，但模型核验仍不保证绝对正确。
- 共享 LocalQueryPlanner 可移除明确标记的私人语句、邮箱、凭据等；对未标记私人内容的完整识别能力未证明。
- KB 纯关键词 trigram 对不同措辞仍可能无命中；空命中不得宣称为已充分。
- 恢复复用已固化的外部产物，但证据复查/正文生成/正文核验仍可能重做；不是零模型调用恢复。
- `packages/contracts/src/generated.ts` 未更新（原代理与本次均未改）；现契约测试只要求该文件存在、`openapi.json` 同步，前端未消费 assessment 的兼容边界成立。
- 学习 assessment 为可选投影，无新表、无状态版本升级；图版本已升级，旧执行图安全终止/显式重试，历史领域状态保持可读。
- main 完整相关回归仍有 52 项既有失败，mypy 仍有 22 项既有错误；不宣称全仓库全绿。

## 合并、推送与清理

（待合并后补记）

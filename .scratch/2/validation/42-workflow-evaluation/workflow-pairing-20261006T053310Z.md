# 工单 42 旧/新树真实模型配对报告

- 生成时间：2026-10-06T05:39:22.912289+00:00
- 任务数据摘要：`6072356363b4cdf840cb403840ca8f26fa1ac6b7e141d07b15c2fd7d2e143fd1`
- 旧树：`D:\BridGes\.worktrees\42-baseline-7818c34` @ `7818c34b1cc425081b54fd65cedf87d61c80fefb`
- 新树：`D:\BridGes\.worktrees\42-workflow-evaluation-and-external-capability-gates` @ `1c6f9edda00165de1b0bb19e02912037e65e5b54`
- 生效模型：['qwen3.7-plus-2026-05-26']

## 质量检查点（同任务数据，两侧同一模型配置）

| 场景 | 检查点 | 旧树通过 | 新树通过 |
| --- | --- | --- | --- |
| A01 | answer_nonempty | ✓ ✓ | ✓ ✓ |
| A01 | single_chat_call | ✓ ✓ | ✓ ✓ |
| A01 | no_external_model_capability | ✓ ✓ | ✓ ✓ |
| A01 | no_task_created | n/a n/a | ✓ ✓ |
| A01 | no_unnecessary_search | ✗ ✗ | ✗ ✗ |
| A02 | answer_nonempty | ✓ ✓ | ✓ ✓ |
| A02 | no_paper_results | ✗ ✗ | ✓ ✓ |
| A03 | answer_nonempty | ✓ ✓ | ✓ ✗ |
| A03 | asks_one_clarification | ✗ ✗ | ✗ ✗ |
| A03 | no_paper_results | ✓ ✓ | ✓ ✓ |
| A11 | no_paper_results | ✓ ✓ | ✓ ✓ |
| A11 | hard_condition_blocked | ✗ ✗ | ✓ ✓ |
| R07 | answer_nonempty | ✓ ✓ | ✗ ✗ |
| R07 | stop_no_auto_continue | ✓ ✓ | ✓ ✓ |
| R07 | continue_creates_new_run | ✓ ✓ | ✓ ✓ |

## 汇总（质量 / 成本 / 延迟）

| 侧 | 检查点通过率 | 模型调用 | prompt tokens | completion tokens | 整答 P50/P95 (ms) | 首字 P50/P95 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 旧树 | 20/28 | 10 | 4517 | 11446 | 15671/44421 | 17658/40519 |
| 新树 | 23/30 | 6 | 6896 | 4967 | 6905/18703 | 13612/15803 |

## 与 09 预算初值对照（新树）

| 预算类别 | 总预算 ms | 预留 ms | 实测整答 P95 ms | 结论 |
| --- | --- | --- | --- | --- |
| lightweight | 120000 | 0 | 13921 | 保留（余量 106079 ms） |
| normal | 60000 | 15000 | 18703 | 保留（余量 26297 ms） |
| deep | 120000 | 30000 | None | 无样本 |

## 执行错误码

| 侧 | 错误码计数 |
| --- | --- |
| 旧树 | 无 |
| 新树 | network_not_allowed×2、output_budget_exceeded×3、web_search_citation_invalid×2 |

## 配对等价与问题

- 两侧任务数据、模型与重复次数一致，无执行错误。

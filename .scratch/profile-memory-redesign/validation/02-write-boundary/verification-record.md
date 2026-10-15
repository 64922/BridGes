# Issue 02 验证记录：真实检查结果

验证版本：代码提交 `5d2865b4`（分支 `codex/issue-02-memory-write-boundary`，
基点 `cfc224c4`）＋ 本提交附带的证据与记录。环境：conda `agent`
（Python 3.11.15，pytest 9.1.1），Windows；截图证据见 `evidence/`。

## 1. 重构前后可见行为对比（用户演示项）

用 01 的确定性旅程运行器对同一语料跑 8 条旅程（`--mode deterministic`）：

```
python scripts/run_issue01_profile_baseline.py --mode deterministic --output <dir>
```

| 证据 | 运行 | 生成时间 | 代码提交 |
| --- | --- | --- | --- |
| `evidence/baseline-stub-20261015T063956Z.*` | main 第 1 次 | 06:39:56Z | `cfc224c4`（clean） |
| `evidence/baseline-stub-20261015T064148Z.*` | main 第 2 次（对照） | 06:41:48Z | `cfc224c4`（clean） |
| `evidence/baseline-stub-20261015T073527Z.*` | 分支（本票） | 07:35:27Z | `5d2865b4`（clean） |

比较方法与结果（工具见 `tools/`，输出见 `evidence/comparison-*.txt`）：

- `compare_profile_baseline.py`：归一化只剔除运行间必然不同的字段（随机
  标识、时间戳、耗时、同刻写入的行序），其余逐字段比较。
  - main-r1 vs 分支：`normalized equal: True`
  - main-r2 vs 分支：`normalized equal: True`
- `diff_profile_baseline_fields.py`（字段级定位，容忍归一化口径本身）：
  - main-r1 vs main-r2：`no field-level differences`
    ——证明比较器口径可靠（同版本两次运行也判相等）。
  - main-r1 vs 分支：`no field-level differences`

归一化保留并逐字段一致的字段包括：每轮用户消息、助手回复正文、四维记录数
与内容、原子条目数与正文/状态/补位关系（superseded 链）、提取运行状态与
outcome、评估 checks 与 `observed_status`（8 条旅程的通过/部分/已知失败
结论）、邻居读取探测结果、模型调用计数（两侧均 10 次，token 0 为替身
模式说明）。差异仅剩随机标识与时间戳——即「相同输入得到相同可见行为」，
且恢复检查不再由构造触发不影响任何旅程结果。

## 2. 提交、回滚与治理回归（pytest）

- 定向（本票新回归）：
  `tests/profiles/test_issue02_memory_write_boundary.py` 7 项全过：
  构造纯净性（不补任务/不入队/不写行）；显式 `recover_inflight_runs`
  重启语义（RUNNING→PENDING、任务重建、原 last_error 保留）；app 启动显式
  恢复；读取接口与导出后行数不变（GET items/evidence/controls 与导出）；
  内存组合恢复 no-op；`_RecordingCommit` 记录用户权威动作恰好打开 5 次
  提交边界（remember/modify/delete/forget/生命周期）；失败回滚无残留。
- `tests/profiles/` 整目录：分支 543 passed / 1 failed，main 536 passed /
  1 failed；失败均为既有 `test_issue01_chat_profile_correction.py::
  test_chat_correction_uses_latest_record_and_is_idempotent`（issue 01 记录
  的既有时序失败，两侧同名同因）。
- `tests/profiles/test_issue04_profile_commit.py` 与
  `test_issue06_profile_recovery.py`（提交边界与恢复回归）全过；重启恢复
  测试 `test_sqlite_restart_recovers_an_inflight_run_without_a_task` 按新
  两段语义全过。

## 3. 全量 pytest 对比（`--continue-on-collection-errors`）

命令：`python -m pytest -q --tb=no -rf --continue-on-collection-errors`
（两侧均忽略 4 个 main 上也存在的 `tests/career_plan/test_issue29_*`
收集错误——导入已不存在的旧符号）。

| | failed | passed | skipped | errors |
| --- | --- | --- | --- | --- |
| main `cfc224c4` | 346 | 5538 | 37 | 4 |
| 分支 `5d2865b4` | 344 | 5545 | 39 | 4 |

失败清单归一化比对（`FAILED` 行集合）对称差 4 项，逐项核实全部与代码无关：

1. `tests/learning/test_teaching_progress.py::test_answer_assessment_drives_remedial_next_action`
   （分支失败 / main 通过）：在 main 上单测重复 6 次出现 2 次失败（时序
   抖动），全量中两侧各自随机通过/失败；本文件整跑两侧均 5 passed。
2. `tests/closeout/test_api_boot.py` 2 项（main 失败 / 分支通过）：main
   检出中存在缺 `bridges` 的陈旧 `.venv`（`D:\BridGes\.venv`，已
   gitignore），该测试优先用 `.venv` 解释器启动 API 而失败；新建分支
   worktree 无 `.venv`，行为正确。属主检出环境差异，非代码差异。
3. `tests/chat/test_improvement13_acceptance.py::test_121_message_history_prepares_bounded_cache_and_converges`
   （main 失败 / 分支通过）：main 上重复运行 4 次出现 1 次失败，分支上
   2 次出现 1 次失败——两侧同样抖动。

计数差其余部分：+7 为本票新增测试全过；+2 skipped 为
`tests/runtime/test_runtime_contract.py` 两项 `NEEDS_WEB_BUILD`——main
检出存在 `apps/web/.next/standalone/server.js` 构建产物（gitignore），
新建 worktree 未构建，属环境差异；该两项在 main 上运行且双方语义不变。

## 4. 静态检查与依赖

- ruff（仓库配置）：受影响的 `src/bridges/profiles`、`app_setup_models.py`
  与 `tests/profiles` 在归一化后与 main 逐条一致（各 35 条既有 E501 等，
  无新增类别）；新测试文件与新工具脚本 `All checks passed`。
- mypy：`python -m mypy src` 分支 141 条 = main 141 条，错误集合逐条一致
  （错误均散布在既有文件；本票改动文件 0 条）。
- `python -m pip check`：`No broken requirements found.`
- 文件规模：无新拆的源码文件；新增测试 367 行、工具脚本 115/63 行，
  均 < 500 行（commit.py 348 行，未改动其规模）。

## 5. 恢复语义与纯净性的实现核对（对应验收③）

- 构造 `AutomaticProfileService` 不再触发任何恢复/入队（automatic.py:1505
  区域注释）；恢复入口为公开方法 `recover_inflight_runs`（automatic.py:1615）。
- 启动显式恢复：`api/app_setup_models.py:139`；历史重放：
  `profiles/replay.py:834`。grep 实证仓库内生产调用方仅此两处。
- 读取路径（GET items/evidence/migration 报告）、导出与评估组合不调用
  恢复；`_run_retry_task` 等后台路径只在显式调度后执行。

## 6. 验收标准对照

- [x] 原有正常操作和错误反馈与基线一致，无新模型调用、新状态、新产品
      行为 —— 第 1、2、3 节（旅程逐字段一致；模型调用数一致；全量失败
      集差异逐项归因于环境/抖动）。
- [x] 提交中途失败仍完整回滚，无双写残留 —— 第 2 节新回归（记录边界
      打开次数与失败回滚）＋ 既有 issue04 回归全过；本票未移除双写。
- [x] 读取、恢复检查和导出不启动业务执行或自动写入 —— 第 2、5 节。
- [x] 用户编辑优先、删除抑制、账户隔离和恢复测试不退化 —— 第 2 节整目录
      与 05/06 回归；两侧唯一失败为既有抖动测试。
- [x] 新拆文件不超过 500 行、职责与接口清楚 —— 第 4 节（本票未新拆源
      文件；commit.py 为既有 348 行边界模块）。
- [x] 03 可通过唯一提交接缝替换写模型，消费者清单有实引核对 ——
      `transaction-and-ownership.md` 第 1、2、5 节（每个入口带 file:line，
      生产双写与用户权威路径全部收敛到 ProfileCommit）。

## 7. 复现

```powershell
# 行为对比（两条命令分别生成 main / 分支证据后比较）
python scripts/run_issue01_profile_baseline.py --mode deterministic --output .tmp/issue02/baseline-branch-clean
python validation/02-write-boundary/tools/compare_profile_baseline.py "<main-glob>" "<branch-glob>"
python validation/02-write-boundary/tools/diff_profile_baseline_fields.py "<main-glob>" "<branch-glob>"

# 回归与静态检查
python -m pytest tests/profiles -q
python -m pytest -q --tb=no -rf --continue-on-collection-errors
python -m ruff check <变更文件>
python -m mypy src
python -m pip check
```

# 完整检查与固定基线比较（工单 13）

- 分支：`codex/issue-13-teaching-quality-and-release-acceptance`，HEAD `a51f62a3`
- 基线：`main` `902be64d`（主仓库干净工作树）
- 口径：项目完整后端测试套件（`python -m pytest`，默认配置，conda `agent` 环境）。日志保留于 `.tmp/`（未入库）。

## 结果

| 运行 | failed | passed | skipped | errors | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 分支（本票） | 345 | 5522 | 37 | 4 | 32:26 |
| main 基线 | 345 | 5517 | 37 | 4 | 37:52 |

- 分支比基线多 5 个通过用例，即本票新增测试：`tests/chat/test_issue13_learning_lifecycle_drill.py`（2）与 `tests/lifecycle/test_issue13_learning_data_lifecycle.py`（3）。
- 失败集合逐节点比较：**分支独有失败 0 项，基线独有失败 0 项**（345 = 345，两集合完全一致）。比较脚本输出：`branch-only: 0 / main-only: 0`。
- 已有失败按目录分布（基线同）：`tests/chat` 94、`tests/mcp` 49、`tests/plugins` 39、`tests/orchestration` 34、`tests/github` 22、`tests/learning_projects` 19、`tests/career_plan` 15（4 个收集错误同属该目录）、`tests/resources` 14、其余 ≤12。全部为本票之前的存量失败，未修复、未隐瞒。
- 结论：本票对共享代码的改动（`src/bridges/evaluation`、`scripts`、`src/bridges/chat` 的评测路径）未引入任何新增失败（AC16 基线比较成立）。

## 复现提示

- 在分支 worktree 与干净的 main 仓库分别运行完整 `pytest`，用 `^FAILED ` 前缀提取节点 id 后做集合差；本票运行日志与差集输出保留在 `.tmp/`（如需长时间保留，建议将差集摘要归档）。

# 工单 21 独立验收

日期：2026-10-02。交付提交 `2f889bb1`（基点 `main@83c27f2f`），验收修复提交 `a544bd0c`，合并提交 `2b3dc57c`（合并时 main 已推进到 `58962ddd`，含 Issue 18）。开发与测试使用 conda `agent`（Windows，Python 3.11.15）。

## Standards 轴

- 未发现规范违规。变更实现文件 `ruff check` 零诊断；`mypy` 对轻量策略与兼容入口 `20=20` 与基线一致，无受改文件新增错误。
- 变更面收敛：仅轻量表达策略、正式聊天接缝（`turn.py`/`service.py`）、兼容入口、测试与 `docs/人味化/` 文档；未触碰账户隔离、模式固定、附件分域与退役模块。
- 快照新增字段 `constraints`/`output_tokens` 均有默认值，旧快照反序列化原样复用，重试不重编译，无持久化迁移遗漏。

## Spec 轴

对照票面任务、验收标准与 `docs/人味化/实施方案.md`（材料边界、主要任务与可选承接）复核，交付实现复核通过；发现并修复 2 项正确性问题（提交 `a544bd0c`）：

1. **混合意图「焦虑又请求排查」未完成排查主请求**：`我好焦虑，帮我看看这个问题出在哪` 仍路由为 `empathy`，与「焦虑但正在请求排查时……也应完成排查；不能二选一丢掉需求」不符。修复：新增显式求助排查任务识别（“帮我看看这个问题/错误码/日志”等，排除“帮我看了……”已发生叙述），先于情绪承接路由为 `direct_task`；情绪承接只作可选补充。
2. **文章材料话题仍被推断为用户情绪**：`我最近在写一篇关于焦虑的论文`、`今天看了一篇文章讲焦虑` 仍落 `empathy`，与「引语、待翻译文本、待分析文章的情绪用语不是用户状态」不符。修复：扩展情绪话题词形覆盖“关于/对于 X 的论文/文章/书/研究”“论文/文章……讲/讨论/研究 X”，并把任务请求识别收窄为带“帮我”类前缀，避免“看了一篇文章”等叙述被误判为任务。

其余验收项复核通过：5 公里直接短答；倾诉不强制建议、不固定问“聊聊还是建议”；概念焦虑与翻译引语不推断情绪；建议/排查主请求优先于情绪；感谢已解决不机械追问；引号与否定边界（“不用详细讲”不触发长文）；续接与“你漏答了”按最近实质请求承接且不复制历史、无第二次分类调用；工具成功/部分/错误与权限拒答信号如实决定形态；默认 1024、显式长文/推导有界 2048 且仍受工单 04 最终载荷门约束；重试复用策略快照；编译异常回退安全基线。

## 验证

- 本票测试：交付 43 passed；验收追加 2 项后 `tests/chat/test_improvement21_contextual_expression.py` **45 passed**。
- `tests/chat` 全量（分支验收修复后）：**100 failed / 871 passed / 1 xfailed**；与 `main@58962ddd`（100 failed / 829 passed / 1 xfailed）失败名单逐项一致，**0 新增失败**；通过数差额为新增测试。
- 合并后 `tests/chat` 全量：**100 failed / 874 passed / 1 xfailed**，失败名单与合并前 main 逐项一致；合并树定向回归（本票 45 + 轻量策略 38 + Issue 18 60）**143 passed**。
- `tests/profiles` 全量：**479 passed / 1 failed**；唯一失败 `test_chat_correction_uses_latest_record_and_is_idempotent` 为既有时间相关环境失败，Issue 18 验收已记录。
- 静态检查：变更实现文件 `ruff` 零诊断；`mypy` 20=20 与基线一致；`docs/人味化/复核脚本.py` 与 `复核结果.json` 重生成（新增“焦虑又请求排查”“关于焦虑的论文”两个反例），JSON 仅新增对应两例。
- 环境性既有失败：`tests/chat` 100 项（`test_chat_service` 2 项、`test_web_search_chat` 10 项 V2 直连 `web_search_allowed` 恒 False、teaching 等）在 main 与分支同名同因，未新增。

## 合入与清理

- 合并 `codex/21-context-sensitive-companion-expression` 时与 Issue 18 在 `turn.py` 同一插入点冲突：画像失效复查（Issue 18）与工具状态汇总（Issue 21）两段均保留并依次执行，其余自动合并；合并提交 `2b3dc57c`。
- 已推送到 `origin`（`github.com:64922/BridGes`），`58962ddd..2b3dc57c`；推送经本机 `127.0.0.1:7890` 代理完成，未改动 git 配置。
- 本地 Issue 分支 `codex/21-context-sensitive-companion-expression` 已删除；工作树 `.worktrees/21-context-sensitive-companion-expression` 物理目录与登记均移除；`git worktree prune --dry-run --verbose` 无失效记录，`main`、Issue 17 与 `baseline-check` 等工作树保留。

## 验收边界

- 确定性规则与替身适配器只证明机制；真实模型体验与外部可得性按评测票 39/40。
- 保留已知限制：无引号第三人称转述（“他说：我不想听建议”）仍可能被当作本轮约束；续接分类使用最近实质请求的确定性回看，不消费工单 11 的 `ReferenceResolution.task`（正文补回由工单 11 在载荷完成）。

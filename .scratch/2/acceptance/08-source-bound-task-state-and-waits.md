# Issue 08 独立验收记录

日期：2026-10-01。原交付：`678c62b`（实现）、`7174b2b`（首轮评审修复），由上一会话完成、未推送未合并。本次接手完成：`88633c5`（table-owners 登记）、`5351a59`（schema 版本断言回归修复）、`b6c756a`（第二轮两轴评审修复）、`9e12c58`（跨轮补齐语义与用例）。验收比较点为当前主线 `1b35c02`，实现分支 `codex/08-source-bound-task-state-and-waits`，工作树 `.worktrees/08-source-bound-task-state-and-waits`；开发与验证使用 conda `agent`（Python 3.11.15）。

## Standards

首轮两轴评审未发现文档标准硬违规，提出 10 项判断项。本次处置：

已采纳并修复（`b6c756a`）：

1. 事务边界重复与 `in_transaction` 布尔穿透：仓库提供 `TaskRepository.transaction()`，按同一连接 `in_transaction` 自动并入外层；服务层与 `ConversationRepository.set_current_task` 共用该规则，全仓 `in_transaction` 参数清零。
2. `TaskTurnResult.events` 由 `list[str]` 收敛为 `list[TaskEventKind]`；绑定来源判断删除私有 `_BINDING_ORIGINS`，复用合同 `status_for_origin` 单一事实源。
3. v61 五张任务表加入 `REQUIRED_TABLES`：迁移半执行时启动失败关闭，而不是任务接口持续 500（沿用工单 01 的完整性清单先例）。
4. `api/tasks.py` 更正账户隔离文档：单任务读取跨账户 404、会话列表返回空表。

明确不采纳（均为判断题，理由留档）：

- `_find_effective_*_by_kind` 与 revoke/supersede 两段 UPDATE 保留现状：语义分别是任务/会话范围与显式撤销/自动取代，强合并会牺牲可读性，且已有测试覆盖。
- `TaskEvent.kind` 保持 `str`：审计表 append-only，需容忍未来版本写入的事件种类，不因未知 kind 读取失败。
- `task_versions.invalidated_at` / `invalidation_reason` 保留不写：条件修订后的产物失效由产物属主（工单 10/12/37）裁决，本票只保留字段接缝，避免越权替消费者下失效结论。
- `TaskRepository.set_current_task` 纯转发：`docs/table-owners.md` 明确指针属 `chat/` 域，这里转发就是属主边界本身。
- `test_improvement03_model_quota.py` 的 `SCHEMA_VERSION >= 60` 不收紧为 `>= 61`：放宽是为后续迁移不再被钉死，`>= 60` 仍验证迁移 60 已应用。

修复提交复审（第二遍 Standards 轴）未发现新增违规；唯一措辞问题（`_try_resolve_wait` docstring 声称剩余字段可后续补齐）已在 `9e12c58` 更正并补跨轮用例。

## Spec

首轮两轴评审发现 6 类问题，本次处置：

已修复（`b6c756a`）：

1. `is_new_topic` 合同字段未被执行：原实现对所有 `relation=new` 都暂停旧任务、挂起旧等待，普通聊天也会打断活跃任务。现改为仅 `is_new_topic or should_create` 时暂停；轻量闲聊不动旧任务与旧等待。
2. 多字段等待部分作答即整体解决：改为 `missing_fields ⊆ answer_fields` 才解决；部分答复保持开启。跨轮必须一次给出完整缺失字段集合，已补用例并在 docstring 写明。
3. `replaces` 未校验归属：显式取代现在必须指向目标任务的条件，跨任务取代抛 `TaskNotFound` 且整轮回滚。
4. `_apply_new` 非原子：暂停旧任务与创建新任务现在同一事务（并入 `transaction()`），创建失败回滚暂停，新增注入失败的回归用例。
5. 缺少 wait→答复→完成端到端用例：新增 `test_wait_answer_then_complete_end_to_end`（等待解决→任务活跃→完成、事件序列、历史等待不被重开）。
6. 来源分级与状态分离已有实现与用例；`TaskTurnResult.events` 类型化后领域断言 `isinstance(event, TaskEventKind)`。

明确不采纳：

- 「suspended 等待在回原任务时自动恢复为 open」：`contracts/tasks.py` 的 `WaitStatus` 合同原文写明 suspended「不会被后续消息误填，也不会被悄悄恢复为 `open`」；既有验收测试 `test_topic_switch_does_not_fill_old_wait_and_cancel_releases` 明确断言返回后仍为 `SUSPENDED`、终态经取消释放。自动恢复会推翻已冻结合同，故保持现状并作为已知限制记录（挂起等待只经取消/过期进入终态，无显式恢复入口）。
- 「消息 DONE / 运行 / 产物可信状态接线」与「部分完成」：属运行账本与产物属主（工单 09/10/12/37）。本票在合同层保证任务状态与它们的独立性（`TaskStatus` 与 `WaitStatus`、`TaskVersion` 分离），不替消费者实现状态机。
- 修复复审确认 6 项修复正确、未引入规格外行为；`events` 枚举化与 `REQUIRED_TABLES` 属评审驱动的合同面变更，已在实现说明登记。

## 最终行为与消费者合同

- 合同版本 `TASK_CONTRACT_VERSION = "task-v1"`；schema 61 新增 `conversations.current_task_id` 与 `conversation_tasks` / `task_versions` / `task_conditions` / `task_waits` / `task_events`。
- 写模型归 `bridges/tasks`；会话指针经 `chat/ConversationRepository.set_current_task` 属主写入（`docs/table-owners.md` 已登记五表归 `tasks/`）。
- 来源分级：用户明示与工具事实直接 `effective`，助手未获接受的方案 `draft`，模型推测 `clue`；草案/线索不进入有效条件投影。
- 版本不可变：条件修订/撤销/结果引用变化生成新版本；被取代或被撤销的值不因话题往返复活；完成任务可经 `revise` 重新激活。
- 范围：任务条件默认只约束原任务；用户明示 `conversation` 才跨话题保留；任务级条件不得取代同 kind 的会话级限制。
- 等待：绑定账户/会话/任务/预期版本/缺失字段/来源消息；非匹配答复、画像命令、学习动作、新话题不填旧等待；等待不携带租约，终态写入 `released_at`。
- API：`POST /tasks/turns` 确定性落地、`POST /tasks/waits` 登记等待，其余为只读投影；乐观版本冲突 409，账户隔离跨账户不可见。
- 后续消费者（09 运行预算、10 通勤内核、11 对象解析、12 主智能体）以本票任务/条件/等待为唯一权威状态，图与消息只存引用。

## 测试与证据

环境：`C:/Users/33755/anaconda3/envs/agent/python.exe`；按既有环境记录给 pytest 子进程加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（沙箱 safe-delete shim 的假 ERROR 规避，不改仓库/系统配置）。

- 工单定向：`pytest tests/tasks tests/storage/test_schema_v61.py tests/lifecycle/test_task_state_lifecycle.py` → **36 passed**（任务域 30 项含第二轮新增 6 项，v61 结构 3 项，任务生命周期 3 项）。
- 扩大比较：`pytest tests/tasks tests/storage tests/lifecycle tests/contracts tests/api tests/runtime` → **294 passed / 2 skipped / 5 failed**；5 项失败全部在 `tests/lifecycle/test_lifecycle_api.py::_create_chat`（409），已在 main 同文件复现，非本票引入。
- chat 名称级对照：修复前 `tests/chat` 101 failed / 615 passed，唯一新增失败为 Issue 03 用例硬编码 `SCHEMA_VERSION == 60`（`5351a59` 修复）；修复后 **100 failed / 616 passed / 1 xfailed**，与 main 基线失败名单**逐条一致，零新增**。
- 其他域：`tests/profiles tests/security tests/observability tests/expression tests/architecture tests/learning` → 594 passed / 2 skipped / 4 failed；4 项均在 main 基线复现（observability 2 项见 `05-regression-comparison.json` 既有失败名单，profiles/security 各 1 项）。
- 迁移与恢复：v60→v61 升级保留旧会话数据、旧会话指针为空；新库含五表与 `current_task_id`；五表纳入启动完整性清单。
- 合同同步：`pytest tests/contracts` **3 passed**；`openapi.json` 重新生成（events 枚举、missing_fields 描述）；`packages/contracts/src/generated.ts` 重新生成（补登 08 任务端点 +922 行，此前遗漏）。
- 静态检查：ruff 对改动源码/测试通过（余下 3 项 N818/E501 为 main 既有，未触碰行）；`ruff format --check` 通过；mypy `src/bridges/tasks src/bridges/contracts/tasks.py src/bridges/storage/database.py --follow-imports=silent` 无问题；`git diff --check` 通过。
- API 多轮重放与断进程恢复：`tests/tasks/test_task_api.py` 覆盖末尾条件、纠正、话题往返、等待错接、两个任务并存、跨账户 404、乐观 409、同库重建应用后投影恢复。

## 验证限制

- 仅确定性请求重放，未接真实模型；工单 12 的主智能体理解与 11 的对象定位未在本票实现，评审使用直构 `TaskTurnRequest`。
- 「同一会话初期只允许一个前台可写运行」属工单 09；本票只提供写模型与单指针，不实现并发运行准入。
- suspended 等待无显式恢复路径（合同要求不悄悄恢复）；恢复动作留给后续澄清流程设计，若需要须先更新合同与既有验收测试。
- `tests/chat` 既有 100 项失败与环境性 Windows 文件锁抖动沿用 main 基线，不宣称全量通过。
- `task_versions.invalidated_at` / `invalidation_reason` 为消费者预留字段，本票不写；产物失效由工单 10/12/37 验收。

## 合并与清理

待处理：分支尚未推送、未合并；本节在推送/合并并清理工作树后补充（含远端 SHA、合并提交与 `git worktree remove` 结果）。

# Issue 10：建立通用、持久化、幂等的 model_run_lock 接口

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-QWEN-AUDIT-01、US-QWEN-AUDIT-02、US-QWEN-AUDIT-03

## 根因与证据

- `ModelGateway` 已为尝试路径产生不可变 `ModelRunLock`，合同包含 `lock_id`、`run_id`、`account_id`、`project_id`、capability、实际模型、参数、prompt/contract、fallback、状态、重试和 usage。
- SQLite 的 `model_run_locks` 表只保存较小子集：`src/bridges/storage/database.py` 中没有 `run_id`、`project_id`、prompt/contract、retry/fallback、degradation 等字段，无法稳定关联非聊天业务调用和同一 run 的多次调用。
- 唯一通用落库动作实际位于 `src/bridges/chat/repository.py::insert_run_lock`，它是聊天 repository 的普通 `INSERT`。跨域服务要么丢弃 gateway 返回的锁，要么各自把 JSON 塞进业务对象；同一锁在重试/恢复时再次插入会主键冲突，而不是幂等重放。
- `messages.run_lock_id` 只容纳一条主锁，Humanizer 修订、Career 修复、OCR 多页、Embedding 批次和供应商取消等“一次业务操作多次模型动作”无法靠该字段完整表达。
- Issue 11–16 都需要同一种审计接缝；若各域自行补 INSERT，会产生字段、事务、脱敏和幂等语义漂移。

## What to build

1. 在 AI/审计所有权边界定义 `ModelRunLockRecorder`（或等价端口），业务域只依赖该端口，不依赖 `ConversationRepository`。最低接口：
   - `record(lock, *, business_ref, operation, attempt_ordinal) -> persisted lock`；
   - `record_many(...)`，在一个事务内按调用顺序保存多条锁；
   - 按 `lock_id`、`account_id + run_id`、业务引用查询的只读接口，供投影与发布门验证。
2. 扩展持久化 schema，完整保存审计所需的非秘密字段，并建立规范关联：账户、run、capability/version、provider/region、实际 model、status、retry、fallback、prompt/contract、usage、created_at、业务对象类型/ID、operation 和调用序号。正文/媒体内容与 Key 不得进入该表。
3. 规定幂等语义：同一 `lock_id` 与完全相同 canonical 内容重复记录视为成功且只保留一行；同一 ID 内容不同报稳定 `model_run_lock_conflict`，原记录不可覆盖。
4. 为业务关联设计独立关联表或等价规范结构，使一个 message/job/document/page/batch/task 能关联多条锁，一条锁也可保留主业务引用；`messages.run_lock_id` 在兼容期可作为主要锁投影，但不能成为唯一审计来源。
5. 明确事务边界：业务状态若声称模型调用已完成，对应锁必须在同一提交单元中持久化；无法原子提交时先保存可恢复 pending/attempt 证据，并通过 outbox/恢复器最终闭合，不能出现“业务成功但锁永远丢失”。
6. 提供迁移与向后兼容：保留已有锁 ID/时间/状态，能安全读取旧行；不可从缺失字段伪造调用事实，未知字段标记为 legacy/unknown。迁移幂等且支持中断后重跑。
7. 统一脱敏与字段白名单，拒绝或清理 API Key、Authorization、prompt、响应正文、用户消息、OCR 文本、图片/视频提示词等敏感 payload。
8. 将聊天现有 `_persist_lock`/`insert_run_lock` 迁移到 recorder，作为 tracer bullet 验证接口；其他域接线留给 Issue 11–16。

## 非目标

- 不在本 issue 中完成 Humanizer、Career、Profile、OCR、Embedding 和媒体边缘动作的全部接线；这些由后续 issue 使用本接口完成。
- 不改变 ModelGateway 的模型选择或全局 Qwen Key 策略。
- 不把请求/响应正文完整持久化以支持“重放”；运行锁是审计快照，不是敏感内容存档。
- 不将本地 deterministic、DDG、arXiv 等非模型动作伪造成 `model_run_lock`；如需审计应使用各自的 tool/search 记录。
- 不覆写、合并或删除已有不可变锁来“修复”统计。
- 不要求一个业务对象只能有一条锁，也不把多次供应商调用折叠成一条汇总锁。

## Acceptance criteria

- [ ] 所有域可通过独立 recorder 端口保存锁，不需要导入 chat repository。
- [ ] 新 schema 能无损持久化 `ModelRunLock` 的审计字段，并保存 business ref、operation 和稳定调用序号；字段与合同有版本号。
- [ ] 同一 `lock_id`、相同 canonical 内容重复记录 N 次只产生一条锁和一组幂等关联，所有调用返回成功。
- [ ] 同一 `lock_id`、不同内容绝不覆盖原行，返回 `model_run_lock_conflict` 并产生不含正文的安全告警。
- [ ] `record_many` 要么按原顺序全部提交，要么全部回滚；并发写入不产生重复或乱序关联。
- [ ] 一次业务 run 可查询全部锁并按 attempt ordinal 排序；单一消息/任务可关联多条锁，主要锁投影仍兼容现有 API。
- [ ] 模型成功状态与锁持久化满足原子/可恢复合同；故障注入后不存在不可恢复的“业务成功、锁缺失”。
- [ ] 迁移前已有 `model_run_locks` 可读，ID、账户、模型、状态和时间不变；缺失关联明确标为 legacy，而非伪造 run/object。
- [ ] 账户作用域查询不能读取其他账户锁；业务对象 ID 冲突不会越权关联。
- [ ] recorder 拒绝或脱敏秘密与正文；数据库、日志和错误不含 Key、Authorization、prompt、响应、OCR 文本或媒体提示词。
- [ ] 普通聊天成功、失败、超时和重试回归均通过 recorder 持久化；进程重启后锁和消息关联仍可查询。
- [ ] 非模型本地操作不会产生模型锁。

## Test plan

1. 为 recorder 做 repository/contract 测试：单条、多条、幂等重放、冲突、并发、事务回滚、顺序和账户隔离。
2. 做数据库迁移测试：从当前旧 schema 建库并插入历史行，升级两次，验证幂等、历史字段不变、legacy 缺失字段可识别。
3. 做故障注入：分别在锁写入前、关联写入中、业务提交前后中断，重启恢复后只能得到“完整成功”或“明确失败/待恢复”，不能静默缺锁。
4. 构造一个 run 两次调用、一个对象多页/多 attempt 的样本，断言每次实际供应商动作独立成锁且排序稳定。
5. 迁移 chat tracer bullet，覆盖 success、blocked、retryable fail、timeout、重试和重复消费同一完成事件。
6. 注入跨账户相同业务 ID 与恶意敏感字段，验证授权边界和脱敏拒绝。
7. 扫描日志和 SQLite 测试文件，断言不存在测试 Key、Authorization、prompt 或完整 response。

建议验证命令：

```powershell
python -m pytest `
  tests/ai/test_model_gateway.py `
  tests/chat/test_chat_service.py `
  tests/integration/test_qwen_capability_gateway.py `
  tests/storage/test_model_run_lock_recorder.py `
  tests/storage/test_model_run_lock_migration.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue10
```

实现者如采用不同测试目录，可等价调整文件名，但不得减少幂等、迁移、并发、故障恢复与账户隔离覆盖。

## Observability & rollback

- 低基数指标：`model_lock_record_total{capability,status}`、`model_lock_record_conflict_total{capability}`、`model_lock_recovery_pending_total{operation}`、`model_lock_missing_business_ref_total{capability}`。
- 日志只包含 lock ID、capability、status、run/business 引用的不可逆安全标识及稳定错误码；不记录模型输入输出、Key 或 Authorization。
- schema 迁移采用 expand-first：先增加新表/列和双读能力，再切 chat writer，确认后才停止旧写法；不得先删除旧字段或外键。
- 回滚应用时保留所有新旧锁与关联数据；旧版本无法识别的新字段应安全忽略。禁止通过删除冲突锁或清空审计表回滚。
- 建议稳定错误码：`model_run_lock_conflict`、`model_run_lock_persist_failed`、`model_run_lock_link_failed`、`model_run_lock_recovery_required`、`model_run_lock_scope_violation`。

## Blocked by

- 无。

## Comments

- 2026-08-13：本 issue 是 Issue 11–16 的公共基础设施；各业务 issue 可先写 adapter/service 测试，但在本接口完成前不能宣称持久化审计闭环。
- 2026-08-13：锁记录的是每一次真实模型动作，包含失败与重试；业务投影可选择一条主要锁，但审计源不得折叠调用历史。

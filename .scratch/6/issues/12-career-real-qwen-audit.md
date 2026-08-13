# Issue 12：补齐 Career 真实 Qwen 生成与修复审计闭环

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-QWEN-01、US-QWEN-02、US-CAREER-01

## 已验证现状与根因

- `src/bridges/career/service.py` 的 `CareerPlannerService._invoke_structured` 通过 `ModelGateway.invoke("qwen_structured_output", "1", ...)` 生成六类规划结果，但只消费 `call_result.output/status/error`，没有持久化 `call_result.lock`。
- `_invoke_and_parse` 在结构校验失败且预算允许时调用 `_invoke_structured` 第二次执行有界修复。因此一次用户操作可能真实调用 Qwen 两次；当前业务投影与观测记录都无法在重启后证明首次失败和修复成功分别发生过。
- `_audit` 记录的是 Career 领域终态，不等价于供应商模型运行锁；“规划完成”不能替代 provider、固定 model ID、调用状态和调用序号等真实调用证据。
- 当前 `tests/career/test_career_service.py` 已使用可编程结构化 adapter 覆盖正常输出、格式修复、限流、鉴权和边界违规，但未断言每次网关调用都形成独立持久化锁。
- 生产凭据范围已经确认：继续使用安装级全局 Qwen Key，不增加账户级 Key。账户、会话、助手消息及业务 run 仍必须严格隔离并写入锁的业务关联。

## What to build

1. 按 Issue 10 的统一模型运行记录接口，在 Career 每次结构化调用后立即、幂等持久化返回锁；首次生成和一次有界修复必须是两条不同记录。
2. 给同一 Career run 内的调用赋予稳定阶段和序号：首次为 `career_generation:1`，只有真实发起修复请求时才有 `career_repair:2`。本地宽容解析、确定性复核和投影构造不得生成模型锁。
3. 记录成功、降级、可重试失败、永久失败及结构化解析失败对应的供应商调用证据。首次模型成功但输出违反合同，应保存“供应商调用成功”的锁，同时由业务审计记录“结构无效”；不能篡改模型锁状态来表达业务复核结果。
4. Career 终态投影保留主要锁引用或业务 run 引用，使调用集合可按账户和 run 查询；完整锁不可嵌入投影，也不得持久化提示词、规划正文或用户画像内容。
5. 保持现有调用上限、RunBudget、六类规划合同、证据边界、承诺词限制和失败恢复语义。补锁不得额外重试，不得在预算不足时发起修复调用。
6. production composition 只允许 Issue 09 固定模型矩阵中的真实 Qwen adapter；缺 adapter、Stub、fixture/cassette 或模型漂移时失败关闭。

## 上下文指针

- `src/bridges/career/service.py`：`CareerPlannerService.run_task`、`_invoke_and_parse`、`_invoke_structured`、`_audit`。
- `src/bridges/ai/model_gateway.py`：网关调用及 `ModelRunLock` 语义。
- `tests/career/test_career_service.py`：生成、修复、鉴权、限流、结构及领域边界测试。
- `tests/career/test_career_routing.py`、`tests/career/test_career_review.py`：路由和本地复核边界。

## 非目标

- 不修改 Career 规划六类输出、提示词、证据来源规则、画像启用逻辑或确定性复核标准。
- 不扩大一次有界修复为多次重试，也不绕过 RunBudget。
- 不把路由判定、证据组装、宽容解析、本地复核或投影生成标成 Qwen 调用。
- 不把安装级全局 Qwen Key 改成账号级 Key；不得读取、打印、哈希、回显或持久化 Key。
- 不用 fake adapter、fixture、cassette、预置 JSON 或普通 HTTP 成功替代真实供应商 smoke。
- 不重新定义通用锁表与事务协议；依赖 Issue 10，只完成 Career 业务接线。

## Acceptance criteria

- [ ] Career 的全部 `qwen_structured_output` 调用点统一持久化 `ModelRunLock`，静态检查不存在调用后丢弃锁的路径。
- [ ] 正常一次生成恰好写入一条 `career_generation` 锁，关联账户、会话、助手消息、业务 run、固定模型、阶段和调用序号。
- [ ] 首次结构无效且修复成功时恰好写入两条不同锁，顺序为 generation、repair；重启后两条均可查，规划投影可定位同 run 的完整集合。
- [ ] 首次结构无效但预算不足时只有一条 generation 锁，不得伪造 repair 锁；现有可重试中文错误保持不变。
- [ ] 首次或修复调用发生鉴权、限流、网络、解析等失败时，每次已经发起的供应商调用均有对应状态锁，且 Career 业务终态不能覆盖模型运行状态。
- [ ] 供应商返回成功但输出违反结构合同或后续本地边界复核失败时，模型锁仍准确记录调用成功，领域审计另行记录 `career_output_invalid` 或 `career_boundary_violation`。
- [ ] recorder 重复提交相同锁幂等；真正重新执行模型请求时保存新的调用序号，不覆盖旧锁。
- [ ] 日志、锁及指标不得保存 Key、Authorization、system/user prompt、用户陈述、画像切片、证据正文、规划正文或完整响应。
- [ ] production-like 组合检测到 Stub、fixture、cassette、缺少 adapter 或 Issue 09 模型矩阵漂移时失败关闭，不能生成规划完成态。
- [ ] 两账户并发规划时锁和投影按账户隔离；共享安装级 Key 不得成为跨账户查询依据。

## Test plan

1. 扩展 `tests/career/test_career_service.py` 的可编程 adapter：正常生成断言 1 锁；结构非法后修复成功断言 2 锁；预算不足断言 1 锁；首次失败和修复失败断言每个实际调用均有正确状态锁。
2. 添加临时 SQLite 合同测试，覆盖同 run 多锁、阶段/序号、幂等、跨账户隔离、投影前崩溃及重启后查询。
3. 注入 recorder 持久化失败，验证 Issue 10 的失败关闭行为：不得把无可持久审计证据的模型结果提升为 Career 完成态。
4. 添加架构测试，枚举 Career 网关调用点并禁止绕过统一 recorder；验证模型 ID 来自 Issue 09 单一事实源。
5. 对本地路由、证据组装和 review 注入模型调用 spy，断言这些步骤不新增模型锁。
6. 可选真实 smoke：仅在显式开关且已配置安装级全局 Qwen Key 时，禁用 Stub、fixture、cassette 和网络录制，完成一个最小规划请求并重启查询真实锁。若要覆盖修复分支，应使用受控测试提示触发真实结构不合法，不得以 fake 响应冒充；无法稳定触发时只把该分支留在 fake 合同测试中。代码只能判断凭据是否已配置，不能读取或输出 Key。

建议回归命令：

```powershell
python -m pytest `
  tests/career/test_career_service.py `
  tests/career/test_career_review.py `
  tests/career/test_career_model_run_locks.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue12-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_CAREER_REAL_SMOKE='1'
python -m pytest tests/career/test_career_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 按 `career_generation`、`career_repair`、模型状态和稳定错误码聚合调用数、锁数、缺锁数、延迟和 usage；不采集提示词或规划内容。
- 增加 `career_missing_run_lock`、`career_lock_persist_failed`、`career_call_sequence_mismatch` 稳定错误码。
- 灰度期核对每个 run：正常为 1 条、发生真实修复为 2 条、预算不足仍为 1 条；超出两条或阶段倒序均告警。
- 若 recorder 接线引起回归，可停止 Career 发布并回滚接线层，但不得删除、改写或合并已经保存的锁，也不得长期以关闭审计恢复功能。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：已验证 Career 生成与一次有界结构修复都走真实结构化模型网关，但返回锁当前未持久化。
- 2026-08-13：用户确认使用安装级全局 Qwen Key；本 issue 只记录模型运行元数据和业务关联，严禁读取或泄露 Key 与用户内容。

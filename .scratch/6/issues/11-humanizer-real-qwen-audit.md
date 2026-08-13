# Issue 11：补齐 Humanizer 真实 Qwen 首稿与修订审计闭环

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-QWEN-01、US-QWEN-02、US-HUMANIZER-01

## 已验证现状与根因

- `src/bridges/skills/humanizer/service.py` 的 `HumanizerService._invoke_draft_model` 通过 `ModelGateway.invoke("qwen_structured_output", "1", ...)` 生成首稿；网关返回的 `call_result.lock` 没有进入持久化接口，方法只返回正文和延迟。
- 同文件的 `_invoke_revision_model` 会读取 `call_result.lock.usage` 计算额外 token，但仍未保存这条锁；一次任务发生“首稿 + 定向修订”时，两次真实 Qwen 调用都无法在重启后审计。
- Humanizer 还有兼容路径 `_invoke_model`。实现时必须枚举该服务内全部结构化模型调用点，不能只修复当前表达任务分支而留下另一条无锁入口。
- 当前测试用可编程 adapter 已覆盖单次首稿、两次调用、修订失败、预算不足和用户停止等行为，但主要断言结果与调用次数，没有证明每次实际模型调用都有独立、持久化、可关联的运行锁。
- 这是“真实调用已经发生，但业务层丢弃网关证据”的问题，不是要求新增模型生成逻辑。生产凭据继续使用安装级全局 Qwen Key；账户 ID 只用于数据、运行上下文和锁的隔离，不参与 Key 选择。

## What to build

1. 按 Issue 10 提供的统一记录接口，把 Humanizer 的每次 `ModelGateway.invoke` 返回锁立即、幂等地持久化；首稿、证据安全修订、裁决定向修订及兼容路径必须走同一接缝。
2. 为同一 Humanizer 业务 run 内的调用赋予稳定阶段标识与调用序号，例如 `humanizer_draft:1`、`humanizer_revision:2`；不能用终态结果覆盖首稿锁，也不能把两次调用折叠为一条。
3. 成功、降级、可重试失败、永久失败、空输出和结构校验失败都必须保留模型调用锁。模型已返回但候选稿未通过本地复核时，锁仍代表真实发生过的调用。
4. Humanizer 结果投影只需保存主要锁引用或业务 run 引用；完整调用集合由统一锁仓库按 `account_id + run_id` 查询。投影不得复制 prompt、正文或模型完整响应。
5. 保留现有“每篇最多首稿一次、修订一次”的调用预算、事实/来源硬门和交付语义；补锁不得触发额外 Qwen 请求，也不得改变是否修订的裁决。
6. production composition 必须绑定 Issue 09 固定的结构化模型和真实 Qwen adapter；测试替身只能用于合同测试，不能被生产启动路径接受。

## 上下文指针

- `src/bridges/skills/humanizer/service.py`：`HumanizerService.__init__`、`run_task`、`_invoke_draft_model`、`_invoke_revision_model`、`_invoke_model`。
- `src/bridges/ai/model_gateway.py`：`invoke` 及 `ModelRunLock` 生成语义。
- `tests/humanizer/test_article_draft_service.py`：首稿与两次调用合同。
- `tests/humanizer/test_article_revision_service.py`：定向修订、失败、预算不足与停止场景。
- `tests/humanizer/test_article_real_smoke.py`：显式启用的真实 Humanizer smoke。

## 非目标

- 不把安装级全局 Qwen Key 改成账号级 Key，也不读取、打印、哈希、回显或持久化 Key。
- 不修改 Humanizer 提示词、体裁规则、事实锁、来源账本、修订触发阈值或两次调用上限。
- 不把本地表达复核、事实校验或结果投影伪装成模型调用。
- 不把 fake adapter、fixture、cassette、非空模板正文或 HTTP 200 当作真实提供方证明。
- 不在本 issue 中定义通用锁表和事务协议；该能力由 Issue 10 提供，本 issue 只完成 Humanizer 接线。

## Acceptance criteria

- [ ] Humanizer 服务内每个 `qwen_structured_output` 调用点都通过统一记录接缝；静态检查不存在直接调用后丢弃 `call_result.lock` 的路径。
- [ ] 只生成首稿的成功任务恰好新增一条 Humanizer 模型锁，锁可关联账户、会话、助手消息、业务 run、阶段 `draft`、调用序号和固定模型 ID。
- [ ] 触发修订的任务恰好新增两条不同锁；首稿与修订顺序稳定，均在进程重启后可查，终态投影不能覆盖或删除首稿锁。
- [ ] 修订未触发、用户在修订前停止或预算不足时只保留首稿锁，不伪造修订锁。
- [ ] 修订调用失败时保留首稿成功锁和修订失败锁；现有“交付首稿/保持原文/待用户确认”语义不变。
- [ ] 结构化响应为空、解析失败或本地事实/来源复核不通过时，已发生的供应商调用仍有对应锁，锁状态和业务终态不得互相冒充。
- [ ] 重复投递同一记录事件不会产生重复锁；恢复执行若确实发起新模型调用，则以新的调用序号保存新锁，而不是覆盖旧锁。
- [ ] 锁与日志只保存 capability、provider、固定 model ID、状态、时间、延迟、usage、稳定错误码和业务关联；不保存 Key、Authorization、prompt、原文、候选正文、画像内容或完整响应。
- [ ] production-like 组合在缺少真实 adapter、出现 Stub/cassette 或模型不符合 Issue 09 矩阵时失败关闭，不得返回 Humanizer 完成态。
- [ ] 两个不同账户并发执行时，锁查询严格按账户隔离；全局 Key 的共享不得造成业务数据或锁串户。

## Test plan

1. 扩展 Humanizer fake adapter 合同测试：分别运行单次首稿、首稿后修订、修订前停止、预算不足、首稿失败、修订失败、空输出和本地复核失败，断言 adapter 调用数、锁数、顺序、阶段、状态及业务关联一致。
2. 使用临时 SQLite 仓库验证锁幂等写入、同 run 多锁、跨账户隔离和进程重启后可查；模拟“模型成功后、结果投影前”崩溃，重启后首稿锁仍存在。
3. 注入 recorder 写入失败，按 Issue 10 的失败关闭合同断言不能把无审计的真实模型输出提升为完成态。
4. 加架构测试扫描 Humanizer 全部 `gateway.invoke` 调用点，确保都经统一记录接缝且模型 ID 只来自 Issue 09 单一事实源。
5. 可选真实 smoke：只有显式开关且运行环境已经配置安装级全局 Qwen Key 时，禁用 Stub、fixture、cassette 和 record/replay，完成一条最小首稿及一条会触发修订的任务；重启后核对 1 条与 2 条真实锁。测试代码不得读取或输出 Key，只检查凭据“已配置/未配置”。

建议回归命令（实现者可按最终文件名等价调整）：

```powershell
python -m pytest `
  tests/humanizer/test_article_draft_service.py `
  tests/humanizer/test_article_revision_service.py `
  tests/humanizer/test_humanizer_model_run_locks.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue11-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_HUMANIZER_REAL_SMOKE='1'
python -m pytest tests/humanizer/test_article_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 按 `humanizer_draft`、`humanizer_revision`、状态与稳定错误码统计调用数、锁数、缺锁数、延迟和 token；指标不得包含正文或提示词。
- 为 `humanizer_missing_run_lock`、`humanizer_lock_persist_failed`、`humanizer_lock_business_mismatch` 提供稳定错误码，并让缺锁场景失败关闭。
- 灰度时比较 `writing_call_count` 与同 run 持久化锁数；二者不一致立即告警，区分“未触发修订”与“修订调用缺锁”。
- 如接线引发回归，可回滚 Humanizer 对新 recorder 的依赖并停止发布，但不得删除、改写或补造已经持久化的锁，也不得以关闭审计作为长期回滚方案。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：已验证首稿和修订均真实走结构化模型网关，但服务层未持久化返回锁；本 issue 不重复实现 Humanizer 生成能力，只闭合真实性与审计证据。
- 2026-08-13：凭据范围已由用户确认：使用安装级全局 Qwen Key；任何测试、日志和 issue 实施均不得读取或泄露 Key 内容。

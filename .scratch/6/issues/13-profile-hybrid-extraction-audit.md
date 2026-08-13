# Issue 13：保留 Profile 混合抽取并诚实标记来源与 Qwen 审计

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-QWEN-01、US-QWEN-03、US-PROFILE-01

## 已验证现状与根因

- `src/bridges/profiles/automatic.py` 已实现有意的混合策略：生产可配置 `GatewayAutomaticProfileExtractor`，但 `AutomaticProfileService._extract_once` 遇到 `signal_classification.is_local` 时会改用 `RuleBasedAutomaticProfileExtractor`。
- 明确自述、学习目标、清晰观察、更正/撤回等高置信信号适合本地确定性处理；歧义信号才需要 `GatewayAutomaticProfileExtractor` 调用 `qwen_profile_extraction`。用户已确认保留该策略，不要求每条画像都消费 Qwen。
- `GatewayAutomaticProfileExtractor.extract` 调用 `ModelGateway.invoke` 后只解析输出，返回的 `result.lock` 没有持久化；队列重试真实再次调用 Qwen 时也缺少逐次证据。
- 现有 `ProfileExtractionRun`、观察记录和状态投影主要表达抽取结果/重试状态，尚不足以让用户或审计明确区分“本地规则识别”与“Qwen 辅助识别”。混合能力如果统一显示为“AI 自动提取”，会误导用户；如果统一要求模型锁，又会为本地分支伪造调用。
- 生产凭据为安装级全局 Qwen Key。Key 不按账户选择；账户隔离仍由运行上下文、画像仓库、队列任务和模型锁关联保证。

## What to build

1. 固化并文档化两条执行路径：`local_rule` 用于分类器标记为本地的高置信信号，`qwen_model` 只用于需要语义判断的非本地/歧义信号；分类结果必须是可测试、可观测的显式决策。
2. 为抽取 run、观察/提交结果和对用户可见的来源说明增加稳定来源枚举及中文标签，例如 `local_rule`（“本地规则识别，未调用模型”）与 `qwen_model`（“Qwen 辅助识别”）。禁止用一个模糊的“AI 自动”标签覆盖两者。
3. 仅 `qwen_model` 分支按 Issue 10 持久化每次 `ModelGateway.invoke` 返回锁；队列重试若再次发起真实请求，必须新增带 attempt/sequence 的锁。`local_rule` 分支调用数和锁数都必须为 0。
4. 将来源决策与业务 run、消息、分类器版本、抽取器版本及稳定 reason code 关联；不保存消息正文、规范化画像值或模型完整输出到锁/普通日志。
5. Profile 页面、首次自动记录说明或相应 API 投影应诚实解释混合策略：明确内容可在本机规则处理，歧义内容可能使用全局配置的 Qwen；用户原有查看、更正、撤回和关闭自动记录能力保持不变。
6. production composition 中 Qwen 分支只使用 Issue 09 的固定真实 adapter；缺 adapter 时该分支进入既有可恢复/降级状态，不得悄悄改用模板结果。本地分支不应因 Qwen 不可用而失效。

## 上下文指针

- `src/bridges/profiles/signals.py`：`ProfileSignalClassification`、`is_local` 及分类理由。
- `src/bridges/profiles/automatic.py`：`RuleBasedAutomaticProfileExtractor`、`GatewayAutomaticProfileExtractor.extract`、`AutomaticProfileService._extract_once`、`_gateway_attempted`、重试和结果审计。
- `src/bridges/contracts/profile_extraction.py`：抽取 run、outcome、retry task 和投影合同。
- `tests/profiles/test_automatic_profile_extraction.py`：本地/网关分支、队列重试、恢复与账户隔离测试。

## 非目标

- 不把所有画像信号都改成 Qwen，也不删除高置信本地规则路径。
- 不让本地分支创建“占位锁”或假模型成功记录；模型锁只证明真实供应商调用。
- 不改变画像四维模型、隐私阻断、更正/撤回语义、可靠度门槛或用户关闭自动记录的权利。
- 不将安装级全局 Qwen Key 改为账号级 Key；不读取、打印、哈希、回显或持久化 Key。
- 不把 fake、fixture、cassette、预置输出当成真实 Qwen smoke。
- 不在本 issue 中重做通用持久化锁协议；依赖 Issue 10。

## Acceptance criteria

- [ ] 每个自动画像 run 都有明确且稳定的 `local_rule` 或 `qwen_model` 来源；不存在空来源、模糊 `ai` 来源或同一 attempt 同时标记两种来源。
- [ ] `signal_classification.is_local=True` 时只运行规则抽取器，网关调用数为 0、模型锁数为 0；结果对用户显示“本地规则识别，未调用模型”或同等明确中文。
- [ ] 非本地歧义分支真实调用 `qwen_profile_extraction`，每个实际 attempt 保存独立锁，并显示“Qwen 辅助识别”；锁关联账户、会话、消息、抽取 run、attempt、分类器/抽取器版本和固定模型。
- [ ] Qwen 分支失败并进入队列重试时，首次失败锁保留；重试真实发生后新增下一序号锁，不覆盖前次证据。
- [ ] Qwen 不可用时，本地规则分支仍可成功；需要模型的分支不得伪造本地规则输出来冒充等价语义，必须保持既有可恢复/永久失败状态和稳定错误码。
- [ ] 明确自述、学习目标、清晰观察、更正与撤回代表性用例均证明不调用 Qwen；至少一类歧义用例证明调用 Qwen。
- [ ] 来源标签出现在相关状态/API 投影和首次说明中，且不夸大为“所有画像均由 Qwen 生成”或“完全不使用模型”。
- [ ] 锁、来源元数据和日志不含 Key、Authorization、消息正文、画像值、敏感信息、prompt 或完整响应。
- [ ] production-like 组合中 Qwen 分支若绑定 Stub、fixture/cassette、缺 adapter 或模型矩阵漂移则失败关闭；本地分支仍保持纯本地。
- [ ] 两个账户并行抽取、重试和回放时，来源、锁与画像记录严格隔离；共享全局 Key 不得泄露任何跨账户关联。

## Test plan

1. 扩展 `tests/profiles/test_automatic_profile_extraction.py` 参数化用例：明确自述、学习目标、行为观察、更正/撤回断言来源 `local_rule`、网关 0 调用、0 锁；歧义信号断言 `qwen_model`、1 调用、1 锁。
2. 使用可控 fake gateway 测试 Qwen 成功、永久失败、可重试失败及队列重试；断言每个真实模拟调用对应一条独立锁、attempt 顺序稳定、来源不被重试改写。
3. 临时 SQLite 测试来源字段与锁跨重启可查、幂等写入、回放/恢复和跨账户隔离；验证本地分支不存在任何模型锁。
4. 对用户可见 Profile 状态/首次说明做 API 合同测试，断言两种中文来源说明准确，且不包含画像正文或凭据细节。
5. 生产组合测试分别移除 Qwen adapter：本地用例仍绿，歧义用例失败关闭；注入 Stub/cassette 时生产门必须跑红。
6. 可选真实 smoke：显式开关且已配置安装级全局 Qwen Key 时，禁用 Stub、fixture、cassette 和录制，提交一条不含敏感信息的最小歧义信号，验证真实输出、来源及持久锁；同时跑一条本地信号证明真实调用增量为 0。代码不得读取或输出 Key。

建议回归命令：

```powershell
python -m pytest `
  tests/profiles/test_automatic_profile_extraction.py `
  tests/profiles/test_profile_hybrid_source_projection.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue13-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_PROFILE_REAL_SMOKE='1'
python -m pytest tests/profiles/test_profile_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 按 `local_rule`/`qwen_model`、classification reason、outcome、attempt 和稳定错误码统计，不记录用户消息与画像值。
- 监控 `profile_local_unexpected_model_call`、`profile_qwen_missing_run_lock`、`profile_source_mismatch`、`profile_lock_persist_failed`；本地分支出现模型调用或 Qwen 分支缺锁均告警并失败关闭对应任务。
- 灰度期比较来源计数、网关调用计数和锁数：`local_rule` 必须是 0/0，`qwen_model` 每个 attempt 必须 1/1。
- 如来源投影导致兼容问题，可暂时回滚 UI 展示版本并保留后端来源字段；不得回滚为模糊标签、删除锁或把 Qwen 分支伪装成本地成功。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：用户确认保留“本地规则 + Qwen”混合画像策略，并要求诚实披露；只有真实 Qwen 分支落模型锁。
- 2026-08-13：凭据继续使用安装级全局 Qwen Key；本 issue 严禁读取或泄露 Key，账户隔离仍是强制验收项。

# Issue 17：建立全功能 Qwen 真实性发布门

Status: ready-for-agent

Type: task

Priority: P0（release gate）

User stories: US-QWEN-01、US-QWEN-02、US-QWEN-03、US-QWEN-04

## 已验证现状与根因摘要

- 当前生产数据库已有 `qwen_text_chat`、TTS、ASR 的真实成功运行锁，证明安装级全局 Qwen Key 可以被生产组合使用；这不等于所有公开功能都已经真实接线。
- 普通聊天和现代图片、视频、语音主链已有真实模型调用与部分运行锁；Humanizer、Career、Profile 的 Qwen 分支、知识库 OCR/Embedding 以及部分媒体边缘动作存在“真实调用但锁丢失”的缺口。
- 旧 Expression 写入口曾登记为模型能力，但生产没有可用 adapter，正文仍可由确定性生成器形成成功结果；测试环境自动补 Stub 会掩盖该生产缺口。
- 生产注册表、ADR 与固定模型常量存在漂移，部分模型 ID 散落在组合根中。只检查聊天模型不能证明结构化生成、画像、视觉、OCR 等能力符合已确认矩阵。
- DDG、arXiv 抓取、本地画像规则、账户与资料管理等能力本来就不应消费 Qwen Key。真实性门不能用“所有功能都必须调用模型”这种错误规则，而要验证每项公开能力的声明与实际执行一致。
- 现有真实提供方 smoke 依赖显式注入全局 Key，普通测试环境会跳过；当前没有一份发布证据能把“公开功能清单、生产接线、固定模型、真实提供方响应、持久化调用锁”闭合到同一次受控运行。

## What to build

建立一条 production-composition 发布门：以一份机器可校验的公开能力清单为入口，将每项用户可达能力唯一归类为 `qwen_model`、`external_non_qwen`、`local_deterministic` 或 `retired`，再根据类别执行不同真实性检查。

对于 `qwen_model`，门禁必须证明生产使用安装级全局 Key 绑定真实 adapter、模型 ID 来自批准矩阵、供应商确实返回结果，并为每次实际调用持久化不可变运行锁。对于其他三类，门禁必须证明它们不会伪装成 Qwen 成功，也不会意外读取或转发 Qwen Key。

这是一张最终 Contract issue，不替代前置缺陷修复。前置 issue 提供正确的生产能力和审计接缝，本 issue 将这些局部保证组合成一次可重复、失败关闭、可供发布负责人复核的全功能证据。

## 功能分类基线

以下分类是本 issue 的最低覆盖面；实现时如发现新的公开路由、聊天动作或 UI 能力，必须先加入清单再决定类别，不能静默忽略：

| 用户旅程 | 预期类别 | 真实性合同 |
| --- | --- | --- |
| 普通聊天、学习模式最终正文、论文搜索成功后的引用综合 | `qwen_model` | 真实 `qwen_text_chat`，固定文本模型，消息与运行锁可追溯 |
| Humanizer 首稿与条件修订 | `qwen_model` | 每次结构化调用分别落锁，模型输出确实决定结果 |
| Career 生成与条件修复 | `qwen_model` | 每次结构化调用分别落锁，不得以解析失败形成完成态 |
| Profile 歧义信号抽取 | `qwen_model` | 真实画像 adapter 与运行锁 |
| Profile 明确信号、更正和撤回 | `local_deterministic` | 明确标记本地规则，不调用模型、不伪造锁 |
| 知识库 OCR、入库 Embedding、重建 Embedding、查询 Embedding | `qwen_model` | 固定能力、固定模型、批次或页面调用锁与业务对象关联 |
| 现代图片生成/编辑、视觉替代文本、供应商取消 | `qwen_model` | 实际供应商动作逐次落锁；纯本地读取不造锁 |
| 现代视频生成与供应商取消 | `qwen_model` | Wan 调用逐次落锁；本地状态投影不造锁 |
| ASR 与 TTS | `qwen_model` | 真实语音 adapter、固定模型及运行锁 |
| DuckDuckGo 通用网页搜索 | `external_non_qwen` | 只访问 DDG；失败后才允许另起真实聊天模型降级调用 |
| arXiv 检索与论文卡片字段整理 | `external_non_qwen` | 检索 worker 不继承 Qwen Key；最终自然语言综合另计聊天模型调用 |
| 登录、账户、会话列表、画像编辑、材料 CRUD、关键词检索等本地操作 | `local_deterministic` | 不应产生 Qwen 调用或运行锁 |
| 旧 Expression 与旧 Media 写入口 | `retired` | 稳定返回 410，不注册伪模型能力，不执行确定性假成功 |

## 实施边界

包含：

- 一份版本化、机器可读的生产能力清单，覆盖公开 API、聊天动作和与 UI 对应的生产服务。
- 生产组合检查：所有 `qwen_model` 能力有真实 adapter；所有 `retired` 能力不再注册；生产禁止 Stub、fixture、cassette 和确定性模型替身。
- 固定模型矩阵检查：活跃能力只能引用唯一模型常量；未知 ID、散落字面量和静默 fallback 均失败。
- 审计接缝检查：每个真实 Qwen/百炼供应商调用都必须返回并持久化运行锁；一次业务操作中的多次调用不得折叠成一条假锁。
- 静态与组合检查：禁止业务服务绕开批准 adapter/recorder 直接调用 Qwen 客户端；如仍有过渡例外，门禁必须失败并点名，批准例外清单最终为空。
- 受控 live suite：使用同一个安装级全局 Key 对各类活跃模型能力执行最小真实探针，并在重启后复查数据库证据。
- 脱敏报告与非零退出码，可直接接入 `scripts/release_gate.py --real-probes` 或等价正式发布命令。

不包含：

- 不把 Qwen Key 改成账号级凭据；用户已确认继续使用安装级全局 Key。
- 不要求 DDG、arXiv、本地规则或 CRUD 功能调用 Qwen。
- 不在这张 issue 中重新实现 01、03、07–16 的业务修复。
- 不恢复已决定退役的 Expression/Media 写入口。
- 不读取、打印、哈希、回显或持久化 Key 内容。
- 不把 mock、HTTP 200、非空模板字符串或预置 cassette 当成真实供应商成功。
- 不为了让门禁变绿而静默切换模型、跳过失败能力或降低能力分类。

## Acceptance criteria

- [ ] 能力清单覆盖所有公开生产 API、聊天工具动作和对应 UI 用户旅程；发现未分类能力时门禁失败并输出稳定标识。
- [ ] 每项能力恰好属于 `qwen_model`、`external_non_qwen`、`local_deterministic`、`retired` 之一，重复分类或缺失分类均失败。
- [ ] 生产组合中的每个 `qwen_model` 能力均绑定真实 adapter；出现 Stub、fixture、cassette、确定性生成器或 `no_adapter` 时启动/门禁失败并点名 capability。
- [ ] 所有生产模型 ID 来自 Issue 09 固化的单一事实源，并与批准 ADR 一致；未知 ID、散落字面量和静默 fallback 均失败。
- [ ] 每个实际 Qwen/百炼调用，无论成功、失败、超时、重试还是修复，都形成独立、持久化、幂等的 `model_run_lock`。
- [ ] 每条运行锁至少可关联 capability、provider、model、status、时间、调用序号、账户、业务 run，以及适用的消息或对象；不保存 Key、prompt、响应正文或用户材料。
- [ ] 普通聊天和学习模式最终正文通过真实文本模型探针；学习联网失败时，DDG 失败投影与后续 Qwen 降级调用被记录为两个不同阶段。
- [ ] Humanizer、Career、Profile 歧义分支各完成一次真实结构化调用；多次修订/修复场景保留每条锁，业务投影指向主要锁且能找到同 run 的全部锁。
- [ ] Profile 本地分支在相同探针运行中确认模型调用数与锁数均为 0，结果明确标记为本地规则来源。
- [ ] OCR 使用含已知文字的最小样本；Embedding 完成入库和查询；两者都验证非空实际输出、固定模型、锁关联和进程重启后可查。
- [ ] ASR、TTS、图片、视觉替代文本、视频及供应商取消动作按最小成本探针真实调用并保存锁；没有可安全取消的任务时不得伪造取消成功，报告应明确失败关闭。
- [ ] DDG 与 arXiv 探针证明检索阶段不读取、不继承、不发送 Qwen Key；它们的失败不会生成模型成功锁。
- [ ] 登录、账户、画像编辑、材料 CRUD 和关键词检索的代表性探针证明不会调用模型或生成运行锁。
- [ ] 旧 Expression/Media 写入口稳定返回 410，且生产能力注册表中不存在相应模型能力或确定性假成功路径。
- [ ] live suite 显式禁用 fixture/cassette/Stub，缺少全局 Key、真实网络或供应商权限时整体状态为失败或 `inconclusive`，不得通过发布门。
- [ ] 门禁完成后重启应用，以只读查询证明本次全部模型运行锁仍存在且业务关联完整。
- [ ] 发布报告仅输出 build、capability、类别、provider、固定 model ID、status、latency、lock ID 和脱敏错误码；不包含 Key、请求头、prompt、响应正文、用户消息或材料内容。
- [ ] 任一活跃模型能力未真实接线、型号漂移、调用无锁、错误被提升为成功或应为本地的功能误调模型时，命令非零退出。

## Test plan

1. 使用假 registry 和临时数据库做能力清单完整性测试：遗漏、重复、错误类别、生产 Stub、未绑定 adapter、退役能力重新注册都必须跑红。
2. 对代码扫描/架构门构造直接 `QwenApiClient` 调用、散落模型字面量和绕过 recorder 的夹具，验证输出精确 capability 和调用点。
3. 使用可控 adapter 验证成功、失败、超时、重试、解析修复及同一业务 run 多次调用的锁数量、顺序、幂等和事务行为。
4. 对 `external_non_qwen` 和 `local_deterministic` 旅程注入模型调用 spy，断言检索/本地操作的模型调用数为 0；另行验证 DDG 失败后的聊天降级确实产生一条独立文本模型锁。
5. 对退役路由运行契约测试，断言 410、稳定错误码、中文迁移说明，且无模型/确定性正文生成副作用。
6. 在显式允许真实提供方的 production-like 环境运行完整最小 live suite；禁用网络录制和测试替身，随后重启进程并复查锁。
7. 扫描 live 报告、普通日志和测试产物，断言不存在 Key、Authorization、用户正文、OCR 文本、音频转写、图片提示词或模型完整响应。

建议回归命令（实现者可按仓库最终测试文件名等价调整，但不得缩减覆盖类别）：

```powershell
python -m pytest `
  tests/credentials/test_registry.py `
  tests/credentials/test_global_credential.py `
  tests/chat `
  tests/humanizer `
  tests/career `
  tests/profiles `
  tests/ingestion `
  tests/retrieval `
  tests/image `
  tests/video `
  tests/speech `
  tests/closeout/test_release_gate.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue17-contract
```

真实发布验收：

```powershell
python scripts/release_gate.py --real-probes --qwen-authenticity
```

## Observability & rollback

- 输出按 capability 聚合的调用次数、状态、模型 ID、延迟、锁数量和缺失关联，不记录请求/响应正文。
- 为 `unclassified_capability`、`production_stub`、`missing_adapter`、`model_matrix_drift`、`direct_client_bypass`、`missing_run_lock`、`retired_route_active` 提供稳定门禁错误码。
- live suite 必须使用专用探针业务 run，可被审计但不混入普通用户历史；测试完成后的业务样本可按既有保留策略清理，运行锁作为发布证据保留。
- 新门禁先以显式命令落地并形成一次基线证据；进入正式发布流程后不得通过环境变量静默跳过。
- 若门禁自身误报，可回滚门禁接入点，但不能回滚或删除已落库的真实运行锁，也不能以关闭真实性检查作为发布缺陷的长期解决方案。

## Blocked by

- [Issue 01：恢复真实 arXiv 搜索并建立反馈环](./01-arxiv-live-search-feedback-loop.md)
- [Issue 03：修复 DDG deadline 交接、错误保真与有限重试](./03-ddg-deadline-error-retry.md)
- [Issue 07：退役旧 Expression 写入口](./07-retire-legacy-expression-writes.md)
- [Issue 08：退役旧 Media 写入口](./08-retire-legacy-media-writes.md)
- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)
- [Issue 11：Humanizer 真实 Qwen 审计闭环](./11-humanizer-real-qwen-audit.md)
- [Issue 12：Career 真实 Qwen 审计闭环](./12-career-real-qwen-audit.md)
- [Issue 13：Profile 混合提取真实性与审计](./13-profile-hybrid-extraction-audit.md)
- [Issue 14：知识库 OCR 真实 Qwen 审计闭环](./14-knowledge-ocr-real-qwen-audit.md)
- [Issue 15：知识库 Embedding 真实 Qwen 审计闭环](./15-knowledge-embedding-real-qwen-audit.md)
- [Issue 16：补齐现代媒体边缘调用锁](./16-media-edge-model-run-locks.md)

## Comments

- 2026-08-13：用户确认使用安装级全局 Qwen Key，不改为每账号单独 Key；账户之间仍必须保持数据、上下文和运行关联隔离。
- 2026-08-13：用户确认通用网页搜索只使用 DuckDuckGo，不引入 Brave 或其他带 Key 的备用源；DDG 失败时保留真实 Qwen 模型知识安全降级。
- 2026-08-13：本 issue 是最终发布 Contract；局部修复可并行，只有依赖全部完成后才允许以此门禁给出“所有功能真实性已验证”的结论。

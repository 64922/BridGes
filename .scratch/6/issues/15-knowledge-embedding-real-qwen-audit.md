# Issue 15：统一知识库入库、重建与查询 Embedding 的真实 Qwen 审计

Status: resolved

Type: task

Priority: P0

User stories: US-QWEN-01、US-QWEN-02、US-KB-EMBED-01

## 已验证现状与根因

- `src/bridges/ingestion/embedding.py` 的 `QwenEmbeddingPort.embed` 使用安装级全局 Qwen Key 直接构造 `QwenApiClient` 并调用 `client.embeddings`；它不经过 `ModelGateway`/统一 recorder，因此真实成功或失败都没有持久化模型运行锁。
- 当前端口签名只有 `embed(account_id, texts)`。账户可用于数据隔离，却无法区分请求来自首次入库、增量索引、索引重建还是查询向量，也无法关联知识库对象、索引版本、检索 round 和批次序号。
- `src/bridges/ingestion/index.py` 在写入/重建时调用该端口，`src/bridges/retrieval/service.py` 每轮查询还会对清洗后的 query 再调用一次。两条生产路径共享模型和凭据，但都缺少逐批次审计。
- 当前实现已锁定 `text-embedding-v4`、1024 维、L2 规范化并在失败时诚实回退关键词检索；这些合同是正确基础。缺口是模型 ID 仍须由 Issue 09 单一事实源提供，并为每次真实 API 请求保存可关联、重启后可查的锁。
- 批量 `texts` 是一次供应商 API 请求，审计粒度应为“每个实际远端批次一条锁”，并记录条目数/维度等脱敏元数据；不能为批内每段文本伪造独立供应商调用，也不能把多个 API 批次折叠为一条。
- 凭据范围已经确认：安装级全局 Qwen Key。不得把 Key 复制进任务、文档或查询上下文；账户、对象和检索数据仍严格隔离。

## What to build

1. 将生产 Embedding 统一接入 Issue 09 固定能力和 Issue 10 recorder，停止由知识库业务端口绕过审计直接调用 Qwen client；若 Embedding 因 API 形态不适合通用 `ModelGateway`，也必须使用 Issue 10 批准的等价真实 adapter/recorder 接缝，不能自建第二套锁语义。
2. 扩展 Embedding 调用上下文，稳定区分 `ingestion_write`、`index_rebuild`、`retrieval_query`（以及发现的其他生产调用点），携带账户、业务 run、对象/文档或索引版本、检索 round、批次序号与调用序号。
3. 每个实际发送到 Qwen 的批次形成一条独立持久锁，记录 capability、provider、固定模型、状态、条目数、固定维度、延迟、usage/请求计量（供应商提供时）及稳定错误码；不记录输入文本、query、向量或完整响应。
4. 成功、鉴权、限流、区域、网络、响应数量不符、索引缺失、维度不符和空向量均保留真实调用锁。供应商成功但本地合同校验失败时，锁表示远端调用状态，本地索引/检索另记稳定失败原因。
5. 空输入 `texts=[]` 不发远端请求、不建锁。关键词检索、FTS、向量相似度计算和诚实降级都是本地步骤，不建模型锁。
6. 入库/重建的多批次执行按批次顺序留锁；检索 query 每轮真正向量化时留一条查询锁。重试实际再次调用时新增序号，不覆盖原失败锁。
7. 保持当前 1024 维、L2 规范化、版本切换原子性和 Embedding 失败后关键词检索的诚实说明；补锁不得写入空向量、静默更换模型或把关键词结果冒充向量结果。

## 上下文指针

- `src/bridges/ingestion/embedding.py`：`EmbeddingPort`、`QwenEmbeddingPort.embed`、`DeterministicEmbeddingPort`、维度与规范化合同。
- `src/bridges/ingestion/index.py`：`VersionedIndex._embed_or_fail`、写入与 `rebuild` 的批次调用。
- `src/bridges/ingestion/service.py`：Embedding 可用性、摄取、重建与相关调用入口。
- `src/bridges/retrieval/service.py`：查询向量调用和关键词诚实降级。
- `src/bridges/api/main.py`、`src/bridges/runtime/executor.py`：API/worker 两处生产端口组合，必须共享同一事实源和 recorder。
- `tests/ingestion/test_ingestion_service.py`、`tests/retrieval/test_retrieval_service.py`：入库、不可用、查询降级和隔离合同。

## 非目标

- 不把安装级全局 Qwen Key 改为账号级 Key，也不读取、打印、哈希、回显或持久化 Key。
- 不修改向量维度、L2 规范化、检索融合、层级配额或关键词降级策略。
- 不把每个批内文本伪装成独立供应商调用；锁粒度必须与实际 HTTP/API 请求一一对应。
- 不记录输入段落、查询词、生成向量、原始响应或可反推用户材料的信息。
- 不用确定性 Embedding、fixture、cassette、录制向量或预置数组证明真实 Qwen 调用；这些仅用于合同测试。
- 不以补锁为由额外调用 Embedding 健康探针或改变请求批大小，除非有独立验收证据且不扩大本 issue。

## Acceptance criteria

- [ ] API 与 worker 的所有生产 Embedding 调用均使用同一固定模型事实源和统一 recorder；架构测试禁止知识库业务代码直接无审计调用 `QwenApiClient.embeddings`。
- [ ] 首次入库、增量写入、索引重建和检索 query 均有稳定 operation 标签与业务上下文；新增生产调用点必须先分类后接线。
- [ ] 每个实际远端批次恰好新增一条锁；批内条目数记录为脱敏计数，锁数不等于文本段数，多个 API 批次不能折叠。
- [ ] 单轮检索成功生成 query vector 时新增一条 `retrieval_query` 锁；纯关键词路径、向量相似度计算和 Embedding 不可用后的降级不新增伪锁。
- [ ] `texts=[]` 返回空结果且远端调用增量、锁增量均为 0。
- [ ] 鉴权、限流、区域、网络、数量不符、维度不符和空向量均保存对应调用锁；本地索引不写错误/空向量，检索继续给出诚实关键词降级说明。
- [ ] 多批次入库/重建及真实重试的锁按批次/调用序号稳定排序；幂等重放记录事件不重复插入，真正重调不覆盖旧锁。
- [ ] 摄取或检索进程重启后锁仍可按账户、对象/索引版本或 retrieval round 查询；两账户之间不可见。
- [ ] 锁、日志和指标不包含 Key、Authorization、输入文本、query、向量值、用户材料、prompt 或完整响应。
- [ ] production-like 组合若使用 `DeterministicEmbeddingPort`、Stub、fixture/cassette、缺真实 adapter/recorder 或模型漂移则失败关闭，不能标记 vector ready。
- [ ] 安装级全局 Key 只由组合根注入真实端口；业务上下文与模型锁不包含凭据值或其派生标识。

## Test plan

1. 为 Embedding 接缝增加 fake adapter 合同测试：空输入、单批、多批、数量不符、维度不符、空向量、鉴权、限流和网络失败，断言实际模拟 API 请求与锁一一对应。
2. 扩展 ingestion/index 测试：首次入库、增量写入、重建、多批次和失败回滚，断言 operation、批次序号、对象/索引关联、锁数量及不写错误向量。
3. 扩展 retrieval 测试：向量成功为一条 query 锁；Embedding 失败后关键词结果仍可用且失败锁保留；完全不配置 Embedding 时无调用/无锁并显示诚实说明。
4. 临时 SQLite 测试幂等、真实重试、重启后查询和跨账户隔离；模拟远端成功后、索引提交前崩溃，锁仍存在且版本不假切换。
5. 架构测试扫描 `QwenApiClient.embeddings` 和 `QwenEmbeddingPort` 生产构造点，确保 API/worker 使用同一模型/recorder，测试替身不能进入 production composition。
6. 可选真实 smoke：仅在显式开关且已配置安装级全局 Qwen Key 时，禁用 Stub、fixture、cassette 与 record/replay，向量化一条无敏感信息的固定短句并执行一次最小入库与查询；验证 1024 维、非零规范化向量、入库/查询锁及重启后可查。不得读取或输出 Key、输入全文或向量。

建议回归命令：

```powershell
python -m pytest `
  tests/ingestion/test_ingestion_service.py `
  tests/ingestion/test_embedding_model_run_locks.py `
  tests/retrieval/test_retrieval_service.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue15-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_EMBEDDING_REAL_SMOKE='1'
python -m pytest tests/ingestion/test_embedding_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 按 operation、模型、状态统计 API 批次数、条目数、锁数、延迟、维度失败和关键词降级数；不记录文本与向量。
- 增加 `embedding_direct_client_bypass`、`embedding_missing_context`、`embedding_missing_run_lock`、`embedding_lock_persist_failed`、`embedding_batch_count_mismatch` 稳定错误码。
- 灰度期强制核对“实际远端 API 批次数 = 新增锁数”；分别监控 ingestion、rebuild、query，避免总量相等掩盖路径缺口。
- 若统一接缝导致回归，可停止向量远端调用并回退到现有关键词检索诚实降级；不得恢复无锁直连、写空向量、删除历史锁或静默更换模型。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：已验证入库/重建与查询共享真实 Qwen Embedding 端口，但当前直接 client 调用没有持久锁及业务操作上下文。
- 2026-08-13：凭据继续使用安装级全局 Qwen Key；实现、日志和测试严禁读取或泄露 Key、用户文本与向量内容。
- 2026-08-14：Issue 15 实现完成（分支 `worktree-15-knowledge-embedding-real-qwen-audit`）。
  - `qwen_embedding` 进入 Issue 09 固定矩阵（`MODEL_BY_CAPABILITY`），生产组合注册真实 `QwenEmbeddingAdapter`（`text-embedding-v4` / 1024 维 / L2，`max_attempts=1` 网关内不重试）；`QwenEmbeddingPort` 停止直连 `QwenApiClient`，改经 `ModelGateway` 调用，每次实际远端批次由网关产生不可变运行锁并立即经 Issue 10 recorder 持久化（account/run/对象/operation/批次与调用序号，含 provider、条目数、维度、规范化、usage 与实测延迟）。
  - 调用点分类接线：`ingestion_write`（文档 run/对象）、`index_rebuild`（索引版本 run/对象，多批次按批次顺序）、`retrieval_query`（round 对象，round_id 在向量化前生成，锁与轮次记录同标识）；空输入不发请求不建锁；清理后为空的查询不向量化；真实重调自动递增调用序号，旧失败锁永不覆盖；供应商成功但数量/维度/空向量不符时锁如实记录远端状态，本地另记稳定错误码（`embedding_batch_count_mismatch` 等）。
  - API 与 worker 组合根注入同一 gateway + recorder；静态架构测试扫描 `QwenApiClient.embeddings` 直连（只允许真实 adapter）、`QwenEmbeddingPort` 构造必须携带 `gateway`/`recorder`、`DeterministicEmbeddingPort` 禁入生产接线。
  - 附带修复：重建多批次 `vector_count` 未跨批累加导致 >16 分块重建必然校验失败的既有缺陷。
  - 验证：新增接缝合同测试 22 项（`tests/ingestion/test_embedding_model_run_locks.py`）、检索查询锁测试 4 项、架构测试 6 项；完整 `tests` 套件失败集与 main 一致（既有环境性失败：附件/项目层 enqueue 410 陈旧测试、同名测试文件收集冲突、start 桌面 profile 常驻挂起等）；ruff 通过，mypy 相对 main 无新增错误。
  - 已 rebase 到合并 Issue 11–14/16 后的 main（`8188860`）：与 Issue 14 的 OCR 接缝共用同一生产组合与 recorder；Issue 14 修复 enqueue 旧测试后，`test_index.py::test_rebuild_failure_keeps_previous_version_serving` 的 `failing_embed` 适配 EmbeddingPort `context` 参数（补锁签名变更的既有测试适配）。

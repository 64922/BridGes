# Issue 14：统一知识库 OCR 的真实 Qwen 页级调用与持久化锁

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-QWEN-01、US-QWEN-02、US-KB-OCR-01

## 已验证现状与根因

- `src/bridges/ingestion/ocr.py` 的 `QwenOcrPort` 确实使用安装级全局 Qwen Key 构造 `QwenApiClient`，但随后直接调用 `QwenOcrAdapter.call`，绕过 `ModelGateway` 与统一运行锁记录接缝。
- 该端口在本地构造 `_qwen_ocr_capability`，当前模型 ID、能力版本和重试策略形成另一份事实源；这会与 Issue 09 的固定生产模型矩阵漂移。
- `extract(account_id, content, media_type)` 缺少文档对象、摄取 run、页面/图片序号等上下文；其 `run_id=f"knowledge-base-ocr-{account_id}"` 会被同一账户重复使用，不能可靠证明哪一页触发了哪一次调用。
- `src/bridges/ingestion/service.py` 已有图片 OCR、失败时诚实元数据降级和解析缓存；缓存命中本来就不应再次调用模型。当前缺口是：真实请求成功或失败均没有可持久、页级关联的模型锁，缓存复用也无法从审计中与新调用区分。
- 仓库还存在 `src/bridges/science/qwen_parser.py`、`src/bridges/media/qwen_extraction.py` 等 OCR/视觉提取入口。实现前必须完成知识库用户旅程调用清单，避免只修 `QwenOcrPort` 而保留另一条绕过网关的知识库 OCR 路径。
- 凭据范围已确认：安装级全局 Qwen Key；账户 ID 负责数据与审计隔离，不用于选择或复制 Key。

## What to build

1. 将所有知识库 OCR 真实调用统一到 Issue 09 注册的 Qwen OCR/兼容视觉能力和 Issue 10 的运行记录接缝；删除或停止使用端口内自建的生产能力快照及直接 client/adapter 绕行。
2. 扩展 OCR 端口输入合同，至少携带 `account_id`、知识库对象/文档 ID、摄取 run ID、稳定页面或嵌入图片序号、媒体类型和内容哈希引用；禁止用可复用的 `knowledge-base-ocr-{account_id}` 作为唯一关联。
3. 每个真正发往 Qwen 的页面/图片请求形成一条独立、幂等、持久化锁，成功、鉴权失败、限流、区域错误、网络异常、空输出和适配器失败都需保留准确状态。
4. 锁按摄取 run 和页序号排序，可关联知识库对象与文档，但不保存图片/PDF bytes、base64、OCR prompt、OCR 文本或 Key。内容哈希若需关联，只能使用既有对象级不可逆标识，不新增可反推材料的日志字段。
5. 解析缓存命中时不得调用 Qwen、不得新建模型锁；缓存投影应标记 `cache_hit` 并可引用原始 OCR 运行证据或解析版本，而不是伪造“本轮模型成功”。缓存失效后重新识别则产生新的锁。
6. 保持现有诚实降级：OCR 失败时材料可以按既定元数据/未识别说明继续，但不得标成“已识别”或向量内容完整；失败锁仍要持久化并可定位到页面。
7. 若多页输入部分成功，成功页与失败页各有锁和状态，摄取投影给出脱敏的页数汇总；不得用单条汇总锁覆盖所有页面。

## 上下文指针

- `src/bridges/ingestion/ocr.py`：`QwenOcrPort`、`_qwen_ocr_capability`、当前直接 adapter 调用。
- `src/bridges/ingestion/service.py`：OCR 调用、解析缓存、失败降级和摄取状态。
- `src/bridges/science/qwen_parser.py`、`src/bridges/media/qwen_extraction.py`：需纳入调用清单的其他 OCR/视觉提取入口。
- `src/bridges/api/main.py`：生产 `QwenOcrPort` 与全局凭据组合。
- `tests/ingestion/test_ingestion_service.py`：图片 OCR、失败降级、缓存失效和相同内容复用测试。
- `tests/ai/test_qwen_vision_adapters.py`：OCR adapter 请求/响应合同。

## 非目标

- 不改变安装级全局 Qwen Key 为账号级 Key；不读取、打印、哈希、回显或持久化 Key。
- 不在本 issue 中擅自决定 OCR 最终模型 ID；兼容性 smoke 和单一模型矩阵由 Issue 09 决定，本任务只消费该事实源。
- 不把本地图片元数据解析、缓存命中、文本 PDF 解析或文件读取标记成 Qwen 调用。
- 不取消现有 OCR 失败后的诚实降级，也不以空 OCR 文本伪装材料完整就绪。
- 不把 fake OCR、fixture、cassette、预置文字或录制响应作为真实供应商验收。
- 不将原始材料、base64、OCR 文字或提示词写入模型锁和普通日志。

## Acceptance criteria

- [ ] 知识库所有生产 OCR 入口均通过统一注册能力与 recorder；架构测试禁止 `QwenOcrAdapter.call`/`QwenApiClient` 从知识库业务代码直接调用。
- [ ] OCR 模型 ID、区域、能力版本和重试政策只来自 Issue 09 单一事实源；端口内无生产硬编码副本或静默 fallback。
- [ ] 单页/单图真实 OCR 请求恰好新增一条锁，关联账户、知识库对象、文档、摄取 run、页/图序号、调用序号和固定模型。
- [ ] 多页请求每个实际供应商调用一条锁；部分失败时锁数量等于实际请求数，各页状态独立，重启后均可查。
- [ ] 鉴权、限流、区域、网络、空输出和 adapter 错误均保存失败锁；上层仍按既有合同诚实降级，不把失败页标成 OCR 成功。
- [ ] 相同内容解析缓存命中时 Qwen 调用增量和模型锁增量均为 0，并标明缓存来源；解析版本失效后的重新识别新增锁，不覆盖原锁。
- [ ] recorder 重复提交同一个 page-call 锁幂等；worker 真正重试供应商请求时使用新调用序号并保留旧失败锁。
- [ ] 锁、日志和指标不包含 Key、Authorization、文件名中的敏感正文、原始 bytes、base64、OCR prompt、OCR 文本或完整响应。
- [ ] production-like 环境出现 Stub、fixture/cassette、缺少真实 adapter 或模型矩阵漂移时 OCR 能力失败关闭并进入诚实降级，不能产出伪锁。
- [ ] 两账户上传相同文件时，缓存与锁的访问严格按账户策略隔离；共享全局 Key 不得造成对象、页或 OCR 结果串户。

## Test plan

1. 扩展 ingestion fake OCR 合同：单图成功、失败、空输出、多页部分失败和 worker 重试，断言实际 adapter 调用数与页级锁数一一对应、阶段/序号及业务关联正确。
2. 扩展已有缓存测试：相同内容复用断言第二次 0 调用/0 新锁；解析版本失效断言重新调用并新增锁；原锁不被覆盖。
3. 临时 SQLite 测试页级锁的幂等、部分成功、跨重启查询和跨账户隔离；模拟 OCR 返回后、解析结果保存前崩溃，锁仍存在。
4. 架构测试扫描知识库 OCR 入口，禁止直接构造生产 `CapabilityRecord`、`QwenApiClient` 或调用 adapter；允许测试目录中的 fake。
5. 注入 recorder 写失败，验证 Issue 10 的失败关闭/诚实降级合同，不得形成“已 OCR”投影。
6. 可选真实 smoke：显式开关且环境已配置安装级全局 Qwen Key 时，禁用 Stub、fixture、cassette 和录制，用一张含已知短中文的最小测试图调用真实 OCR；验证非空实际输出、固定模型、1 条页级锁和重启后可查。测试代码只判断凭据是否配置，不得读取或输出 Key、图片 base64 或完整 OCR 文本。

建议回归命令：

```powershell
python -m pytest `
  tests/ai/test_qwen_vision_adapters.py `
  tests/ingestion/test_ingestion_service.py `
  tests/ingestion/test_ocr_model_run_locks.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue14-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_OCR_REAL_SMOKE='1'
python -m pytest tests/ingestion/test_ocr_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 仅按文档/摄取 run 统计请求页数、成功页数、失败页数、缓存命中数、模型 ID、锁数、延迟和稳定错误码；不采集 OCR 内容。
- 增加 `ocr_direct_client_bypass`、`ocr_missing_page_context`、`ocr_missing_run_lock`、`ocr_lock_persist_failed`、`ocr_page_count_mismatch` 稳定错误码。
- 灰度期核对“实际远端页请求数 = 新增页级锁数”；缓存命中单独统计且两者增量必须为 0。
- 如统一接缝出现回归，可停止 OCR 远端调用并回到现有“未识别文字”的诚实降级；不得恢复无锁直连、删除旧锁或将元数据降级显示为 OCR 成功。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：已验证知识库 OCR 当前真实直连 Qwen adapter，但绕过模型网关且没有对象/页级持久锁。
- 2026-08-13：凭据使用安装级全局 Qwen Key；任何实现与测试严禁读取或泄露 Key、原始材料及完整 OCR 文本。

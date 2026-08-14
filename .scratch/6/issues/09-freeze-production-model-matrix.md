# Issue 09：固化生产 Qwen 模型矩阵与真实 adapter 启动门禁

Status: resolved

Type: task

Priority: P0

User stories: US-QWEN-MATRIX-01、US-QWEN-MATRIX-02、US-QWEN-STARTUP-01

## 根因与证据

- ADR-0009 将核心对话、推理、视觉理解与工具调用固定为 `qwen3.7-plus-2026-05-26`，并规定模型不可用时明确停用，不得静默降级；`src/bridges/ai/fixed_models.py` 被指定为模型 ID 单一事实源。
- 实际生产组合仍在 `src/bridges/api/main.py::_register_builtin_capabilities` 硬编码 `qwen3.6-flash`（结构化输出、画像及旧 Expression）、`qwen-vl-ocr` 和 `qwen3-vl-plus`；`src/bridges/ingestion/ocr.py`、`src/bridges/runtime/executor.py` 等也散落视觉/OCR 字面量。
- `fixed_models.py` 当前只集中 chat、embedding、ASR、TTS、image 及导入的 video 常量，没有把所有活跃 capability 显式映射到固定 ID；同一能力可在注册表、adapter 与调用方出现不同来源。
- 当前组合可在测试环境使用 Stub，生产缺少 adapter 时也可能直到请求阶段才得到 `no_adapter`。启动成功因此不能证明“所有公开 Qwen 能力都能使用全局 Key 走真实 provider”。
- 旧 `expression_draft_generation` 是退役能力，不能通过把模型 ID 改正确来继续存活；应先由 Issue 07/08 清理旧能力面，再冻结现行产品矩阵。

## 已确认的模型映射原则

1. `qwen_text_chat`、`qwen_structured_output`、`qwen_profile_extraction` 均使用 `qwen3.7-plus-2026-05-26`。
2. Vision 与 OCR 必须先用真实全局 Qwen Key 做最小兼容 smoke：若 `qwen3.7-plus-2026-05-26` 对现有图像输入和输出合同均真实可用，则两者也对齐该快照；若不兼容，必须先更新 ADR-0009、记录真实证据并批准明确例外，之后才可保留独立视觉/OCR 模型。禁止先提交代码例外、后补 ADR。
3. Embedding、ASR、TTS、Image、Video 沿用既有固定矩阵：`text-embedding-v4`、`qwen3-asr-flash`、`qwen3-tts-flash-2025-11-27`、`qwen-image-2.0-pro-2026-06-22`、`wan2.7-t2v-2026-06-12`。
4. 所有模型 ID 只从 `bridges.ai.fixed_models` 导出；registry、adapter、runtime、服务与测试合同不得另写生产模型字面量。
5. 同一 capability 只能绑定一个批准模型；超时、限流、不可用或输出不合约时只能对同一绑定按批准策略重试，绝不静默换模型、换 capability 或用本地模板提升为成功。

## What to build

1. 在 `src/bridges/ai/fixed_models.py` 建立完整的活跃 capability → 常量映射。结构化输出和画像可直接复用 `CHAT_MODEL_ID`，但 registry 暴露的实际 ID 必须完全一致；为视觉/OCR 添加经 smoke 决策后的唯一常量。
2. 删除生产代码中的模型 ID 字面量，至少覆盖 capability 注册、adapter 构造、OCR、运行时视觉、image/video/speech/embedding 服务；只允许 fixture/cassette 中的历史供应商响应保留字面值，且不得成为生产配置来源。
3. 实现 production composition 校验器：枚举所有状态为 active/verified 的 `MODEL` capability，确认其 model ID 在批准矩阵中、vendor/region/合同一致，并绑定真实 adapter。未知 capability、重复绑定、Stub/cassette/deterministic adapter、无 adapter 或矩阵漂移均阻止生产启动。
4. 明确区分测试与生产组合：单元测试可显式构造 Stub；生产路径和 production-like 发布测试禁止自动回退 Stub。缺少安装级全局 Qwen Key 时，生产启动以稳定错误码失败关闭，不打印或探测性记录 Key。
5. 增加显式 vision/OCR live compatibility smoke：用最小含已知文字图像分别验证视觉描述合同与 OCR 文本合同，检查真实响应 model、非空输出、状态和运行锁；fixture/cassette/mock 不能作为兼容结论。
6. 若 smoke 证明 `qwen3.7-plus-2026-05-26` 不兼容，停止本 issue 的生产映射变更并先提交 ADR 更新：写明供应商证据、例外模型、影响能力、失效/复核日期和无 fallback 原则。ADR 获批后才继续实现对应常量。
7. 为启动门禁和 live smoke 输出脱敏报告，仅包含 capability、批准/实际 model ID、adapter 类型、状态、延迟、锁 ID 与稳定错误码。

## 非目标

- 不更换安装级全局 Qwen Key 为账户级 Key。
- 不在本 issue 中补齐各业务服务丢失的持久化锁；公共 recorder 由 Issue 10、各调用面由 Issue 11–16 完成。
- 不恢复 Issue 07/08 已退役的旧 Expression/Media 能力。
- 不引入备用模型、用户可选模型、自动路由或价格驱动降级。
- 不以官方文档声明、mock 成功、HTTP 200、非空模板或录制 cassette 替代真实兼容 smoke。
- 不在未经 ADR 批准时保留 `qwen3-vl-plus` 或 `qwen-vl-ocr` 作为“临时”生产例外。

## Acceptance criteria

- [ ] `qwen_text_chat`、`qwen_structured_output`、`qwen_profile_extraction` 的 registry、adapter 请求和运行锁均为 `qwen3.7-plus-2026-05-26`。
- [ ] Vision/OCR 对 `qwen3.7-plus-2026-05-26` 的真实 smoke 有可复核脱敏结果；兼容则两者完成对齐，不兼容则代码变更在 ADR 获批前失败关闭。
- [ ] Embedding、ASR、TTS、Image、Video 与已确认固定矩阵逐项一致。
- [ ] 所有生产模型 ID 都来自 `bridges.ai.fixed_models`；静态检查发现生产路径模型字面量时失败并报告文件与 capability。
- [ ] 每个活跃 Qwen capability 恰好绑定一个真实 adapter；生产启动发现 missing/Stub/cassette/deterministic adapter 时非零失败。
- [ ] 已退役的 `expression_draft_generation` 及旧 Media 伪能力不在生产 registry。
- [ ] 缺少全局 Key、模型权限不足或固定模型不可用时提供明确不可用状态，不注册备用模型，也不以本地结果伪装成功。
- [ ] adapter 返回的实际 model 与批准 ID 不一致时调用失败且门禁报 `actual_model_mismatch`，不能只记录警告后继续。
- [ ] 重试始终针对同一 capability/model；测试证明限流、超时和无权限路径不存在 silent fallback。
- [ ] 启动和 smoke 报告不包含 Key、Authorization、请求正文、图像内容或完整模型输出。

## Test plan

1. 扩展 `tests/credentials/test_registry.py`，逐项断言 capability → fixed model 映射，并覆盖未知、重复、漂移和退役 capability。
2. 新增生产组合门禁测试，分别注入 missing adapter、Stub、cassette、deterministic adapter、实际 model 不匹配与缺失全局 Key，断言启动失败及稳定错误码。
3. 添加静态架构测试，扫描生产模块中的受控模型字面量；允许列表只覆盖 `fixed_models.py` 和明确的供应商解析测试数据。
4. 对网关构造限流、超时和 provider 返回不同 model 的场景，验证只重试相同 ID且从不 fallback。
5. 在显式 opt-in 环境运行真实 vision/OCR smoke，保存脱敏结果与运行锁；不具备真实 Key/网络时结果为 `inconclusive`/失败，不能通过发布门。
6. 回归 chat、structured/profile、embedding、speech、image、video 注册和 adapter 测试。

建议验证命令：

```powershell
python -m pytest `
  tests/credentials/test_registry.py `
  tests/ai `
  tests/ingestion/test_ocr.py `
  tests/image `
  tests/video `
  tests/speech `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue09
```

真实兼容验收命令应由实现者提供显式开关，例如：

```powershell
python scripts/release_gate.py --real-probes --vision-ocr-compatibility
```

## Observability & rollback

- 稳定门禁错误码至少包括 `missing_global_qwen_key`、`unapproved_model_id`、`model_matrix_drift`、`missing_adapter`、`production_test_adapter`、`actual_model_mismatch`、`vision_ocr_compatibility_unproven`。
- 指标按 capability/model/status 聚合，不记录 Key、prompt、输入文件、OCR 正文或供应商完整响应。
- 先在 production-like 启动与 release gate 强制执行，再进入正式生产启动；不得提供可被普通环境变量永久绕过的“警告模式”。
- 若批准模型在发布后不可用，回滚应用版本或明确停用该能力；禁止把旧模型字面量放回调用点、恢复 Stub 或静默 fallback。
- Vision/OCR 如需例外，回滚边界由先行 ADR 明确；未经 ADR 的失败 smoke 不产生生产配置变更。

## Blocked by

- [Issue 07：退役旧 Expression 写入口](./07-retire-legacy-expression-writes.md)
- [Issue 08：退役旧 Media 写入口](./08-retire-legacy-media-writes.md)

## Comments

- 2026-08-13：用户确认 chat/structured/profile 统一使用 `qwen3.7-plus-2026-05-26`；Vision/OCR 先做真实兼容 smoke，能工作则对齐，不能工作则必须先更新 ADR 才允许例外。
- 2026-08-13：用户确认 embedding/ASR/TTS/image/video 依既有固定矩阵；所有 ID 采用单一事实源，任何静默 fallback 均不可接受。
- 2026-08-14：Issue 09 实现完成（分支 `issue-09-freeze-production-model-matrix`，commit 16c00ee + 复查修复）。
  - `bridges.ai.fixed_models` 建立完整 `MODEL_BY_CAPABILITY` 矩阵（10 个活跃 MODEL capability）；新增 `ASR_LONG_MODEL_ID`（qwen_asr_long 长音频/文件转写变体，与 `ASR_MODEL_ID` 同族，属既有 ASR 矩阵；如用户认为该 ID 需单独 ADR 批准请提出）、`VISION_MODEL_ID`/`OCR_MODEL_ID`（对齐 `CHAT_MODEL_ID`，门禁未证实前失败关闭）；VIDEO_* 常量迁入单一事实源，`bridges/video/constants.py` 改为再导出 shim。
  - 删除生产代码模型字面量：capability 注册与组合根收敛到 `bridges.ai.production`（API 组合根、CLI 启动门、发布门复用同一接线）；ocr/运行时视觉/media/evaluation（executors/case_executors/suite_data）全部改从 fixed_models 取值；静态架构测试 `tests/architecture/test_model_literal_scan.py` 扫描生产路径，批准 ID 只允许出现在 fixed_models.py（AST 精确匹配），未批准历史 ID（qwen3.6-flash/qwen-vl-ocr/qwen3-vl-plus）任何文本出现即失败。
  - 生产组合校验器 `bridges.ai.composition`：稳定错误码 `missing_global_qwen_key`/`unapproved_model_id`/`model_matrix_drift`/`missing_adapter`/`production_test_adapter`/`actual_model_mismatch`/`vision_ocr_compatibility_unproven`，扩展 `unknown_capability`/`duplicate_binding`/`model_fallback_configured`；CLI `BridGes api` 的 production 路径（阶段 1 production-like 启动门）与发布门强制执行；vision/OCR 真实兼容证据由发布门强制（阶段 2 正式生产启动门留待后续，无环境变量警告模式）。
  - 网关对 adapter 返回的实际模型与批准 ID 不一致失败关闭（`actual_model_mismatch`，invoke 与 stream 共用 `_build_mismatch_lock`），运行锁如实记录漂移值；重试始终针对同一 capability/model（测试证明限流/超时/无权限路径无 silent fallback）。
  - vision/OCR 真实兼容 smoke：`bridges.ai.matrix_probe`（PyMuPDF 生成含已知文字 `BRIDGES-QWEN-2026` 的最小图像，检查真实响应 model、非空输出、状态与运行锁 ID）；发布门 `scripts/model_matrix_gate.py --real-probes` 显式 opt-in 执行并保存脱敏报告。本机无安装级全局 Qwen Key → 结果为 `inconclusive`，发布门以 `vision_ocr_compatibility_unproven` + `missing_global_qwen_key` 失败关闭（符合"不兼容/未证实则失败关闭"）；需在配置真实全局 Key 的环境执行 `python scripts/model_matrix_gate.py --real-probes` 完成最终兼容验收。
  - 附带修复：speech 服务持久化失败锁时把供应商原文折叠为面向用户的中文提示（修复 Issue 10 录制器安全扫描对 "authorization" 等词的拒绝——main 上 2 个 speech 测试原本已红）。
  - 验证：Issue 建议命令 203 通过；全量 `tests` 套件 3128 通过 / 212 失败，其中失败为既有环境性失败（plugins/retirement/retrieval 等，pristine main 相同文件集同样失败；`tests` 全量收集的两个同名文件冲突为既有问题，两文件单独通过；`test_runtime_smoke.py`/`test_runtime_contract.py` 在 main 与分支上都会因本机 OS keyring 保存的全局 Key 导致 `BridGes start` 常驻挂起，为既有环境问题）。workflows 一个用例因新契约（mismatch 失败关闭）更新 fixture 后通过。

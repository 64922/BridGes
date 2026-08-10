# 04 — 把 Qwen OCR 接入知识库图片摄取管线
Status: ready-for-agent
Blocked by: 无
Covered requirements: 三次改进#2

## 背景与根因

用户现象：上传到知识库的截图类材料（题目照片、板书、文献截图）完全无法按内容检索；即使被命中，"原文片段"也只是一段合成元数据。

根因：图片解析器只产出元数据文本。`src/bridges/ingestion/parsers.py:348-363` `_parse_image` 生成的归一文本是 `"图片：{文件名}\n类型：…\n尺寸：…\n大小：…"`，该文本被 FTS/向量正常索引，所以命中的是文件名而不是图片内容；文件头注释（`parsers.py:5-6`）明确"不引入独立 OCR 模型"。

关键事实：仓库里已存在 Qwen OCR/视觉能力，只是知识库摄取管线没有接线——

- 能力注册：`src/bridges/api/main.py:270-283` 注册 `qwen_ocr`（`model_id="qwen-vl-ocr"`，`:278`；状态 VERIFIED）；适配器 `QwenOcrAdapter`（`src/bridges/ai/qwen_vision_adapters.py:31`）在 `api/main.py:997-999` 注册。
- 抽取实现：`src/bridges/media/qwen_extraction.py:103` `QwenOcrExtractor`（OCR prompt 在 `:85-88`；经 `gateway.invoke("qwen_ocr", "1", ...)` 调用，`:159-172`；失败抛 `ExtractionError`，`:174-176`）。media 管线在有 ModelGateway 时对图片选用它、无网关时回退确定性抽取（`src/bridges/media/service.py:152-188`，回退在 `:162-163`）。
- 未接线的一侧：知识库摄取链路 `_process_document`（`src/bridges/ingestion/service.py:746-803`）→ `_parse_with_cache`（`:825-856`，按 account_id+content_hash+`parser_version` 缓存，版本比对在 `:840`）→ `parse_document`（`:845`）→ 空文本标记 empty（`:775-777`、`:1037-1044`）→ 分块/向量化（Embedding 失败诚实降级，`:788-800`）。图片解析器版本 `IMAGE_PARSER_VERSION = "image-metadata-v1"`（`parsers.py:112`）。全链路无任何 OCR 调用。

## What to build

把已有 Qwen OCR 能力接入摄取管线的图片分支，使图片内容可被检索：

- **OCR 接入**：图片材料摄取时调用 `qwen_ocr`（qwen-vl-ocr）抽取可见文字，OCR 文本与既有元数据一起进入归一文本，经既有分块（`src/bridges/ingestion/chunker.py:18-22`）、FTS 与向量索引后可被检索命中。
- **失败降级**：OCR 不可用（无全局 Key / 端口未构造）或调用失败时，回退到现有元数据文本，并在解析文本中如实标记（如"图片内容未做文字识别"）；不阻断摄取、不标记 error、不伪装已识别。
- **隐私边界**：单机本地处理，最小披露——仅把该图片字节经既有全局百炼通道送 OCR（与 Embedding 同一披露面，`src/bridges/ingestion/embedding.py:52-53` 同为全局百炼运行凭据），不附带其他材料、画像或对话内容；production 禁止 cassette 落盘，沿用既有 record_mode 语义（`api/main.py:1092-1099`、`src/bridges/runtime/executor.py:144-149`）。
- **凭证不可用时**：与 Embedding 相同的诚实降级语义——端口为 None 即跳过 OCR 走元数据回退（对齐 `_embedding_availability`，`ingestion/service.py:289-296`），缺 Key 不得使材料进入 error。

## Implementation notes

- `src/bridges/ingestion/parsers.py`
  - `_parse_image`（`:348-363`）扩展为 OCR 感知：OCR 文本以参数注入后并入归一文本（元数据保留在前，OCR 文本在后；无 OCR 文本时追加如实标记行）。保持解析器纯函数风格——不在 parsers 内发起网络调用，网络调用放 service 层。
  - bump `IMAGE_PARSER_VERSION`（`:112`，如 `image-ocr-v1`）：解析缓存按版本失效（`ingestion/service.py:834-844`），旧元数据缓存自动重解析。
  - 更新文件头 `:5-6`"不引入独立 OCR 模型"的注释，使其与新行为一致。
- `src/bridges/ingestion/service.py`
  - 仿照 `embedding: EmbeddingPort | None = None`（`:160`）注入可选图片 OCR 端口（协议参照 `EmbeddingPort`，`src/bridges/ingestion/embedding.py:45-49`；测试注入确定性假端口）。
  - `_process_document`（`:746-803`）图片分支：对象字节与文件名在场（`:756-764`），有端口则先 OCR：成功 → OCR 文本 + 元数据；调用失败或无端口 → 元数据回退 + 如实标记行。失败语义对齐 Embedding 的诚实降级（`:788-800`）：不阻断、不伪装。
- OCR 端口生产实现（复用既有栈，不引入新供应商）
  - payload 约定参照 `QwenOcrExtractor`：`image_base64`/`mime_type`/`prompt`/`temperature=0.01`/`max_tokens=4096`（`src/bridges/media/qwen_extraction.py:159-172`），prompt 复用或改写 `OCR_IMAGE_PROMPT`（`:85-88`）。
  - 若经 ModelGateway 调用，注意 `build_run_context_for_ocr`（`:73-82`）以 `SourceAsset` 为入参，ingestion 侧无此对象，需构造最小 `RunContextEnvelope` 或绕开该辅助函数；也可在 ingestion 侧实现一个薄端口直接包装 `QwenApiClient` + `qwen_ocr` 适配器约定（`src/bridges/ai/qwen_vision_adapters.py:31-69`）。
- 构造接线两处（缺一不可，worker 才是实际执行解析的进程）
  - `src/bridges/api/main.py`：`IngestionService` 构造点 `:1101-1105` 旁，与 `QwenEmbeddingPort`（`:1081-1100`）同一全局 settings 来源；API 进程 Key 可缺，端口构造须容忍 None。
  - `src/bridges/runtime/executor.py`：`:150-163` 构造区；worker 启动有 GQ-01 全局 Key 硬门（`src/bridges/cli/main.py:690-692`），但端口构造仍按"可 None、调用时诚实降级"实现，与 API 进程同语义。
- 前端与契约：优先复用"解析文本内标记行"方案——标记随分块与引用片段自然透出（注入对话的片段截断 160 字符，`src/bridges/chat/turn.py:539`、`:605-606`），无 API 形态变化则不动 OpenAPI 与前端。
- 测试
  - `tests/ingestion/test_parsers.py`：OCR 文本并入归一文本、无 OCR 时的标记行、版本号 bump。
  - `tests/ingestion/test_ingestion_service.py`：注入假 OCR 端口覆盖三路径——成功（图片内容词可入索引）、调用失败（回退元数据 + 标记、材料仍 ready）、端口 None（同上）；缓存版本失效后重解析；同内容重复摄取幂等。构造注入模式参考 `src/bridges/evaluation/executors.py:521-522` 的既有做法。
  - 回归 `tests/knowledge_base/`、`tests/retrieval/`、`tests/learning/`。

## Acceptance criteria

- [ ] 上传含文字图片（如题目照片），worker 处理后材料 ready；用图片中的文字（非文件名）提问能命中该材料，引用片段含 OCR 文本而非仅元数据。
- [ ] OCR 不可用（无全局 Key）或调用失败：材料照常完成摄取，索引文本为现有元数据并含"未做文字识别"如实标记；不进入 error、不伪装已识别。
- [ ] `IMAGE_PARSER_VERSION` 升级后旧解析缓存按版本失效并重解析；同内容重复摄取幂等（不产生重复分块）。
- [ ] 隐私：OCR 请求仅含该图片字节与固定 prompt，不携带其他材料/对话/画像内容；production 环境 cassette 不落盘。
- [ ] 成功 / 失败 / 无 Key 三路径均有单测；`tests/ingestion/`、`tests/knowledge_base/`、`tests/retrieval/`、`tests/learning/` 回归通过。

## Verification

- pytest：`.venv/Scripts/python.exe -m pytest tests/ingestion/ tests/knowledge_base/ tests/retrieval/ tests/learning/ -q`（含新增用例）。
- `ruff check` 与 `mypy --strict` 对改动文件干净。
- 手工冒烟（须先起 worker，`src/bridges/cli/main.py:695` → `src/bridges/runtime/executor.py:388-401`，否则材料永远 queued）：上传含文字截图 → ready → 学习/日常模式提问图中文字 → 命中且片段为 OCR 文本；再以 OCR 不可用方式（缺 Key 或注入失败）重传另一张图 → 材料 ready 且标记如实呈现。
- 注意预置失败基线：`tests/chat/test_chat_attachments.py` 有 3 个用例在 HEAD 即失败（见本目录 README），不计入本 Issue 回归。
- 若 03 已落地，加验一次：仅有该图片的知识库在学习模式下如实呈现图片命中（03 的标注语义在 OCR 文本存在时不应再说"未做内容理解"——见 Comments 的协调记录）。

## Non-goals

- 不做扫描件 PDF 的页面级 OCR（`_parse_pdf` 无文本层时的渲染识别）；如需纳入另行立项。
- 不改动教学门对图片引用的语义、向量相似度阈值、检索意图门（均属 [03](./03-knowledge-base-p0-fixes.md)）。
- 不引入新的模型供应商或本地独立 OCR 模型；不做人脸/敏感内容识别。
- 不改动聊天附件管线（`src/bridges/chat/attachments.py`）与 media/science 旧管线的抽取行为。

## Blocked by

无。与 [03](./03-knowledge-base-p0-fixes.md) 无强制依赖，可并行领取；文件重叠情况见 Comments。

## Comments

- 与 03 的文件重叠：03 主要碰 `src/bridges/retrieval/decision.py`、`retrieval/search.py`、`retrieval/service.py`（阈值调用点）、`src/bridges/learning/teaching_gate.py` 与前端知识库页；本 Issue 碰 `src/bridges/ingestion/parsers.py`、`src/bridges/ingestion/service.py`、`src/bridges/api/main.py`、`src/bridges/runtime/executor.py`。两侧不共享文件，可并行（README 执行建议同此）；若 03 的阈值调用点改动意外波及 `ingestion/service.py`（例如共享常量），合并时以各自验收标准为准、不得互相回滚。
- 与 03 的语义协调：03(a) 落地后教学门对图片引用标注"图片未做内容理解"；本 Issue 交付后图片引用可能携带真实 OCR 文本。届时该标注宜按"是否存在 OCR 文本"区分——记录于此作为后续微调，不阻塞任一 Issue。
- OCR 质量提示：qwen-vl-ocr 为云端能力，识别质量随图片清晰度与版式变化；本 Issue 只保证接入、可检索与诚实降级，不承诺识别准确率指标。
- 披露边界：图片字节经全局百炼通道送出属于既有最小披露面（Embedding 已同通道送出分块文本）；本 Issue 不扩大披露范围，也不新增凭证类型。

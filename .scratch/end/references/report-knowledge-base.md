调查完成。以下是知识库模块的完整技术报告。

# BridGes 知识库模块调查报告

## 架构概述（上传 → 解析 → 索引 → 检索 → 注入对话）

```
前端知识库页 (knowledge-base-page-client.tsx)
  → POST /knowledge-base/materials (原始字节 + X-Bridges-Filename 头)
  → KnowledgeBaseService.upload() 校验/去重/存对象库
  → IngestionService.enqueue() 写 document_records(status=queued) + 任务队列
  → 【独立 worker 进程】bridges worker → BackgroundExecutor.run_tick()
  → parse_document() → chunk_document() → Qwen Embedding → VersionedIndex 写入
聊天时：
  → decide_retrieval() 意图门（可能 SKIP）
  → LayeredRetrievalService.run_round() 三层作用域（附件/项目层已退役，实际只有知识库层）
  → FTS5 trigram BM25 + 向量余弦 → RRF 融合 → 固化为 retrieval_rounds + message_citations
  → retrieval_context() 拼入模型 prompt（上限 2400 字符）
```

## 分调查点详述

### 1. 文件上传

- **API 路由**：`src/bridges/api/knowledge_base.py:49-110`（`POST /knowledge-base/materials`，非 multipart，原始字节流 + `X-Bridges-Filename` 头）；列表/详情/下载/重试/重建/删除在同文件 113-244 行。
- **前端入口**：`apps/web/src/app/(app)/(modules)/knowledge-base/knowledge-base-page-client.tsx:371-440`（XHR 上传带进度）；API client 在 `apps/web/src/lib/api.ts:1211-1343`。
- **支持类型**：`src/bridges/ingestion/service.py:61-70` `SUPPORTED_MEDIA_TYPES` = PDF、DOCX、TXT、Markdown、PNG/JPEG/GIF/WebP。服务端魔数嗅探 `src/bridges/chat/attachments.py:460-488`（`sniff_media_type`，扩展名与内容必须一致）。
- **大小限制**：10 MB，`attachments.py:30` `MAX_ATTACHMENT_BYTES`；上传路由预检 `knowledge_base.py:71-93`，服务层复检 `knowledge_base/service.py:71-74`。
- **存储位置**：对象库 `BridgesObjectRepository`（磁盘 `objects/`，元数据 `objects` 表）；同账户同名同内容幂等复用（`knowledge_base/service.py:84-90`）。
- **解析方式**：`src/bridges/ingestion/parsers.py`：
  - PDF：PyMuPDF 纯文本提取（`_parse_pdf:165-211`），**扫描件无 OCR，直接产出空文本 → 标记 `empty`**；
  - DOCX：zipfile + XML 解析（`:214-284`）；
  - TXT/MD：UTF-8 解码（`:287-345`）；
  - 图片：**只生成元数据文本**（`_parse_image:348-363`："图片：{文件名}\n类型：…\n尺寸：…\n大小：…"），文件头注释（`:5-6`）明确"不引入独立 OCR 模型"。**注意：仓库里其实存在 Qwen OCR/视觉能力（`src/bridges/media/qwen_extraction.py:85-104`），但知识库摄取管线没有接线。**

### 2. 切分与索引

- **Chunking**：`src/bridges/ingestion/chunker.py` — 段落优先，目标 900 字符（`TARGET_CHUNK_CHARS:20`），硬上限 2000（`:22`），每块带原文偏移/页码/章节 + SHA-256 内容哈希，确定性。
- **索引是混合式（FTS5 + 向量）**：`src/bridges/ingestion/index.py`
  - FTS：`fts_chunks` FTS5 虚表，**trigram 分词器**（`src/bridges/storage/database.py:316-322`）；
  - 向量：`index_vectors` 表存 `vector_json`，固定 text-embedding-v4、1024 维、L2 规范化（`src/bridges/ingestion/embedding.py:30-33`，凭据为全局百炼 key）；
  - 版本化合同（`IndexContract`），合同变化全量重建 + 原子切换 + 可回滚（`index.py:250-301, 364-411`）；
  - Embedding 不可用/调用失败时**只建 FTS、诚实降级**，不写空向量（`ingestion/service.py:788-799`）。

### 3. 检索

- **入口**：`src/bridges/retrieval/service.py:194-407` `run_round()`。三层作用域，但**附件层与项目层已退役**（`:527-528` 强制 DISABLED 并注明"已退役"），实际只有知识库层生效。
- **意图门（前置）**：`src/bridges/retrieval/decision.py:147-198` — 默认 `COMPANION_DEFAULT` **SKIP 不检索**；只有查询命中显式词（"知识库""上传的文件"等）、学习词（"解释""学习"等，仅 study 模式）或写死的科学主题词表（"热力""量子"等）才 RETRIEVE。
- **候选预筛**：材料 >8 份时按**仅文件名**匹配选候选（`service.py:75` `KNOWLEDGE_BASE_CANDIDATE_LIMIT=8`；`service.py:565-593`）。
- **检索内核**：`src/bridges/retrieval/search.py`
  - 关键词：FTS5 trigram BM25，每层抓取上限 40（`KEYWORD_FETCH_LIMIT:53`），整词无命中按 18/15/12/9/6 滑窗回退（`:57,152-167`）；
  - 向量：余弦降序 top-40（`VECTOR_FETCH_LIMIT:55`，`search_vectors:230-255`）——**没有任何相似度阈值**；
  - 融合：RRF k=60（`RRF_K:33`），知识库层配额 5（`LAYER_QUOTAS:35-39`），跨层权重 KB=1.0 最低，最终引用上限 10（`MAX_CITATIONS:47`），候选 <3 判"覆盖不足"（`MIN_COVERAGE:49`）。
- **注入对话**：`src/bridges/chat/turn.py:592-614` `retrieval_context()`，片段截 160 字符、总上下文 2400 字符上限。
- **图片为何能被命中**：图片的"解析文本"就是那段合成元数据（含文件名），被 FTS/向量正常索引。所以"巴巴博一.jpg"的"原文片段"实际是 `"图片：巴巴博一.jpg\n类型：image/jpeg\n尺寸：…"`——**命中的是文件名，不是图片内容**。

### 4. 文件管理

- 列表（最新在前）、详情（元数据 + 逐阶段状态）、下载、失败重试（重置计数）、显式重建索引、级联删除（含派生索引数据，处理中 409）：`api/knowledge_base.py` + `ingestion/service.py:348-436, 510-558`。
- 状态机：`queued/parsing/processing/ready/empty/error`，租约过期呈现 `recovery`（`ingestion/service.py:117-144`）。
- 前端：状态筛选（全部/处理中/已就绪/失败）、文件名搜索、2.5s 轮询、向量降级横幅（`knowledge-base-page-client.tsx:30-42, 310-323, 882-908`）。**无正文预览**——详情对话框明确"详情不含正文分块"（`:707-709`），只能从统一搜索页带页码锚点跳转。

### 5. 缺陷清单（含"有内容也检不出"的链路分析）

1. **图片引用被教学门整体丢弃 → "没有可用命中"**（直接解释线索）。`src/bridges/learning/teaching_gate.py:227-231`：检索轮次有 citations 但 `_local_sources()` 过滤掉全部 `image/*` 引用（`:146-149`）后为空时，强制把充足性改写为 `NO_HITS`，于是报"当前附件、项目文件和授权知识库没有可用命中。"（`:255`）。知识库里只有图片（如"巴巴博一.jpg"）时必然触发。检索层知道命中了图片，教学层却当作零命中——层间语义不一致。
2. **图片无 OCR/视觉理解，"命中"是文件名命中**。`parsers.py:348-363` 只索引合成元数据；截图里图片被命中为"原文片段"正是这段元数据。用户上传截图类知识（题目照片、板书）完全无法按内容检索。仓库已有 `qwen_ocr` 能力（`media/qwen_extraction.py`）但未接入摄取管线。
3. **意图门默认 SKIP，检索根本不发生**。`decision.py:195-198`：日常模式下不含词表关键词的问题（例如直接问"巴巴博一是什么"——不含"学习/解释/知识库"也不含写死的科学词）直接 SKIP，知识库有内容也完全不查。词表是硬编码的，覆盖面窄。
4. **向量检索无相似度阈值**。`search.py:230-255` 余弦降序直接取 top-40，只要账户有任何向量，再无关的分块也会进融合候选；RRF 融合（`fuse_layer:258-296`）同样无下限。结果是"必出引用"——噪声引用与幻觉来源风险。
5. **>8 份材料时候选预筛只看文件名**。`service.py:565-593`：内容相关但文件名不含查询词的文档在片段检索前就被淘汰；且整查询子串评分（`int(normalized and normalized in filename)`，`:580`）对长查询几乎不成立，退化为按上传顺序取前 8。
6. **摄取依赖独立 worker 进程，worker 不跑则永远 queued**。生产拓扑是 `bridges worker`（`src/bridges/cli/main.py:695` → `runtime/executor.py:388-401` → `ingestion/service.py:637-656`）。worker 未启动时材料停在 queued，检索层显示"材料正在处理或建立索引"（`service.py:780-781`），教学门同样报无可用命中。本地开发极易踩到。
7. **前后端扩展名不一致：`.markdown` 前端放行、后端拒绝**。前端 `ACCEPT_ATTRIBUTE`/`SUPPORTED_EXTENSION` 含 `.markdown`（`knowledge-base-page-client.tsx:24-25`），后端 `_EXTENSION_TYPES` 只有 `.md`（`attachments.py:33-47`），上传 `.markdown` 会得到"文件类型与扩展名不匹配"。
8. **扫描件 PDF 静默变 `empty`**。`parsers.py:192-201` 无文本层不报错而是标记 empty，前端把 `empty` 归入"已就绪"桶（`knowledge-base-page-client.tsx:40`），用户以为可检索实则无任何分块。
9. **小缺陷：候选预筛代码冗余**。`service.py:586-592` `selected` 被无条件赋值后又按 `matched` 重算，逻辑可读性差（功能正确但易引入回归）。
10. **设计权衡（非缺陷但值得知道）**：检索轮次/引用固化后即使材料删除也保留展示（打开时实时校验授权，`service.py:836-922`）；旧索引版本 obsolete 后不自动清理，sqlite 体积随重建增长。

### 6. 测试覆盖

- **知识库**：`tests/knowledge_base/` — `test_knowledge_base_service.py`（12 个用例：上传/列表/详情、不支持类型拒绝、级联删除、重试幂等、重建产生新版本、处理中 409 冲突含 TOCTOU、账户隔离、附件不可经 KB 路径篡改、对象删除失败映射 503）、`test_knowledge_base_api.py`（268 行 API 层）、`test_schema_v8.py`。
- **摄取**：`tests/ingestion/` — `test_parsers.py`（222 行）、`test_ingestion_service.py`（429 行）、`test_index.py`（279 行版本化合同）、`test_executor.py`、`test_api.py`、`test_issue11_global_knowledge_base.py`。
- **检索**：`tests/retrieval/` — `test_retrieval_service.py`（22 个用例：三层作用域、关闭 KB 开关、跨账户隔离、无命中结构化、冲突信号、覆盖不足、索引不可用、引用授权 404、重建后轮次不漂移、多词查询、单层失败传导）、`test_search.py`、`test_decision.py`、`test_schema_v10.py`。
- **E2E**：`apps/web/e2e/issue18-knowledge-base.spec.ts`；视觉基线快照若干。
- **明显缺口**：无图片材料检索行为测试（无 OCR 路径、教学门丢弃图片的场景只在 `tests/learning/test_teaching_gate.py` 间接覆盖）；无 >8 材料候选预筛测试；无向量阈值/噪声引用测试。

**最可能的"检不出"根因排序**：(3) 意图门 SKIP → (6) worker 未跑材料未就绪 → (1)/(2) 只有图片材料被教学门判无命中 → (5) 文件名预筛漏检。
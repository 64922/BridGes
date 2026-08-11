# 03 — 修复知识库 P0 检索与呈现缺陷（图片命中判零、日常模式不检索、向量无阈值、扫描件误标已就绪）
Status: resolved
Blocked by: 无
Covered requirements: 三次改进#2

## 背景与根因

用户现象：知识库里明明有材料却检不出（学习模式报"没有可用命中"、日常模式提问根本不查知识库）；检索又时常"必出引用"带出无关片段；扫描件 PDF 显示已就绪但永远搜不到。调查报告给出四个 P0 根因（file:line 已按当前代码复核）：

1. **教学门把 image/* 引用整体丢弃后判零命中。** `src/bridges/learning/teaching_gate.py:146-148` 的 `_local_sources()` 过滤掉全部 `image/*` 引用；检索轮次有 citations 但过滤后为空时，`:227-231` 强制把充足性改写为 `NO_HITS`，于是 `:254-255` 报"当前附件、项目文件和授权知识库没有可用命中。"。图片的索引文本本就是合成元数据（`src/bridges/ingestion/parsers.py:348-363`），检索层如实命中了图片，教学层却当作零命中——层间语义不一致。知识库仅有图片（如"巴巴博一.jpg"）时必然触发。
2. **检索意图门默认 SKIP，日常模式不检索。** `src/bridges/retrieval/decision.py:147-198`：日常陪伴模式下不含显式词（`:22-30`）、上传材料词（`:31-40`）或写死科学主题词表（`:58-71`）的查询直接落入 `:195-198` 的 `COMPANION_DEFAULT` SKIP；学习词（`:41-55`）仅在 study 模式生效（`:185-189`）。直接问"巴巴博一是什么"这类主题名词查询完全不触发检索。词表硬编码、覆盖面窄。
3. **向量检索无相似度阈值。** `src/bridges/retrieval/search.py:230-255` `search_vectors` 余弦降序直接取 top-40（`VECTOR_FETCH_LIMIT`，`:55`），`fuse_layer`（`:258-296`）同样无下限。只要账户有任何向量，再无关的分块也进融合候选，造成"必出引用"噪声与幻觉来源风险。
4. **扫描件 PDF 的 empty 被前端归入"已就绪"。** `src/bridges/ingestion/parsers.py:192-201` 对无文本层 PDF 不报错、产出空文本；摄取服务据此标记 empty（`src/bridges/ingestion/service.py:775-777`、`:1037-1044`）；前端分桶把 `ready/empty` 同归"已就绪"（`apps/web/src/app/(app)/(modules)/knowledge-base/knowledge-base-page-client.tsx:37-40`），行内芯片标签为"无可索引内容"（`apps/web/src/components/bridges/AttachmentIngestion.tsx:45-50`）。用户以为可检索，实则零分块。

调查报告的"检不出"根因排序：意图门 SKIP > worker 未跑材料未就绪 > 图片被教学门判无命中 > 文件名预筛漏检。本 Issue 覆盖其中四项 P0；其余已知缺陷在 Comments 完整备查。

## What to build

四项独立可测的修复，同一 issue 内交付：

(a) **教学门如实传导图片命中。** 检索层命中图片时不再整体丢弃判零：图片引用保留为本地证据来源并如实标注"图片未做内容理解"；只有真正零命中时才报"没有可用命中"。

(b) **检索意图门放宽。** 日常陪伴模式下也应能检索知识库：不再默认 SKIP，至少对用户显式提及资料/文件/主题名词的查询触发检索；判定规则保持确定性、可单测（decision.py 文件头 `:1-6` 明确决策层不调用语言模型，本 Issue 不破例）。

(c) **向量检索加相似度阈值。** 低于阈值的分块不进入融合，消除"必出引用"噪声；相关命中不得受影响。

(d) **扫描件 empty 如实呈现。** 知识库页把 empty 从"已就绪"桶分离，如实展示为"无法检索/无文本层"；聊天附件侧的 empty 呈现不得因此失真。

## Implementation notes

(a) 教学门 — `src/bridges/learning/teaching_gate.py`
- `:146-148` 不再无条件排除 `image/*` 引用；图片引用进入本地证据列表，标注"图片未做内容理解"（标注字段/文案与前端证据呈现对齐，保持结构化、可测试）。
- `:227-231` 移除"citations 全为图片 → 强制 NO_HITS"的改写，充足性以检索层 `retrieval.sufficiency` 为准如实传导；`:254-255` 的零命中文案只在真正无 citations 时出现。
- 同步核对 `:232` 起 `required_search` 在新充足性语义下的行为：图片命中是否仍触发公开补充由既有规则推导，不得借本 Issue 擅自扩大公开检索范围；用测试锁定。
- 测试：`tests/learning/test_teaching_gate.py` 新增"仅图片命中"场景（不报零命中、标注呈现、真零命中原文案不变）。

(b) 意图门 — `src/bridges/retrieval/decision.py`
- 改写 `:195-198` 的 `COMPANION_DEFAULT` 落点：日常模式对显式提及资料/文件/主题名词的查询返回 RETRIEVE。建议方向（不强制）：利用 `_CASUAL_TERMS`（`:73-83`，当前仅有定义、全仓库无引用）做寒暄兜底 SKIP、其余默认 RETRIEVE 的反向规则——与 (c) 的阈值配合，噪声由阈值吸收。无论采用何种规则，必须确定性、可单测，且"巴巴博一是什么"必须 RETRIEVE。
- 保留既有 SKIP 语义不变：用户关闭知识库开关（`:156-159`）、image_edit（`:164-168`）、专用能力路由（`:179-183`）。
- bump `RULES_VERSION`（`:20`，当前 `retrieval-intent/v1`）——该版本随检索轮次持久化（`src/bridges/retrieval/service.py:163`）。
- 测试：`tests/retrieval/test_decision.py` 更新/新增：日常模式主题名词查询 RETRIEVE、寒暄仍 SKIP、学习模式既有行为不回归、开关关闭仍 SKIP。

(c) 向量阈值 — `src/bridges/retrieval/search.py` + `src/bridges/retrieval/service.py`
- `search.py:230-255` `search_vectors` 增加最小相似度参数，低于阈值的分块在排序截断前剔除；阈值常量与既有常量集中放置（`:33-55` 区域，如 `VECTOR_MIN_SIMILARITY`）。向量与查询均按索引合同 L2 规范化（`src/bridges/ingestion/embedding.py:31-33`），余弦即点积，阈值语义稳定。
- 调用点 `retrieval/service.py:350` 传入阈值；`fuse_layer`（`search.py:258-296`）无需改动（输入已过滤）。
- 阈值取值用现有测试夹具校准：相关命中保持、无关查询零引用；把取值依据写进本文件 Comments。
- 测试：`tests/retrieval/test_search.py` 新增阈值过滤用例；`tests/retrieval/test_retrieval_service.py` 新增"账户有向量但查询无关 → 无引用"回归。

(d) empty 呈现 — `apps/web/src/app/(app)/(modules)/knowledge-base/knowledge-base-page-client.tsx` + `apps/web/src/components/bridges/AttachmentIngestion.tsx`
- `knowledge-base-page-client.tsx:37-40` 分桶：empty 从"已就绪"桶分离，筛选桶与行内芯片（`:598`）同步呈现"无法检索/无文本层"；详情对话框逐阶段状态（`:115` 起、`:687-708`）同步如实呈现。
- 注意 `IngestionStatusChip` 是共享组件：empty 标签"无可索引内容"（`AttachmentIngestion.tsx:45-50`）同时服务聊天附件（`:279-290`、`:385`）。优先在知识库页侧做独立呈现；若改共享组件，两处语义都必须保持诚实。
- 前端检查：`cd apps/web && npm run typecheck`（`apps/web/package.json` scripts）。

## Acceptance criteria

- [x] 知识库仅有"巴巴博一.jpg"时，学习模式证据门不再报"当前附件、项目文件和授权知识库没有可用命中。"，而是如实呈现图片命中并标注"图片未做内容理解"；真正零命中时原文案不变。
- [x] 日常陪伴模式问"巴巴博一是什么"会实际触发知识库检索（决策 action=RETRIEVE 有测试断言）；"你好/晚安"类寒暄仍 SKIP；学习模式既有触发行为不回归；用户关闭知识库开关仍 SKIP。
- [x] 向量检索低于阈值的分块不进融合：账户已有向量材料时，无关查询不再产生引用（无"必出引用"），相关查询命中保持，阈值取值依据记录在 Comments。
- [x] 扫描件 PDF 的 empty 状态在知识库页如实展示为"无法检索/无文本层"，不归入"已就绪"；聊天附件侧 empty 呈现不失真。
- [ ] 以上行为均有确定性单测锁定；`tests/learning/`、`tests/retrieval/`、`tests/knowledge_base/`、`tests/ingestion/` 回归通过。

## Verification

- pytest：`.venv/Scripts/python.exe -m pytest tests/learning/test_teaching_gate.py tests/retrieval/ tests/knowledge_base/ tests/ingestion/ -q`；新增用例随实现一并交付（教学门图片命中、意图门新规则、向量阈值、empty 呈现）。
- `ruff check` 与 `mypy --strict` 对改动文件干净；前端 `npm run typecheck`（apps/web）；知识库页 E2E 回归 `apps/web/e2e/issue18-knowledge-base.spec.ts`。
- 手工冒烟（须先起 worker，`src/bridges/cli/main.py:695` → `src/bridges/runtime/executor.py:388-401`，否则材料永远 queued）：上传一张图片 + 一份扫描件 PDF；学习模式提问确认图片命中如实呈现，日常模式问主题名词确认实际检索，知识库页确认扫描件显示"无法检索/无文本层"。
- 注意预置失败基线：`tests/chat/test_chat_attachments.py` 有 3 个用例在 HEAD 即失败（见本目录 README），不计入本 Issue 回归。

## Non-goals

- 不接入图片 OCR/视觉理解（由 [04](./04-knowledge-base-image-ocr.md) 负责）；本 Issue 只保证图片命中被如实传导与标注。
- 不调整 >8 份材料候选预筛算法、摄取对独立 worker 的依赖、旧索引版本清理策略（见 Comments 备查清单）。
- 不含 `.markdown` 扩展名对齐（已由主会话直接修复，`src/bridges/chat/attachments.py:40` 现含 `.markdown`）。
- 不改动 Embedding 诚实降级语义与索引版本合同。

## Blocked by

无。

## Comments

调查报告缺陷清单中本 Issue 范围之外的已知缺陷，完整备查：

- **#2 图片无 OCR，"命中"是文件名命中** → 由 [04](./04-knowledge-base-image-ocr.md) 负责。
- **#5 >8 份材料时候选预筛只看文件名**：`src/bridges/retrieval/service.py:565-593`，内容相关但文件名不含查询词的文档在片段检索前被淘汰；整查询子串评分（`:580`）对长查询几乎不成立，退化为按上传顺序取前 8（`KNOWLEDGE_BASE_CANDIDATE_LIMIT = 8`，`:75`）。
- **#6 摄取依赖独立 worker 进程**：`src/bridges/cli/main.py:695` → `src/bridges/runtime/executor.py:388-401` → `IngestionService.process_pending`；worker 未启动时材料永远 queued，检索层显示"材料正在处理或建立索引。"（`src/bridges/retrieval/service.py:781`），教学门同样报无可用命中。本地开发极易踩到。
- **#7 `.markdown` 前后端不一致**：前端放行（`knowledge-base-page-client.tsx:24-25`）、后端曾拒绝；已由主会话在本批 Issue 撰写期间直接修复（`src/bridges/chat/attachments.py:40` 现含 `.markdown` → `text/markdown` 映射）。仅备查，本 Issue 不涉及。
- **#9 候选预筛冗余赋值**：`src/bridges/retrieval/service.py:586-592` `selected` 被无条件赋值后又按 `matched` 重算，功能正确但可读性差、易引入回归。
- **#10 设计权衡（非缺陷）**：检索轮次/引用固化后即使材料删除也保留展示，打开时实时校验授权（`src/bridges/retrieval/service.py:836` 起 `_access_state`，知识库材料删除提示在 `:915`）；旧索引版本 obsolete 后不自动清理，sqlite 体积随重建增长。
- **测试缺口备查**：调查时无图片材料检索行为测试、无 >8 材料候选预筛测试、无向量阈值/噪声引用测试。本 Issue 补齐图片命中门用例与向量阈值用例；候选预筛测试留给后续修复 #5 时一并补齐。
- **向量阈值取值**：用现有 `DeterministicEmbeddingPort` 夹具校准为 `0.89`；无关查询“古典音乐作品分析”与“量子力学波函数坍缩。”为 `0.873436`，应过滤；既有多分块相关候选最低为 `0.890369`、冲突回归的向量顶级命中为 `0.893158`，均保留，关键词命中路径不受影响。
- 串行协调（轨道 A）：本批 01/02/03 都改 `src/bridges/learning/teaching_gate.py`（01 改门裁决与降级、02 改状态机、03 改图片引用传导），按本目录 README 轨道 A 串行执行（01 → 02 → 03）；本 Issue 以 02 合入后的 `assess`/`_local_sources` 区域为基线，rebase 重点核对 `:146-148` 与 `:227-231` 两处。
- 与 04 的并行关系：本 Issue 主要碰 `retrieval/decision.py`、`retrieval/search.py`、`retrieval/service.py`（阈值调用点）、`learning/teaching_gate.py` 与前端；04 碰 `ingestion/parsers.py`、`ingestion/service.py` 及两处服务构造接线。文件不重叠，可与任何轨道并行领取（README 执行建议同此）。
- 附带观察（核实行号时发现，非调查结论）：`decision.py:73-83` 的 `_CASUAL_TERMS` 当前仅有定义、全仓库无任何引用。若 (b) 采用寒暄兜底规则则顺手接线并测试；不采用则保持现状，本 Issue 不做无关清理。

## Answer

- 已在分支 `codex/knowledge-base-p0-fixes` 完成四项修复，并补齐对应单测与知识库页面 E2E 用例。
- 通过：本次新增/相关后端行为测试；`mypy --strict`（4 个修改后的 Python 源文件）；`apps/web` `npm run typecheck`；`git diff --check`。
- 已知基线：指定 pytest 回归为 `98 passed, 35 failed`；35 个失败均来自 Issue 11 全局知识库迁移后仍调用已退役聊天附件/项目文件接口的旧测试，报错为 `legacy_file_source_retired`，未触及本次改动文件逻辑。E2E 因当前 Windows 沙箱禁止 Playwright 浏览器进程创建（`spawn EPERM`）未进入断言阶段。
- `ruff check` 唯一报告为基线中已存在的 `TeachingQuiz` 未使用导入（`src/bridges/learning/teaching_gate.py:35`），本次改动未产生该问题。

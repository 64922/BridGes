# 07 — 知识库检索与通用 OCR

**What to build:** 用户从知识库主动添加的材料可被日常或学习任务按需检索，回答能定位到实际使用的材料片段。

**Blocked by:** 03 — 长对话上下文

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 现有账户知识库的上传、状态、重试、下载和删除仍可用；聊天附件不出现在知识库列表或索引中。
- [x] 检索是否触发取决于模式、任务与用户请求，不再由科学词白名单决定；无关材料不进入模型上下文。
- [x] 讲义、笔记、简历和教材章节的代表材料完成通用 OCR／解析验证，引用包含文档、页码或片段定位。
- [x] 向量模型与索引版本独立于主模型；跨账户召回和已删除材料召回被拒绝。

## Comments

### 实现摘要（2026-09-25，分支 `v2/07-knowledge-base-retrieval`，worktree `../BridGes-07-knowledge-base-retrieval`）

**AC2 检索是否触发不再由学科词白名单决定**（`src/bridges/retrieval/decision.py`）

- 删除 `_SCIENCE_TOPIC_TERMS` 及其分支：任何学科的专业名词都不再单独构成检索理由。判定只看三件事——模式、能力路由、用户请求形态；`RULES_VERSION` 升到 `retrieval-intent/v3`。
- 请求词表 `_UPLOADED_MATERIAL_TERMS` 补齐 V2 首版材料名（讲义、笔记、简历、教材章节、上传材料…），学习与日常模式共用同一判定；`_STUDY_TERMS` 不再重复登记「教材」（会被材料分支先行命中，属遮蔽项）。
- 判定顺序（先到先得）：`user_disabled` → `image_edit` → `explicit_knowledge_base` → `uploaded_material` → 其余专用能力 → 学习模式教学请求 → 日常知识问句 → 不检索。
- 无关材料不进模型上下文：只有本轮产出引用时才注入 `retrieval_context(citations)`（`chat/turn.py::assemble_payload`）；无引用即无上下文块，由 `test_unrelated_material_is_not_injected_when_retrieval_runs` 固定。

**AC3 通用 OCR 与定位引用**

- `src/bridges/ingestion/ocr.py`：图片提示词从科学图片预设改为通用文档抽取（保留阅读顺序与换行、公式表格转纯文本、不猜不可辨认内容、无可读文字时返回空）。`IMAGE_PARSER_VERSION` 升到 `image-ocr-v2`，旧解析缓存按版本失效并以新提示词重识别（`test_image_parse_cache_version_expiry_reparses_with_ocr` 参数化覆盖 `image-ocr-v1` 与 `image-metadata-v1` 两种陈旧版本）。
- `qwen_ocr` 的 `prompt_version` 由 `2026-07-24` 升到 `2026-09-25`：提示词变了，运行锁里的审计快照必须跟着变（不做「按当前提示词回猜历史运行」）。
- 四类代表材料端到端（解析 → 分块 → 索引 → 检索 → 引用）：讲义 PDF（定位到第 2 页）、简历 DOCX（定位到「项目经验」小节）、教材章节 MD（定位到「第三章 电磁场」）、笔记照片（先走通用 OCR 再入库）。引用均带文件名 + 页码或小节标题 + 片段文本。
- 真实 OCR smoke 保留显式门禁：`BRIDGES_OCR_REAL_SMOKE=1` 且安装级全局 Key 存在才执行；本机 `is_global_qwen_key_configured()` 为 False，本轮未执行（记为跳过，不记为通过）。

**AC4 向量模型/索引版本独立与召回边界**

- 索引契约（`IndexContract(model_id, dimensions, normalization, chunker, schema_version)` + `index_versions`/`index_active`）独立于可配置主模型：`qwen_embedding` 不在 `CONFIGURABLE_CAPABILITIES`，`configured_model_id("qwen_embedding", …)` 返回 `None`，激活/切换主模型不改索引版本，由 `test_index_contract_is_independent_of_main_model_activation` 固定。
- 跨账户材料不可召回（`test_cross_account_material_is_not_recallable`）；已删除材料不可召回，且历史引用侧如实标注材料已删除（`test_deleted_material_is_not_recallable_but_citation_reports_deleted`）。

**AC1 既有知识库能力与聊天附件隔离**（不改实现，跑既有套件核对）

- 上传/状态/重试/下载/删除/重建/级联删除/跨账户 404 全部通过（`tests/knowledge_base/test_knowledge_base_api.py`、`test_knowledge_base_service.py`）。
- 聊天附件不进知识库：以 source 守卫与检索层 attachment 层退役为证据；守卫用例 `test_chat_attachment_record_not_mutable_via_kb_path` 在 main 与分支上**同为预存在失败**（V2 已把聊天附件入库通道退役为 410，用例仍走旧 API），非本票回归。

### 全量回归与基线比对（2026-09-25）

- 命令（两侧完全一致）：`pytest tests --ignore=tests/humanize_eval -q --tb=no -rf --basetemp=.tmp/pytest-basetemp`
- main（557b418）：**239 failed / 3649 passed / 39 skipped / 2 errors**
- 分支（893c546）：**241 failed / 3662 passed / 40 skipped / 2 errors**
- 失败名称差集：**main 额外 0 项**；分支额外 **2 项**，且两项都是 `tests/closeout/test_api_boot.py`（`test_api_starts_from_unique_absolute_sqlite_path_and_health_passes`、`test_two_api_runs_do_not_collide_on_ports_or_data_dirs`）。239 + 2 = 241，算术闭合。
- 这 2 项是**工作树环境差异、与代码无关**：该文件的 conftest 优先用 `REPO_ROOT/.venv` 启动子进程，而 main 仓有**未跟踪的 `.venv`**、worktree 没有，于是回退到 conda Python 且子进程 `ModuleNotFoundError: No module named 'bridges'`。带 `PYTHONPATH=src` 复跑 `tests/closeout/test_api_boot.py` 即 **2 passed**。`tests/integration/test_runtime_smoke.py` 的 2 个 setup error 同源。
- 新增用例：`tests/retrieval/test_v2_07_knowledge_base_retrieval.py`（11 例：9 函数，代表材料参数化 3 例）＋ `tests/retrieval/test_decision.py` 2 例 ＋ `tests/ingestion/test_ocr.py` 1 例 ＋ `tests/ingestion/test_ingestion_service.py` 参数化 1 例。
- 随票改写 2 个既有用例（`tests/chat/test_retrieval_chat.py`）：它们原本靠「热力学」这类**裸学科名词**触发检索（旧白名单行为，与 AC2 直接冲突），改用点名材料的请求形态（「根据我的材料，…」）。两用例验证的是引用投影跨刷新与重试复用同一轮次，改触发词不改验证意图。
- 定向（两侧同命令）：`pytest tests/knowledge_base tests/chat/test_v2_05_photo_attachments.py tests/retrieval tests/ai -q --tb=no -rf` → 分支 **6 failed / 265 passed**、main **6 failed / 252 passed**，**失败名称集合完全相同**。
- 静态门禁：`ruff check src tests` 两侧输出**逐行一致**（573 errors，均为预存在）；`mypy src`（strict）两侧**逐行一致**（115 errors / 23 files）。

### 已知边界

- 语义相关性只能由关键词路径证明：测试替身 `DeterministicEmbeddingPort` 对无关文本也给出约 0.87–0.91 余弦，替身自身无法区分相关性，故「无关材料不进上下文」的断言走 `embedding=None` 的关键词检索路径。真实语义门需要真实 embedding 才能验证。
- 无文字层的扫描 PDF 仍按 `empty` 处理：本票只改 OCR 提示词口径，未加版面分页或 pdftotext 兜底。
- 文件类型/大小上限沿用既有实现，本票未提高承诺。
- 尚无「模块/任务」显式入参：判定只依赖模式 + 请求形态 + 能力路由（Issue 03 编译器单预算收敛的已知边界同理，本票未扩大）。
- 泛化材料词（如「讲义」「教材」）在闲聊里可能多触发一次检索；无引用产出则不注入上下文（已有用例固定）。
- 本机 `test_start_fails_*` 三个用例在 worktree 下会挂死（PYTHONPATH 可用时 desktop `start` 真的拉起四服务），故全量跑沿用既有本地惯例避开；main 侧它们快速失败，属预存在失败。
- 预存在失败（main 与分支同名）：V2 退役类用例仍走旧 API（聊天附件/项目文件入库 410）、`test_runtime_smoke` 的 `running_api` 夹具子进程失败、端口占用类波动用例。

### Code review 结论（两轴）

- Standards：5 项发现，修 4 项——① `qwen_ocr` 的 `prompt_version` 未随提示词变更（已升到 `2026-09-25`）；② `decide_retrieval` 文档里的判定顺序与代码不一致（已按代码归位）；③ `_STUDY_TERMS` 与 `_UPLOADED_MATERIAL_TERMS` 重复登记「教材」（已删重）；④ `tests/ingestion/test_ocr.py` 对提示词措辞做逐词断言，属变更探测器（已收敛为「通用文档任务、无学科预设」的行为口径）。未改 1 项：测试替身与既有用例的重复（符合仓库惯例，保留）。
- Spec：4 条 AC 均有实现或证据；未发现越界实现；`tests/ingestion/test_ocr_real_smoke.py` 中硬编码 `SCHEMA_VERSION == 47` 的断言修正属本票前置修复（随 schema 演进必然失败），已在实现提交正文说明。


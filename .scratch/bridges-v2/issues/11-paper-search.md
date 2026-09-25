# 11 — 论文搜索

**What to build:** 用户从日常聊天的 `+` 菜单显式选择论文搜索后，可收到有真实论文链接、选择理由和阅读顺序的结果。

**Blocked by:** 02 — 可恢复的对话运行；03 — 长对话上下文；04 — 自然表达与专用人味化退出

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 此票把第一个显式模块接到日常父图：`paper.parse → paper.plan → paper.search → paper.enrich → paper.rank → paper.present`。解析结果记录原文短语、规范化值、扩展词、置信度和最终查询；缺失或歧义只问一项，并将等待状态持久化，下一条回复从该处恢复。工具返回统一记录查询、实际证据、取得时间与错误；查询设超时、有限重试和取消，先核对标题、主体、链接和原词覆盖再生成。公开检索只发送最小查询词，不能传私人上下文；首个子图建立的这套证据与恢复合同供后续模块沿用。

- [x] 论文模块的中文菜单项与可移除 chip 可用；发送时逐消息保存明确模块 ID，重开后历史标识不随新选择改变。
- [x] `+` 上拉菜单、chip 移除及发送按钮有中文可访问名称和可见焦点；菜单以 `aria-expanded` 标记状态，支持方向键、Enter、Tab、Esc 和点击外部关闭，关闭后焦点返回 `+`，已输入文字不丢失。
- [x] 论文子图只由显式选择或用户点击建议启动；普通聊天中明显的论文请求可建议一键以原文启动，不暗中检索。
- [x] 保留原始术语与实际查询词；孤立的“Transformer”先消歧，明确机器学习语境则检索相应主题。
- [x] arXiv 真实结果经主题和来源核对后默认给出 3–5 篇、阅读顺序及链接；稀疏领域或未取到全文时说明实际数量与证据边界。
- [x] arXiv 覆盖不足时有限尝试其他学术元数据来源并标明来源与全文可得性；主题不匹配时停止推荐并澄清，不凑满篇数。
- [x] 澄清、检索失败、停止与重试在同一消息中显示真实状态；代表性外部查询经过实际可得性验证。

## Comments

### 实现摘要（2026-09-25，分支 `v2/11-paper-search`，worktree `../BridGes-11-paper-search`）

**架构：第一个接到日常父图的显式模块，同时固化后续模块复用的三份合同。**
父图新增 `select_explicit_module → invoke_subgraph_or_chat` 派发：只读随用户消息
持久化的 `module_id`（模型无法从正文改写模块选择），未接入的模块明确拒绝而不
悄悄降级为普通聊天；子图六节点各自发真实 `started/completed` 进度，失败定位到
具体节点（`paper.search` 等）。合同落在 `src/bridges/contracts/modules.py`：

- **证据合同** `ModuleQueryRecord`：来源、实际查询词、证据条数、取得时间、
  错误分类、是否可重试、内部说明（内部说明不呈现给用户）。
- **等待合同** `ModuleWaitState`：模块、类型、问句、来源消息 ID、恢复载荷与
  时间；随助手消息持久化，下一轮从 `pending` 处恢复，不靠内存协程跨请求存活。
- **失败与停止合同**：检索 25s / 元数据补充 12s 墙钟预算，超时如实失败；停止
  在节点边界生效并把「已停止」写回同一条消息；重试复用既有运行与幂等键。

**后端**（`tests/paper/` 40 例，含新增 7 例来源单测）：
- `parsing.py` + `lexicon.py`：原词逐字保留、规范化、译名/同义词只作扩展、
  置信度分档；「Transformer」孤立出现先问一项（附候选语境），本轮或前文命中
  机器学习/电力语境则直接检索该语境；年份范围与排序意图（最新/经典/入门）
  解析为真实约束。
- `planning.py`：精确词（主词 + ≤2 个扩展词）优先；候选偏少时第二次只用主词
  放宽一次（`_search` 合并两次记录、取候选更多的那次），目标 3–5 篇，候选
  请求量为目标的 3 倍。
- `sources.py`：arXiv 复用既有 `ArxivSearchService`（缓存/节流/冷却/有界重试/
  陈旧兜底/脱敏审计已有），只发送最小查询词；`MetadataEnricher` 对候选逐条查
  Crossref 与 OpenAlex（每来源每篇一次、5 篇上限、12s 预算），标题相似度
  ≥0.82 才算命中，失败只标注缺口不阻断；每次外发请求写账户归属的脱敏披露
  审计（`AuditAction.ACADEMIC_METADATA_LOOKUP`，只记来源/标题指纹/长度/状态/
  耗时，不记标题正文）。
- `ranking.py`：原词覆盖门 + 语境覆盖门（只命中原词、语境不符的排除并报数）；
  用户说过的年份范围是真实过滤条件（范围外有命中报排除篇数；范围外才有结果
  时保留候选并说明，绝不悄悄忽略）；明确要综述但结果无综述标记时如实说明；
  综述/教程打头，奠基工作用真实引用数据分辨，无引用数据写进未核实项。
- `presenting.py`：正文由真实证据渲染（原词、实际查询词、篇数、每篇链接/
  理由/未核实项、证据边界），不依赖模型即零虚构；可选中文概述经严格门控
  （只接受本轮候选的 arXiv 标识 + 长度上限，越界丢弃），模型不可用时如实说明。
- 落库（迁移 v51）：`messages.paper_search`、`messages.module_suggestion`；
  终态与投影同事务收敛（`finalize_message`）。
- `suggestion.py`：普通聊天里出现论文请求词且有可检索主题时才给「使用论文
  搜索」建议（随消息持久化）；歧义过大不给建议；建议本身不产生任何外部调用。

**前端**（vitest 96 例；`tsc --noEmit` 与 `next lint` 干净）：
- `+` 菜单复用既有可访问 `Menu`（`aria-expanded`、方向键、Enter、Tab、Esc、
  点击外部关闭、关闭后焦点回 `+`），新增「论文搜索」与「添加照片和文件」；
  选中后输入区上方出现可移除 chip（中文可访问名称），已输入文字不丢失；
  发送逐消息带 `module_id`，历史标签只读消息记录。
- `PaperSearchCard`：澄清/检索中/成功/空/失败/停止六态同一条消息渲染，含原词、
  实际查询词、每次外部调用的来源/查询/状态/时间、阅读顺序、摘要页与全文链接、
  证据边界与重试入口；不呈现缓存命中/上游次数等内部日志。
- `ModuleSuggestionCard`：一键建议（以原文启动、不自动检索、术语歧义时说明
  会先提问一个问题）。
- 等待状态恢复：重开对话时若最后一条论文消息仍在等澄清，输入区恢复「论文
  搜索」标签（可见、可移除，只判定一次），下一条回复从该处继续。

**code-review 双轴（Standards/Spec）复核与修复：**
- 失败投影不再丢查询记录（`_fail` 的 `queries` 形参此前未被使用；现按传入
  记录写回，原词覆盖失败路径也带上本轮全部记录）。
- 全文可得性如实：arXiv 候选自带来源给出的 PDF 链接即「已取得全文」，不再把
  有 PDF 的 arXiv 结果标成「未确认取得全文」。
- 年份范围从「解析了但没用」变成真实过滤 + 证据说明（新增 2 例单测）。
- 删除死代码：`render_error_content`、`MAX_SEARCH_ATTEMPTS`、`_LATIN`、
  `ModuleSuggestionCard` 未用的 `disabled`、从未被设置的
  `PaperConstraints.categories`。
- 单源化：角色中文标签 `ROLE_LABELS`、模块 ID 校验 `CHAT_MODULE_VALUES`、
  元数据超时常量 `ENRICH_TIMEOUT_SECONDS`；`parsing` 两段澄清构造合并为
  `_domain_clarification`。
- 云端披露记录：Crossref/OpenAlex 外发请求此前没有账户归属，现逐次记账
  （新增 7 例来源单测覆盖命中/不误配/有限尝试/超时与审计）。
- 前端：未接入模块不再回显英文 ID（宁可不显示标签）；澄清卡文案与真实恢复
  行为一致。

### 全量回归与基线比对（2026-09-25）

- pytest 全量（两侧同 flags：`--ignore=tests/humanize_eval`、deselect 三个
  `start` 冒烟用例、`-p no:randomly`、`--basetemp` 各自独立）：
  - main（基线提交 `557b418`）：**237 失败 / 3648 通过 / 39 跳过 / 2 错误**。
    该数字两次独立全量复跑逐字一致，可作稳定基线。
  - 分支：**219 失败 / 3706 通过 / 41 跳过 / 0 错误**（该次为让子进程能导入
    `bridges` 而带 `PYTHONPATH=<worktree>/src`，见下条原因）。
- 失败用例名集合差集（219 vs 237 个名字）：**仅分支独有 1 个，仅 main 独有 19 个**。
  19 + 1 恰好解释两侧失败数之差（237 − 19 + 1 = 219），无未归因项：
  - 「仅 main 独有」19 项中 **18 项**是 `test_cli_contract.py`、`test_runtime_contract.py`、
    `test_runtime_smoke.py` 里会 `python -m bridges.cli...` 起子进程的用例。本机
    conda `agent` 环境当前**没有可用的 `bridges` 安装**：在 main 仓库目录下
    `python -c "import bridges"` 同样报 `ModuleNotFoundError`（pytest 进程内能导入
    是靠仓库 ini 的 `pythonpath = src`，不传递给子进程）。所以这 18 项在 main 上
    失败、在分支带上 `PYTHONPATH` 后通过，是执行方式差异而非代码差异。已实证：
    分支**去掉** `PYTHONPATH` 跑 `tests/integration/test_runtime_smoke.py`，得到与
    main 逐字相同的 `10 failed, 2 errors`。
  - 「仅 main 独有」余下 1 项 `tests/contracts/test_openapi_sync.py::test_committed_openapi_matches_current_api`
    在 main 上自身即失败（其 HEAD 已前进到 `0d67f6a`，本票基线为 `557b418`）；
    本分支一侧契约文件是重新生成过的。
  - 「仅分支独有」1 项 `tests/closeout/test_arxiv_worker_reliability.py::test_handshake_timeout_maps_to_arxiv_handshake`
    起真实 worker 子进程，断言握手超时 1.5s 与墙钟 < 8s，其源码注释即写明
    「并行负载下更长，否则超时与启动竞态、断言失真」。单独复跑 3/3 通过、整文件
    14/14 通过、6 路并发复跑 18/18 通过，且此前两侧所有全量产物中从未出现，
    判为负载抖动而非回归。
- 上一轮（`557b418` 基线上另一份带 `PYTHONPATH` 的全量）曾出现 240 失败，
  多出的 3 项已定位为契约变更：`tests/chat/test_v2_02_resumable_runs.py` 中
  图版本号与「未实现模块」用例（该文件原以 `paper` 作为未实现模块样例，
  本票实现后改为 `tieba`），已同步更新，现该文件 10/10 通过。
- mypy：两侧均 **115 处 / 23 文件**（含本票新增的 `src/bridges/paper/`，零新增）。
- ruff `src/`：main 311 → 分支 314，唯一差量是 `api/main.py` 新增 3 处 E402
  （该文件已有 85 处同类，为保持模块级装配惯例）。
- 前端：`tsc --noEmit` 干净、`next lint`（改动文件）零告警、vitest 分支
  **96/96（15 文件）**，main 侧同命令为 77/77（12 文件），即本票净增 19 例 / 3 文件。

### 真实可得性验证（AC5/AC7）

- 用本模块生成的查询词直接请求 arXiv API（`sortBy=relevance`，与默认意图一致）：
  - `all:knowledge AND all:distillation` → 真实且主题相符：`2004.08116 Triplet
    Loss for Knowledge Distillation (2020)`、`2405.09820 Densely Distilling
    Cumulative Knowledge for Continual Learning (2024)`、`1812.00660 Knowledge
    Distillation with Feature Maps for Image Classification (2018)`。
  - `all:Transformer AND all:attention AND all:mechanism` → `2209.15001 Dilated
    Neighborhood Attention Transformer (2022)`、`1809.04281 Music Transformer
    (2018)`、`2206.03003 Transformer-based Personalized Attention Mechanism (2022)`。
- 元数据补充链路对真实源实测：`MetadataEnricher` 对 “Attention Is All You Need”
  命中 Crossref 与 OpenAlex（期刊、引用数 7679、开放获取全文链接），2 篇 × 2
  来源共 4 条 `academic_metadata_lookup` 披露审计；不存在的标题返回
  `no_match`，证明阈值不误配。
- **边界（如实记录）：** 本机对 arXiv 的进程内 HTTPS 请求一律被网络路径挡回
  `406 Not Acceptable`（`httpx` 与标准库 `urllib` 同样 406，换 UA/Accept 无效；
  同一 URL 用 `curl` 返回 200），因此本机无法跑通「模块内真实 arXiv 调用」的
  端到端；已用 curl 复核查询词构造与真实返回，模块内的失败分类也如实显示为
  `arxiv_request`（HTTP 4xx）并保留查询词。该 406 与代码无关（既有
  `arxiv_mcp` 客户端同受影响），网络路径未在本票内改动。

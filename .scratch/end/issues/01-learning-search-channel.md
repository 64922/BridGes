# 01 — 修复学习模式联网搜索通道并建立"未联网核实"降级路径
Status: ready-for-agent
Blocked by: 无
Covered requirements: 三次改进#1

## 背景与根因

现象：用户在学习模式问"我想学习卷积神经网络的基础知识"，知识库为空时，系统回复"这轮我先不把不确定内容说成结论：没有找到能覆盖本轮目标的公开来源，暂不能可靠断言关键结论。"——"知识库无资料时联网兜底"的承诺事实上不可达。

管线事实（均已按当前代码核实）：学习模式分支在 `src/bridges/chat/turn.py:1827`；本地三层检索 `:1914` → `required_search` 决定公开来源（`:1942`，门侧实现 `src/bridges/learning/teaching_gate.py:344`、`TeachingTurnService` 包装 `:389`）→ 公开搜索阶段 `:1950-2144`（`_parallel_search` 线程池 `:3171`，阶段墙钟取 web 8s/arxiv 10s 较小者 `src/bridges/chat/budget.py:27-30`，计算点 `turn.py:2001-2009`）→ 证据门裁决 `TeachingEvidenceGateService.assess`（`teaching_gate.py:217`，类起于 `:214`）→ `can_answer_reliably=False` 时完全不调用模型，直接落库 gap_response 并 done（`turn.py:2187-2224`；文案 `teaching_gate.py:992-997`）。

按贡献排序的根因：

1. **选错搜索端点**：`src/bridges/web_search/client.py:25` 使用 DuckDuckGo Instant Answer API（`https://api.duckduckgo.com/`，provider 版本 `duckduckgo-instant-answer-v1` `:26`），`_parse_results`（`client.py:377-431`）只认 `Results`/`RelatedTopics` 两个字段。该 API 主要对英文维基词条有响应，对绝大多数真实查询——尤其中文查询——结构性返回空。查询脱敏进一步破坏语义：`_scrub_with_categories`（`src/bridges/web_search/service.py:232-261`）用 `_TOKEN` 正则（`service.py:187`，CJK 片段 2-8 字）切词并限 8 个 token（`service.py:261`），实际外发查询是被切碎的关键词串。
2. **verified 门槛过高**：每条结果须回抓原网页才算 verified（`_fetch_source` `client.py:210-300`），`follow_redirects=False`（`client.py:233`）且 `max_redirects=0` 策略（`:219-227`）使任何 3xx 直接 fetch_failed；Wikipedia/百度百科/知乎等列入 `_AGGREGATOR_HOSTS`（`client.py:31-40`）一律标 `summary_only`（`client.py:283-292`），而证据门只接受 `verified`/`cross_verified`（`teaching_gate.py:168`，投影层同标准 `service.py:424-428`）——中文知识类查询最常见的来源被制度性排除。无 verified 即投影 `FETCH_ERROR`/`EVIDENCE_INSUFFICIENT`（`service.py:445-462`）。
3. **无降级路径**：provider 固定 duckduckgo（`service.py:28`、`:272-284`），单查询（`max_queries=1` `service.py:49`）、最多 1 次重试（`max_retries=1` `service.py:51`），且重试循环只对可重试异常生效（`service.py:355-362`）——`EMPTY`（`service.py:434-444`）是正常返回后的投影，不重试、不改写查询、无备用引擎。页面回抓串行（`client.py:137` 列表推导逐个 `_fetch_source`，客户端默认超时 8s `client.py:73`），而阶段总墙钟只有 8s，容易整体超时投影为 `web_search_timeout`（`turn.py:2048-2056`）。
4. **fail-closed 放大上游缺陷**：证据门不过就不调模型（`turn.py:2187-2224`），教学上下文与模式合同都明文禁止模型记忆补全（`turn.py:909`、`:243-246`）。门本身按设计工作；按已确认决策，本 Issue 把"无证据时的行为"从拒答改为"模型知识回答 + 诚实标注"。

## What to build

按已确认设计决策落地四件事：

1. **换用真正的网页搜索端点**：放弃 DuckDuckGo Instant Answer API，改用免费无需 key 的真正网页搜索端点（DDG HTML `https://html.duckduckgo.com/html/` 或 Lite `https://lite.duckduckgo.com/lite/` 端点，解析真实网页结果列表），provider_version 相应升级。
2. **多查询并行 + 空结果改写重试**：模仿 DeepSeek"同时进行多项搜索，以覆盖不同的侧重点"，一轮把脱敏后的主题拆成 2-4 个侧重不同的查询并行执行，结果按 URL 去重合并；合并结果为空时自动改写查询（拓宽/换措辞）重试，改写次数有界，全部纳入阶段预算。
3. **聚合站正常计证 + 回抓并行化**：维基/百度百科/知乎等聚合站作为普通证据来源，回抓成功即 `verified`，不再制度性 `summary_only`；页面回抓由串行改为有界并行，整体耗时纳入阶段预算（`budget.py:27-30` 墙钟与 `turn.py:2001-2009` 剩余预算约束不变）。
4. **证据门降级路径**：搜索仍失败（EMPTY/FETCH_ERROR/超时/不可用）且本地无证据时，不再 fail-closed 拒答；正常调用模型用自身知识回答，回答开头明确标注"本轮未联网核实"，事实性结论降调。证据门投影如实保留不足状态，TeachingCard/WebSearchCard 文案同步，不再把该场景呈现为纯拒答。

## Implementation notes

文件清单与修改要点（行号按当前代码核实，实现时以符号为准）：

1. **`src/bridges/web_search/client.py`（核心改造）**：
   - 端点与解析：`DUCKDUCKGO_ENDPOINT`（`:25`）换为 DDG HTML/Lite 端点，`DUCKDUCKGO_PROVIDER_VERSION`（`:26`）升级（如 `duckduckgo-html-v1`）；`search()`（`:85-137`）的请求参数与 `_parse_results`（`:377-431`）改为解析 HTML 结果页（结果链接/标题/摘要）。保留既有安全约束：`_unsafe_url_code` 目标校验、`_read_bounded` 响应上限（`:319-332`）、`max_results` 截断、`_authority_rank` 排序（`:434-448` 区域）。HTML 解析失败映射为可重试 `WebSearchError`，不得抛裸异常。
   - 聚合站去降级：删除 `_fetch_source` 中 `_is_aggregator` → `summary_only` 分支（`:283-292`），聚合站回抓成功走正常 verified 路径（`:293-300`）；`_AGGREGATOR_HOSTS`（`:31-40`）与 `_is_aggregator`（`:447-448`）随之清理。空文本的 `summary_only`（`:274-282`）可保留——无正文本就不可核验。
   - 回抓并行化：`:137` 的串行列表推导改为有界线程池并行回抓（可参照 `turn.py:3171` `_parallel_search` 的线程池模式），每条请求仍独立超时，整体受调用方剩余预算约束。
   - 建议：页面回抓重定向预算从 0 放宽到小值（如 2）。真实站点普遍 301/302，当前策略把它们全部打成 fetch_failed；若采纳，同步调整 `SearchPlan.max_redirects`（`service.py:54`）并保持链长有界（`:219-227` 的防护意图保留，改为限深而非禁绝）。
   - `health_check`（`:139-160`）同步指向新端点。
2. **`src/bridges/web_search/service.py`（编排）**：
   - 多查询：`SearchPlan` 由单 `query` 扩展为查询集合（2-4 个侧重查询，拆分在 `_scrub_with_categories`（`:232-261`）脱敏输出之上做，原始用户句子不外发）；`plan_id`/缓存键材料（`:56-77`）改为查询集合的哈希。
   - 并行执行与合并：主循环（`:340-399`）改为对查询集合有界并发执行，结果按 URL 去重合并后再进 `_project_results`（`:401-`）；`query_count` 如实记录（现多处硬编码 `1`，如 `:371`、`:422`、`:443`）。
   - 空结果改写重试：合并结果为空（现 `EMPTY` 投影 `:434-444`）时自动改写（拓宽：去修饰词、收缩为核心概念；或替代措辞）重试，次数有界且受 `total_timeout_seconds`（`:52`）与调用方剩余预算约束；改写轨迹记入 reason 与 `_audit` 审计。
3. **`src/bridges/learning/teaching_gate.py`（门裁决与降级；状态机改版属 02，不在此动）**：
   - `assess`（`:217-341`）的 EMPTY 分支（`:288-299`）与 ERROR/None 分支（`:262-286`）：证据状态保持如实（INSUFFICIENT/UNAVAILABLE 不变），但为降级路径提供可机读的"允许模型知识兜底"信号（如在 `TeachingEvidenceGate` 增加可选布尔字段，或由 `turn.py` 按 search_status 判定），不再只有 `can_answer_reliably=False` 一条路。
   - `:168` 的 verified/cross_verified 过滤标准保持不变——聚合站回抓成功后自然成为 verified；summary_only 是否计证不在已确认决策内，默认不允许。
4. **`src/bridges/chat/turn.py`（搜索编排与降级路径）**：
   - 降级生成：`:2187-2224` 的 early-done 分支改为进入正常模型生成；`teaching_context`（`:905-927`）注入新指令——证据门未通过时允许用模型自身知识回答，但必须开头标注"本轮未联网核实"、事实性结论降调、不得伪造来源引用。为不依赖模型自觉，落库前对正文做确定性处理（固定前缀或结构化标注），保证标注一定出现。
   - 兼容改词：`_MODE_CONTRACTS[ChatMode.STUDY]` 的绝对禁令（`:243-246`）与 `teaching_context` 首行（`:909`）按降级路径改写为"未联网核实时允许模型知识，但必须显式标注并降调"。合同整体重写属 02，此处只做与降级兼容的最小改动。
   - 搜索编排：`:1950-2144` 阶段结构与 `:2001-2009` 预算计算保持不变；确认多查询并行 + 并行回抓的总耗时仍受 min(web 8s, arxiv 10s) 与剩余预算约束。
5. **前端**：
   - `apps/web/src/components/bridges/TeachingCard.tsx`：`statusLabel`/`evidenceLabel`（`:22-36`）文案与降级场景对齐——"暂未找到足够证据"（`:26`）场景现在伴随正常回答，文案应说明已用模型知识回答且本轮未联网核实；`sourceLabel`/`searchSourceLabel`（`:38-52`）的 DuckDuckGo 标签按新端点核对。
   - `apps/web/src/components/bridges/WebSearchCard.tsx`：empty/fetch_error/evidence_insufficient 状态文案（`:167-193`）同步，说明系统已降级为模型知识回答而非拒绝回答。
   - 若门投影新增字段（上文 3）：跑 `scripts/regenerate_openapi.py` 再生成 `openapi.json`，`packages/contracts` 与 `apps/web/src/lib/api.ts` 类型同步。

## Acceptance criteria

- [ ] 学习模式问"我想学习卷积神经网络的基础知识"且知识库为空时，系统向真正的网页搜索端点发出 2-4 个侧重查询并拿到真实网页结果，不再出现 Instant Answer 结构性空结果导致的"没有找到可核实的公开网页结果"。
- [ ] 中文知识类查询结果中，维基/百度百科/知乎页面回抓成功即计为 verified 证据，证据门可据此判 SUFFICIENT。
- [ ] 首轮查询合并结果为空时，系统自动改写查询重试（审计/投影可见改写轨迹），改写与重试不超出阶段预算。
- [ ] 页面回抓并行执行，5 条结果的搜索阶段总耗时受既有 8s 墙钟约束，不再因串行回抓轻易触发 `web_search_timeout`。
- [ ] 断网/搜索持续失败时，学习模式正常给出模型知识回答，开头明确标注"本轮未联网核实"，事实性结论降调，卡片如实显示证据不足状态；不再只回 gap_response 拒答。
- [ ] 搜索成功路径行为不回归：verified 证据正常注入教学上下文并生成对应引用。

## Verification

- pytest：`tests/web_search/`（现有 `test_duckduckgo_service.py` 改写为新端点 + 多查询/改写重试/并行回抓用例）、`tests/learning/test_teaching_gate.py`（门裁决与降级信号）、`tests/chat/` 学习模式相关（`test_teaching_chat.py`、`test_issue08_conversational_learning.py`）；新增降级路径测试（搜索失败 → 正常回答 + 确定性标注）。
- `mypy --strict` 与 `ruff` 对改动文件干净；若动契约，跑 `python scripts/regenerate_openapi.py` 并确认契约同步测试通过。
- 前端 `npm run typecheck`（TeachingCard/WebSearchCard 改动）。
- 手工冒烟：真实网络下学习模式提问 CNN 问题（知识库为空），观察多查询并行与来源引用；断网后重试，观察降级标注回答。

## Non-goals

- 不改教学状态机、分课时/测验机制与 `_MODE_CONTRACTS` 整体结构（属 02）。
- 不动 arXiv 通道（`arxiv_mcp`）与本地三层检索。
- 不引入需 API key 的搜索服务；不新增搜索 provider 配置面（仍为固定提供方）。
- 不做搜索结果排序/推荐优化（保留 `_authority_rank` 现有口径）。

## Blocked by

无。

## Comments

串行协调：本批 01/02/03 都会改 `teaching_gate.py`（01 改门裁决与降级、02 改状态机、03 改知识库侧联动），且 01/02 都碰 `turn.py` 学习模式分支，必须串行执行——01 先落地，02 在 01 的新门裁决上改版。

与 02 的接口约定：01 提供的"允许模型知识兜底"信号与确定性标注机制由 02 直接复用；01 不重构 `contracts/teaching.py` 主体，若降级信号必须落在契约上，新增字段保持向后兼容（可选、有默认值），避免阻塞 02 的契约精简。

风险：DDG HTML/Lite 端点有速率限制且页面结构可能变化，解析器必须容错（解析失败映射为可重试错误而非崩溃）；多查询并行会放大限流概率，并发度与改写重试次数取保守值。脱敏规则（`service.py:232-261`）保持不变，多查询拆分只能基于脱敏后的 token。

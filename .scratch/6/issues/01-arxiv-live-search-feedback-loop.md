# Issue 01：修复 arXiv 普通关键词并建立真实搜索反馈环

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-ARXIV-01、US-ARXIV-02、US-ARXIV-03

## 已验证现状与根因

- 用户在论文搜索中输入普通主题（已用 `Transformer` 复现）时，`src/bridges/arxiv_mcp/client.py` 的 `_build_search_params()` 会先查找 `author:`、`title:`、`id:`、`year:` 等结构化字段。没有任何结构化字段时，`matches` 为空，`topic_parts` 也始终为空，最终不会生成 `search_query`。
- 因此发往 `https://export.arxiv.org/api/query` 的请求只包含 `start`、`max_results`、`sortBy`、`sortOrder` 等分页/排序参数，用户的主题没有被传给上游。这不是“arXiv 没有结果”，而是本地请求构造缺陷。
- 已检查到生产会话中的失败投影为 `arxiv_request`、0 条结果、约 734ms。该证据只能说明上游请求未完成，不能证明主题已被真实检索；当前通用 4xx 映射还会丢失有助于诊断的状态类别。
- 论文抓取阶段本来就不应调用 Qwen。只有在真实论文结果返回后，最终自然语言归纳才允许走已登记的 Qwen 文本能力；论文卡片不得由模型臆造。
- 当前结构化查询路径有测试基础，但缺少“纯普通关键词必须进入请求”的契约测试，也缺少一个会在 `search_query` 缺失时明确返回 400 的纵向回归场景，所以此缺陷长期未被门禁捕获。

### 上下文指针

- `src/bridges/arxiv_mcp/client.py`：`_build_search_params()`、`ArxivMcpClient.search()`、HTTP 状态映射与 Atom 解析。
- `tests/arxiv_mcp/test_client_and_service.py`：客户端参数、错误映射和服务投影测试。
- `tests/chat/test_arxiv_search_chat.py`、`tests/chat/test_natural_language_paper_route.py`：聊天入口、自然语言路由、消息投影与最终回答。
- `apps/web/e2e/issue22-arxiv-paper-search.spec.ts`：用户可见论文搜索状态与论文卡片。

## What to build

交付一个从“普通主题输入”到“真实 Atom 结果或真实失败状态”的最小纵向切片：

1. 修正查询构造契约。没有结构化字段时，将规范化后的完整普通主题作为 arXiv `all:` 查询；同时保持结构化字段与其前后普通主题组合查询的既有能力。
2. 对空白、只有无值字段、混合普通主题与字段、引号以及多余空格建立明确且确定的参数行为。最终没有任何有效检索约束时，在发起网络请求前返回稳定的本地请求错误。
3. 保证发送到固定 arXiv 端点的每个主题搜索请求都至少包含 `search_query` 或合法 `id_list`；不能退化为无主题的分页请求。
4. 保留真实 Atom 响应作为论文卡片唯一数据来源。成功投影必须带真实结果数量和可追溯查询摘要；失败投影必须保留稳定错误码、可重试性和脱敏的上游状态类别，不能把 4xx、5xx、超时、权限和解析失败混成“没有结果”。
5. 打通聊天反馈环：搜索中、成功、空结果、失败、取消五种终态均持久化到对应助手消息并在重载后保持一致；成功后 Qwen 只归纳已返回的论文，失败/空结果时不生成伪论文、伪引用或伪成功文案。
6. 增加受控的真实 arXiv smoke（默认普通单元测试不联网）。显式开启时用一个稳定、低成本的普通主题验证上游确实接收查询并返回可解析 Atom；网络未授权时结果必须是 `inconclusive`/跳过，而不是伪通过。

## 非目标

- 不更换 arXiv 提供方，不引入搜索代理、镜像站或需要 Key 的备用源。
- 不扩大 `export.arxiv.org`、`arxiv.org` 之外的网络白名单，也不改变现有 SSRF/固定端点约束。
- 不重写自然语言论文意图路由、排序算法、论文卡片视觉样式或摘要算法。
- 不要求论文抓取调用 Qwen，也不把模型知识当作论文搜索结果。
- 不通过延长全局聊天超时来掩盖请求构造错误。

## Acceptance criteria

- [ ] `Transformer` 生成的参数包含非空 `search_query`，语义等价于 `all:Transformer`，且不再发送无主题分页请求。
- [ ] 多词普通主题的全部有效词项进入查询；空格规范化不改变词项顺序。
- [ ] `graph neural networks author:Kipf year:2016-2018` 等混合输入同时保留普通主题、作者与年份约束；现有 `id:` 路径继续使用合法 `id_list`。
- [ ] 纯空白或仅含无值结构化字段的输入在网络调用前失败，并产生稳定、可测试的请求错误。
- [ ] 测试服务器在缺少 `search_query`/`id_list` 时返回 400、存在主题时返回最小合法 Atom；修复后同一场景从 400 变为成功且解析出论文，能证明修复触达真实请求边界。
- [ ] 401/403、429、其他 4xx、5xx、连接失败、超时、取消和 Atom 解析失败保持可区分的稳定投影；日志和投影不包含完整响应正文。
- [ ] 成功结果中的标题、作者、时间、摘要、abs/pdf URL 均来自 Atom；无结果和失败场景不会出现模型生成的论文卡片或引用。
- [ ] 聊天消息在刷新后仍能还原搜索终态；重试会重新执行搜索并更新当前重试消息，不篡改上一条消息的审计证据。
- [ ] arXiv 检索 worker 不读取、不继承、不发送全局 Qwen Key；只有后续论文归纳产生独立 Qwen 调用记录。
- [ ] 显式真实 smoke 能报告 `passed`、`failed` 或 `inconclusive`，且缺少网络权限时不会把跳过当成发布通过。

## Test plan

1. 在 `tests/arxiv_mcp/test_client_and_service.py` 添加参数化单元测试，覆盖纯主题、多词主题、结构化字段、混合字段、空字段、ID、年份范围和引号清理。
2. 使用 `httpx.MockTransport`（或仓库现有等价机制）建立纵向测试：当参数没有主题时返回 400，有主题时返回最小合法 Atom，并断言真实请求 URL/params 与解析结果。
3. 覆盖 HTTP/网络错误矩阵，断言错误码、权限位、可重试性及用户可见中文提示；同时断言不记录响应正文和凭据。
4. 在聊天服务测试中验证 loading → success/empty/error/cancelled 的持久化投影、SSE 事件及重试行为；模型 spy 断言检索阶段调用数为 0。
5. 扩展 `apps/web/e2e/issue22-arxiv-paper-search.spec.ts`：从普通关键词发起搜索，验证论文卡片来自服务响应；另测失败后可重试且不会显示伪卡片。
6. 在显式联网环境执行最小真实 smoke，并保存提供方、状态、延迟、结果数量和脱敏错误类别。

建议回归命令（实现者可按最终测试文件名等价调整）：

```powershell
python -m pytest tests/arxiv_mcp tests/chat/test_arxiv_search_chat.py tests/chat/test_natural_language_paper_route.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-issue01
cd apps/web
npx playwright test e2e/issue22-arxiv-paper-search.spec.ts --project=chromium
```

## Observability & rollback

- 记录提供方 `arxiv`、查询类型（普通/结构化/ID）、脱敏查询指纹、HTTP 状态类别、耗时、结果数量、终态和稳定错误码；不得记录 Qwen Key、完整用户输入、响应正文或论文全文。
- 为“请求没有 `search_query` 且没有 `id_list`”增加内部不变量错误/计数器，使同类回归在网络调用前可见。
- 真实 smoke 报告必须区分代码错误、网络不可达和上游限流；发布负责人只能接受真实通过或明确批准的 `inconclusive`，不得由 fixture 代替。
- 回滚查询构造改动时应同时回滚对应契约测试；不得回滚为无主题请求。若上游兼容性异常，可临时关闭论文搜索入口并显示真实不可用状态，不能返回确定性假结果。

## Blocked by

无。

## Comments

- 2026-08-13：用户确认需要真实论文搜索；本 issue 将“普通关键词进入真实 arXiv 请求”作为首要发布条件。
- 2026-08-13：论文检索属于 `external_non_qwen`，不要求也不允许为了显示成功而调用 Qwen；成功后的自然语言归纳另走真实 Qwen 文本调用。

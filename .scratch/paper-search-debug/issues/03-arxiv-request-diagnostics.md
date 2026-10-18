# 03 — arXiv 请求失败可诊断并保持多来源交付

**What to build:** arXiv 请求异常时，系统能够识别实际失败类别并提供对应处理建议；其他来源已取得的候选继续经过核验和交付。针对复现出的请求缺陷实施最小修复。

**Blocked by:** 无 — 可独立开始；使用可通过的语义裁决夹具验证多来源流程，不等待 01。

**Status:** ready-for-human

## 问题与证据边界

本轮查询是 `all:"machine learning" AND all:"introduction"`，记录错误码 `arxiv_request`，提示对应 HTTP 错误响应分支。现有记录没有精确状态码和足够上游信息。

不得将尚未观测到的 HTTP 400、代理故障、编码错误、服务变更当成已确认根因。不能以 OpenAlex 成功推导 arXiv 网络正常，也不能用 arXiv 单源失败解释全部候选的语义证据拒绝。

## 实施范围与设计约束

- 检查最终 HTTP 请求参数、编码、固定端点、结果上限与超时设置，使用实际客户端路径构建复现，不另写绕过适配器的查询实现充当产品验证。
- 复测原查询及一个最小对照查询，记录脱敏的最终参数、具体 HTTP 状态、耗时、响应类别和必要安全摘要。不得输出代理认证、完整请求头、密钥或用户私人信息。
- 查阅当前官方接口合同验证参数，不凭记忆猜测外部服务规范。联网操作遵循当前环境可用工具与项目要求。
- 证据显示本地构造错误时，先写实际客户端回归测试再修复；若上游临时故障或环境差异，则明确记录环境/时间及处理方法，不为偶发恢复随意改查询语义。
- 保持固定已登记端点与外发查询最小化，不能通过任意 URL 回退规避失败。不得放宽用户研究条件或来源约束。
- 将 HTTP 细分类别沿现有错误对象、查询记录、投影传递，优先复用现有诊断字段。用户说明区分请求参数、访问限制、限流、超时、上游不可用和解析失败，不能一律让用户修改主题。
- 重试必须有界并服从总预算、停止信号及截止时间；非恢复性请求错误不盲目重试，服务器明确的退避要求按现有能力正确处理。
- 单源失败继续保留已获取的其他来源候选并执行语义与独立证据复核；用户只允许 arXiv 时不能擅自调用 OpenAlex。全部来源失败时诚实报告，不返回伪造空成功。

## 测试与验证方式

使用 HTTP 测试传输覆盖典型请求错误、401/403、429、5xx、超时、连接失败、损坏 Atom 与成功响应，验证错误类别和可恢复性。对实际发现的请求构造缺陷增加精确参数回归。

端到端夹具模拟 arXiv 失败、OpenAlex 返回可核验候选，确认最后仍有合法推荐且保留失败来源记录。再模拟仅允许 arXiv、全部来源失败和用户取消。

真实来源复测单独记录时间、网络环境类别、请求结果和结论，不将真实联网测试作为确定性单元测试的前提。

## 验收标准

- [x] 获得当前原查询的真实响应证据，或明确记录无法取得的环境阻碍及仍未确认的历史根因。
- [x] 若复现本地请求缺陷，具有见红再修复的客户端测试；若未复现，不捏造代码根因或宣称历史故障已证明修复。
- [x] 新失败记录可区分具体 HTTP 类别及重试策略，不丢失诊断所需状态信息。
- [x] 错误信息不泄露敏感载荷，不诱导用户无依据地修改合法主题。
- [x] arXiv 失败、其他来源候选有效时可完成已核验推荐，失败记录仍可查。
- [x] 明确来源约束、全来源失败、停止和共享预算均被正确执行。
- [x] 确定性测试通过，真实验证的成功或限制单独记录；未验证部分不能标为完成修复。

## 不在范围内

增加代理服务、修改用户系统网络设置、引入新论文供应商、更换搜索算法、自动扩展无限重试。

## 实施记录

### 真实请求复测（2026-10-18 UTC）

环境：conda `agent`、Windows、本地代理 `127.0.0.1:7890`、固定端点 `https://export.arxiv.org/api/query`。以下只记录脱敏后的最终参数、状态类别与耗时，不记录请求头、响应正文、代理凭据或用户私人信息。

- 修复前（07:12–07:13Z，实际 `ArxivMcpClient.search` 路径）：`all:"machine learning" AND all:"introduction"` 被构造成 `all:all: AND all:machine AND all:learning AND all:AND AND all:all: AND all:introduction`，HTTP 400，`arxiv_request` / `http_4xx`，516 ms。直接 HTTP 复测同参数为 400（781 ms）；原始表达式对照为 200（702 ms）；最小对照 `all:machine AND all:learning AND all:introduction` 为 200（562 ms）。结论：`_build_search_params` 把 `all:` 当普通词再次加前缀，是本轮 400 的已复现本地根因，排除“主题本身不可检索”。
- 修复后（07:57:57Z，同一实际客户端路径）：最终 `search_query=all:"machine learning" AND all:"introduction"`，HTTP 200，返回 2 篇（`2610.21964v1`、`2610.18210v1`，均 `cs.LG`），478 ms。复现缺陷已修复；真实返回结果未在本票做语义核验（属于 01/04 范围）。
- 最终复测（08:19:18Z）：原查询仍为 HTTP 200、2 篇、420 ms；带年份的 `(all:"machine learning" AND all:"introduction") AND submittedDate:[202001010000 TO 202412312359]` 仍为 HTTP 200、2 篇（`2501.00252v1`、`2501.01453v2`）、98 ms，确认来源层拼接的日期约束没有被表达式解析吞掉。

### 官方接口合同核对（2026-10-18）

查阅 arXiv API User's Manual（`https://info.arxiv.org/help/api/user-manual.html`）：`search_query` 字段前缀为 `ti/au/abs/co/jr/cat/rn/id/all`；日期过滤为 `submittedDate:[YYYYMMDDTTTT TO YYYYMMDDTTTT]`；布尔运算符为 `AND/OR/ANDNOT`；括号用于分组，双引号用于短语；`id` 应使用 `id_list` 参数。实现覆盖 `all/ti/abs/au/co/jr/cat/rn`、`submittedDate`、布尔、括号和引号；`author/title/year` 是本仓库既有内部别名（分别映射 `au/ti/submittedDate`），`id` 字段仍走 `id_list`。日期范围只接受与 `AND` 组合；`OR/ANDNOT` 组合返回明确本地错误而不静默改写语义。

### 实现摘要

- 查询构造拆分为 `query.py`（自然语言/结构化字段、表达式、日期与校验）和 `client.py`（HTTP、Atom 解析、退避头），`ArxivMcpError` 移到 `errors.py`；所有生产与测试导入直接改为 `errors.py`，不保留旧路径兼容层。拆分后 `client.py` 297 行、`query.py` 467 行，均低于 500 行上限。
- `query.py`：新增显式 arXiv 表达式编译，保留 `all:"phrase" AND all:"phrase"`、官方字段、布尔与括号结构；兼容 `author:→au:`、`title:→ti:`、`year:→submittedDate`，不静默放宽或删除硬约束；保留来源层已有的 `submittedDate:[...]` 年份约束。校验控制字符、引号/括号配对、空表达式、日期格式与长度上限。普通自然语言查询保持原有构造路径；小写 `or/and` 不当作布尔运算符，避免扩大检索语义。
- 精确诊断：`upstream_status` 改为 `http_<code>`（400/401/403/404/422/429/5xx）；`arxiv_request` 覆盖非恢复性 4xx，`arxiv_permission` 对应 401/403，`arxiv_rate_limit` 对应 429，`arxiv_offline` 对应 5xx。错误信息区分参数、权限、限流、超时、上游不可用与解析失败，不诱导用户无依据改主题。
- `Retry-After`：客户端解析秒数或 HTTP 日期，`ARXIV_RETRY_AFTER_MAX_SECONDS=300` 在客户端、进程边界和冷却三处共用；经 worker 载荷、进程解码、`ArxivMcpError`、服务投影和 `ModuleQueryRecord.detail` 传递。存在服务器退避要求（含 `0`）时不立即自动重发，改为激活有界冷却并返回剩余秒数。
- `ModuleQueryRecord.detail` 追加 `上游状态：http_<code>`，不改协议 schema；全取消分支先于全失败分支，返回 `paper_cancelled` / `retryable=True`。全来源失败时保留每条记录的来源错误码；错误码不一致则汇总为 `paper_search_failed`，总体 `retryable` 取任一来源的可重试性，避免用单一来源策略掩盖其他来源。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 已同步更新诊断字段描述；`tests/contracts/test_openapi_sync.py` 通过。
- 未改固定端点、来源选择、共享预算、搜索算法或重试上限；新增共享预算拒绝第二个来源的测试，确认已取得来源仍可交付且不伪造成功。

### 验证结果

- 新增/更新确定性测试：表达式保留、括号分组、混合约束、年份范围、官方 `co/jr/rn` 字段、400/401/403/404/422/429/500/503/损坏 Atom、`Retry-After`（含 `0`）、敏感正文不回显、worker 精确状态透传、服务精确投影、`Retry-After` 不立即重试、仅 arXiv、单源失败、全来源失败、全取消、共享预算、arXiv 失败 + OpenAlex 成功 E2E。
- `python -m pytest -q tests/arxiv_mcp tests/paper tests/contracts/test_openapi_sync.py tests/chat/test_arxiv_search_chat.py tests/closeout/test_arxiv_worker_reliability.py tests/closeout/test_v2_21_horizontal_regression.py` → `289 passed, 1 skipped`。
- `python -m pytest -q tests/arxiv_mcp/test_client_and_service.py tests/arxiv_mcp/test_process.py tests/paper/test_paper_issue03_arxiv_diagnostics.py` → `69 passed, 1 skipped`。
- `python -m ruff check src/bridges/arxiv_mcp src/bridges/paper/sources.py src/bridges/paper/nodes_search.py tests/arxiv_mcp tests/paper/test_paper_issue03_arxiv_diagnostics.py` → 通过。
- `mypy` 未报告本票改动文件的错误；全量 `mypy src` 仍有 141 个既有错误，集中在无关模块。
- 全量 pytest 仍受 `origin/main` 既有基线问题阻塞：4 个 `tests/career_plan` 收集错误（`CareerBackgroundLoader` 在 `origin/main` 同样不存在），`--ignore=tests/career_plan` 下另有 328 个集中在 career/retirement/mcp/plugins/learning_projects 等无关模块的既有失败。本票未修改这些模块，抽查失败原因与本次改动无关，故不作为本票验收依据。

### 剩余限制

- 未观测到真实 429/`Retry-After` 响应；退避与上限由确定性测试覆盖。
- 已确认本轮这条查询的历史 400 根因；其他历史失败若无同等修复前后对照，不宣称已证明。
- 真实 arXiv 返回的论文语义相关性未在本票核验，留给 01/04。

## Comments

2026-10-18：用户已确认工单粒度与依赖关系，状态更新为 `ready-for-agent`。如历史上游原因不可恢复取证，可交付诊断能力与有界恢复，但必须把历史原因未确认写入结论，供 04 评估。

2026-10-18：实现与验证完成，状态更新为 `ready-for-human`。本轮查询的本地请求构造缺陷已通过真实修复前后对照确认并修复；真实 429/`Retry-After` 未观测到，由确定性测试覆盖。待合并后由 04 做集成验收。


# Issue 01：接入 Tavily 搜索提供方与安装期凭据流程

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-04、US-07

## 已验证现状与根因

- 本机实测 DuckDuckGo 完全不可达：`html.duckduckgo.com` 与 `duckduckgo.com` 均连接超时（12 秒），环境无代理变量。第 6 轮冻结的 DDG-only 在该网络环境下永远无法成功，学习模式「无法联网抓取网页信息形成教学内容」的直接原因即此。
- 用户已申请 Tavily Key 并冻结决策：通用网页搜索提供方更换为 Tavily。本机实测 `POST https://api.tavily.com/search` 返回 200 与真实结果（含 url/title/content 摘要），无需 MCP 形态。
- 现有搜索接缝：`src/bridges/web_search/client.py`（DDG HTTP 客户端，`httpx.Client(timeout=timeout)` 见 :123-129，结果后抓取正文 :203-207）、`src/bridges/web_search/service.py`（plan/重试/预算/冷却）、`src/bridges/web_search/providers.py`（已有带 Key 提供方先例 BraveSearchClient 与 `settings.brave_search_api_key`，:80、:247）。
- 安装期凭据流程现状：`src/bridges/runtime/bootstrap.py:_resolve_qwen_key`（:303-342）依次尝试 `BRIDGES_QWEN_API_KEY_FILE`/环境变量/系统凭据库/交互式 prompt（:331「请输入百炼 API Key」），保存后由 `_runtime_env`（:414-466）注入 `BRIDGES_QWEN_API_KEY`。Tavily Key 需要同构的第二套流程。
- 预算合同：`src/bridges/public_search_budget.py:7-10`（stage 8s、交接 750ms），本轮不变。
- 前端存在「DuckDuckGo 网页搜索」用户可见文案（学习模式证据门「补充来源」等），需随提供方切换更新。
- 搜索结果经 `_web_search_projection_from_result`（`src/bridges/chat/turn.py:183-261`）投影、`turn.py:903-958` 注入模型上下文，来源验证策略在 `src/bridges/web_search/` 与 `src/bridges/learning/evidence_coverage.py`（要求 `fetched_at`+`content_summary` 非空等）。

### 上下文指针

- `src/bridges/web_search/client.py`：DDG 客户端合同（搜索、正文抓取、错误分类），Tavily 客户端应对齐的投影形状。
- `src/bridges/web_search/service.py`：plan、最多 2 次请求、重试判定 `_retry_allowed`、challenge 冷却。
- `src/bridges/web_search/providers.py`：生产提供方装配点与 Brave 先例。
- `src/bridges/runtime/bootstrap.py:303-466`：凭据解析、交互 prompt、凭据库保存、运行环境注入。
- `src/bridges/config.py`（或 Settings 定义处）：新增 `tavily_api_key` 设置项的位置。
- `apps/web`：搜索提供方用户可见文案（证据门卡片、降级标注）。

## What to build

1. 新增 `TavilySearchClient`：直连 `https://api.tavily.com/search`（POST，`Authorization: Bearer <key>`），返回与现有搜索投影兼容的结果形状（url、title、content 摘要）；需要正文时用 Tavily Extract（或等价的既有抓取管道）填充 `content_summary` 与 `fetched_at`，满足来源验证策略。端点固定、走既有 SSRF/安全 URL 合同。
2. 错误分类与既有投影对齐但语义分离：timeout/connect/5xx 可重试；**401/403 = 配置错误（不可重试，中文文案明确提示检查 Tavily Key，不暗示稍后重试）**；429 = 限流（不立即重试，进入冷却）；响应契约解析失败单独成类。任何错误投影不得包含 Key 或完整请求/响应正文。
3. Settings 新增 `BRIDGES_TAVILY_API_KEY` / `BRIDGES_TAVILY_API_KEY_FILE`；生产组合根（`providers.py` 装配点）在通用网页搜索位**只注册 Tavily**，DDG 从生产注册表移除；缺 Key 时启动不失败，但联网搜索入口返回准确的「未配置搜索凭据」投影，前端如实标注，不得伪装搜索成功或静默退回 DDG。
4. 安装流程：`_resolve_qwen_key` 之后增加同构的 Tavily Key 解析——文件/环境变量/凭据库/交互 prompt「请输入 Tavily API Key（输入内容不会显示）：」，保存至系统凭据库（独立 credential id），`_runtime_env` 注入 `BRIDGES_TAVILY_API_KEY` 并清理同名旧环境变量。已保存凭据的再次启动不重复 prompt；凭据读取失败给出准确中文错误。
5. 保留第 6 轮建立的行为语义：每轮最多 2 次搜索请求、200ms 可取消退避、双 deadline 交接、错误保真、`attempts`/`provider_attempts`/`searched_at` 完整；提供方标识由 `duckduckgo` 改为 `tavily`。
6. 前端用户可见文案把「DuckDuckGo 网页搜索」更新为 Tavily 对应表述；「重试联网搜索」「本轮未联网核实」等既有交互不变。
7. 增加显式 opt-in 的真实 Tavily smoke（默认单测不联网）：用一个稳定低成本查询验证 Key、端点与解析合同；缺 Key/无网络时报告 `inconclusive`，绝不伪通过。

## 非目标

- 不在产品内引入 Tavily MCP/Node 侧集成（用户提供的 MCP 片段面向编码代理，与产品后端无关）。
- 不接 Brave/Bing/百度等任何第三提供方，不做多提供方 fan-out 或静默 fallback。
- 不把 Tavily Key 写入仓库、票据、日志、运行锁或发布报告；不改动 Qwen Key 既有流程。
- 不改变 8 秒公网阶段预算与 120 秒整轮预算；不改动 arXiv 独立检索链路（Issue 05）。
- 不重做学习模式降级语义（Issue 02）与证据门覆盖裁决算法本身。

## Acceptance criteria

- [ ] 配置有效 Tavily Key 后，学习模式/普通聊天联网搜索真实调用 `api.tavily.com` 并返回可核验来源；来源投影含 url/title、`fetched_at` 与非空 `content_summary`，能通过既有验证策略进入证据门。
- [ ] 生产组合中通用网页搜索提供方清单恰好只有 `tavily`；DDG 不再被生产注册；不存在任何静默 provider fallback。
- [ ] 401/403 投影为独立配置错误码、`can_retry=false`，中文文案提示检查搜索凭据；429 投影为限流且进入冷却；timeout/connect/5xx 语义与第 6 轮保持一致；各类错误在聊天终态可区分。
- [ ] 全新安装交互流程依次 prompt Qwen Key 与 Tavily Key，两者均入系统凭据库；再次启动直接复用不重复 prompt；`BRIDGES_TAVILY_API_KEY(_FILE)` 环境路径同样可用。
- [ ] 缺 Key 时联网搜索返回「未配置搜索凭据」的准确投影，应用正常启动，其余功能不受影响。
- [ ] 日志、运行锁、消息投影、SSE 事件与发布产物中不出现 Tavily Key 值（含部分遮蔽外的任何明文片段）；搜索 worker/请求不读取、不继承、不发送 Qwen Key。
- [ ] 前端证据门与降级文案不再出现「DuckDuckGo」字样；提供方标识为 `tavily`。
- [ ] 真实 smoke 输出 `passed`/`failed`/`inconclusive`，缺 Key 或无网络时不计入通过。

## Test plan

1. `httpx.MockTransport`（或仓库等价机制）单测 Tavily 客户端：结果映射、Extract/正文填充、错误矩阵（401/403/429/5xx/timeout/connect/解析失败）、请求不含 Qwen 凭据。
2. 服务层测试：plan/重试/冷却在 Tavily 错误类下语义正确，投影 `provider="tavily"`，`attempts` 与实际 HTTP 调用数一致。
3. 组合根测试：生产装配只注册 Tavily；缺 Key 时的投影与启动行为。
4. bootstrap 单测：prompt 顺序、凭据库读写、环境注入与旧变量清理、读取失败错误文案（注入式 prompt/store fake）。
5. 聊天级测试（`tests/chat/test_web_search_chat.py`、学习模式相关）：Tavily 成功/失败/降级终态、SSE 单一终态、可重载。
6. 前端：文案断言 + 降级卡片交互回归。
7. 显式联网环境执行真实 smoke 并保存脱敏证据（provider、状态、延迟、结果数）。

建议回归命令（实现者可按最终测试文件名等价调整）：

```powershell
python -m pytest tests/web_search tests/chat/test_web_search_chat.py tests/credentials -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue01
```

## Observability & rollback

- 沿用搜索审计字段并新增 `provider="tavily"`；记录尝试序号、脱敏查询指纹、HTTP 状态类别、耗时、错误码；不记录 Key、完整查询原文与响应正文。
- 不变量告警：`provider not in {tavily}`、缺 Key 却发出真实请求、错误投影含 `tvly-` 前缀串。
- 回滚：提供方注册点保留单开关回退能力；若 Tavily 上游异常，可临时关闭联网搜索并进入带标注的模型知识模式（Issue 02 语义），不得恢复 DDG 静默 fallback 或伪造成功。

## Blocked by

无。

## Comments

- 2026-08-14：用户确认以 Tavily 取代 DDG 并已申请 Key；Key 值只允许经安装流程进入系统凭据库，禁止写入仓库（含本票据）。
- 2026-08-14：本 issue 取代第 6 轮冻结决策 #2 的实现层；ADR 更新与 DDG 代码去留收口在 Issue 07。
- 2026-08-15：实现完成（分支 `01-tavily-search-provider`，worktree `.tmp/issue01-tavily-worktree`，conda 环境 `agent`）。要点：新增 `TavilySearchClient`（Bearer 认证、Tavily Extract 填充 `fetched_at`/`content_summary`，错误矩阵 401/403→`web_search_configuration`、429→限流+冷却、契约失败→`web_search_contract`，Extract 端点配置类错误透传不吞掉）；生产组合根只注册 Tavily，DDG 不再被生产注册，缺 Key 启动不失败并返回「未配置搜索凭据」投影；Settings 新增 `BRIDGES_TAVILY_API_KEY(_FILE)`，bootstrap 在 Qwen 之后同构 prompt「请输入 Tavily API Key」并保存至独立 credential id；前端证据门/降级文案与提供方标识切换为 tavily；发布门探针与漂移检查改为 Tavily；健康探针 TTL 由 30s 提升到 300s（Tavily 按量计费，降低固定探针成本）；真实 smoke 由 `BRIDGES_TAVILY_SMOKE=1` 显式启用，输出 passed/failed/inconclusive。回归：`tests/web_search`、`tests/chat/test_web_search_chat.py`、`tests/credentials`、`tests/runtime/test_bootstrap.py`、`tests/learning/test_teaching_gate.py`、`tests/closeout/test_release_gate.py`、`tests/integration/test_health_api.py` 全部通过（251 passed / 2 skipped）；发布门确定性检查全绿（python-tests/ruff/mypy/web-unit/web-typecheck）；Web typecheck 与 50 项单测通过；mypy 相对 main 基线无新增错误。代码评审（双轴）后修复：Extract 401/403 透传、配置错误文案去除「重试」暗示、聊天终态配置错误测试、错误矩阵 Key 不变量测试。ADR-0028 与 DDG 代码去留按票据约定收口在 Issue 07。

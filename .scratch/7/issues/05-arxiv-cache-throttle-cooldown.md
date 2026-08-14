# Issue 05：arXiv 结果缓存、请求节流与限流冷却

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-03

## 已验证现状与根因

生产观测：「给我找三篇关于Transformer的论文」第 1 次尝试「arXiv 搜索超时」，用户手动重试后「arXiv 请求过于频繁，请稍后重试」（429）。

- 阶段预算 10 秒（`src/bridges/chat/budget.py:33-36`），Windows 上 worker 子进程 spawn 与 httpx 导入消耗数秒；实际 HTTP 超时被压成剩余预算（`src/bridges/arxiv_mcp/client.py:136-137`）。
- 客户端层无重试、无缓存、无任何针对上游的节流/最小间隔（全库 grep 无 `cooldown|token bucket|min interval|throttle|cache` 命中）；429 分类在 `client.py:163-168`（`arxiv_rate_limit`，retryable=True）。
- 手动重试链路：`ArxivPaperSearchCard.tsx:171-179` → `retryChatRun` → `ChatService.retry_generation`（`src/bridges/chat/service.py:1636`）：前次 ERROR 后可立即重建 run 并再次打上游。第一次超时的请求已真实到达 export.arxiv.org，上游速率计数不撤销，立即重试落入限流窗口 → 429。
- worker 拓扑：主 worker 常驻串行复用 + 最多 3 个临时并发 worker（`process.py:138-200`），多 worker 间无上游协同限速；worker 环境继承 `HTTP_PROXY/HTTPS_PROXY`（`process.py:41-52`）。
- 本机实测 export.arxiv.org 2.2 秒返回 200；arXiv 官方建议请求间隔 ≥3 秒。
- 已冻结决策（本轮 #5）：结果缓存 + 3 秒节流 + 限流冷却与重试倒计时 + 阶段预算 10s→15s。

### 上下文指针

- `src/bridges/arxiv_mcp/client.py:77`、:136-189：默认超时、剩余预算截断、429/超时分类。
- `src/bridges/arxiv_mcp/process.py`：worker 池、重启与重发逻辑（:212-285）、代理环境（:41-52）。
- `src/bridges/arxiv_mcp/service.py:218-262`：投影与截止检查；`src/bridges/chat/turn.py:2694-2800`：论文路由执行与合成超时占位。
- `src/bridges/chat/service.py:1636-1764`：重试路径；`apps/web/src/components/bridges/ArxivPaperSearchCard.tsx:169-183`：重试按钮与错误展示。
- 缓存先例：`WebSearchCacheRepository`（`src/bridges/api/main.py:213`）。

## What to build

1. **结果缓存**：以规范化查询（大小写/空白/词序归一）为键缓存成功结果，TTL 10 分钟；仅缓存成功投影，失败与空结果不缓存；缓存命中在投影与审计中如实标记 `cache_hit`，卡片数据仍全部来自真实 Atom 响应。缓存不落敏感信息。
2. **请求节流**：在服务层（单例）对上游请求做进程级最小间隔 3 秒的串行化调度，覆盖所有 worker（含临时并发 worker）；等待计入阶段预算，剩余预算不足时不发起请求。
3. **限流/超时冷却**：429 或超时终态后进入短冷却（默认 20 秒，可配置单一常量来源）；冷却期内的重试（含手动重试）不打上游，直接返回准确错误投影并带剩余等待秒数；投影含 `retry_after_seconds`，错误码与既有分类保持一致。
4. **前端倒计时**：重试按钮在冷却期内禁用并显示剩余秒数，到期自动恢复；不引入轮询风暴（本地计时即可）。
5. **阶段预算**：`EXTERNAL_TIMEOUT_SECONDS["arxiv_search"]` 10s→15s，仍为单一预算来源；120 秒整轮预算不变。
6. 审计：缓存命中/未命中、节流等待时长、冷却拒绝次数、实际上游调用数均可查询；投影 `attempts` 与真实 HTTP 调用数一致。

## 非目标

- 不更换 arXiv 提供方、不引入镜像站或需 Key 的备用源。
- 不增加轮内自动重试（保持单发 + 手动重试经冷却约束）。
- 不改变查询构造合同（第 6 轮 Issue 01 已修复）与论文卡片数据来源合同。
- 不为缓存引入跨进程/持久化存储（进程内即可），不改变 run 120 秒总预算。

## Acceptance criteria

- [ ] 相同规范化查询 10 分钟内重复搜索命中缓存，上游调用数为 0，投影标记 `cache_hit`。
- [ ] 任意两个上游请求间隔 ≥3 秒（含并发 worker 场景）；节流等待不超出阶段预算。
- [ ] 429/超时后冷却期内的手动重试返回准确错误与 `retry_after_seconds`，上游调用数为 0；冷却到期后重试真实发起。
- [ ] 前端重试按钮冷却期内禁用并倒计时，到期恢复可点。
- [ ] 阶段预算 15 秒生效；超时、429、空结果、成功四类终态投影可区分且可重载。
- [ ] 「先超时、冷却到期后重试、成功」全链路不再出现 429 链式失败。

## Test plan

1. 客户端/服务层单测：缓存键归一化、TTL、仅成功缓存；节流间隔断言（fake clock）；冷却状态机（进入/拒绝/到期）。
2. `httpx.MockTransport` 纵向：超时→冷却→恢复→成功的调用次数与投影断言。
3. 聊天级：重试路径在冷却期内的投影与 SSE 终态；`tests/chat/test_arxiv_search_chat.py`、`tests/chat/test_natural_language_paper_route.py` 回归。
4. 前端：按钮倒计时与禁用态组件测试/E2E。
5. `tests/closeout/test_arxiv_worker_reliability.py` 回归，确认 worker 重启重发语义与节流叠加后调用数仍受控。

建议回归命令：

```powershell
python -m pytest tests/arxiv_mcp tests/chat/test_arxiv_search_chat.py tests/chat/test_natural_language_paper_route.py tests/closeout/test_arxiv_worker_reliability.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue05
```

## Observability & rollback

- 指标：`arxiv_upstream_calls`、`cache_hit_ratio`、`throttle_wait_ms`、`cooldown_rejections`；不含查询原文与响应正文。
- 回滚：缓存、节流、冷却各自独立开关可关；阶段预算回退 10 秒需同步回退对应测试。

## Blocked by

无。

## Comments

- 2026-08-14：本轮冻结决策 #5 全包范围即本 issue 的 What to build 1-5。
- 2026-08-14：用户网络下 arXiv 实测可达；429 主因是「超时请求已记账 + 立即重试无间隔」，不是上游永久封禁。

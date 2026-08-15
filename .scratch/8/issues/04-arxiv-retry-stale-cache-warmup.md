# Issue 04：arXiv 搜索可靠性组合包——有界重试、陈旧缓存兜底与 worker 预热

Status: ready-for-human

Type: task

Priority: P1

User stories: US-04

## 已验证现状与根因

生产观测（2026-08-15 截图）：「给我找5篇与Transformer相关的影响深远的论文」→「arXiv 论文搜索未完成 / arXiv 搜索超时，请重试。」，间歇性出现。第 7 轮 Issue 05 已落地缓存/节流/冷却（`.scratch/7/issues/05-arxiv-cache-throttle-cooldown.md`），但间歇性超时仍发生。代码事实（无缺陷级 bug，为预算挤压叠加）：

- **Windows 冷启动吃预算**：新 worker 需 spawn 子进程 + import httpx + 握手（5s 上限，`process.py:68`）；主 worker 常驻但并发请求的临时 worker 每次冷启动（`process.py:179-199`）。
- **HTTP 超时 = 剩余预算**（`client.py:136-137`）：冷启动 + 3s 节流排队（`guard.py:30-87`，等待计占预算）后留给境外上游（export.arxiv.org）的窗口可能只剩几秒，RTT 抖动即撞线。
- **节流预算不足直接合成超时**：`wait_for_request_slot` 返回 None 时未发请求即报超时（`service.py:270-276`）。
- **HTTP 层零重试**：单发；仅 worker 进程级故障有 1 次重启重发（`process.py:73,212-285`），worker 上报的 timeout/429 不触发重发（:248-273）。第 7 轮 Issue 05 明确"轮内不自动重试"为非目标——本轮 grilling 已推翻该决策。
- **无陈旧兜底**：缓存只存成功结果（TTL 600s），失败/空结果不缓存，上游抖动时重复查询也拿不到任何结果。
- 冷却放大感知：超时终态激活 20s 冷却（`service.py:327-330,355`），用户立即重试继续失败。

已冻结决策（本轮 grilling #2）：可靠性组合包——HTTP 层 1 次自动重试 + 陈旧缓存兜底 + worker 预热 + 阶段预算 15s→20s；**不**引入镜像端点。

### 上下文指针

- `src/bridges/arxiv_mcp/client.py:77-79,136-189`：超时计算、单次 GET、错误分类。
- `src/bridges/arxiv_mcp/service.py:232-242,270-276,327-330,393-398`：缓存查询、节流预算不足、冷却激活、命中标记。
- `src/bridges/arxiv_mcp/cache.py`、`guard.py`、`limits.py:8-20`：第 7 轮三件套与回滚开关先例。
- `src/bridges/arxiv_mcp/process.py:68-74,139-141,301-383`：worker 池、spawn、握手。
- `src/bridges/chat/budget.py:40-45`：阶段预算 15s；`src/bridges/api/main.py:880-884`：服务单例装配点（预热挂载点）。
- 前端：`apps/web/src/components/bridges/ArxivPaperSearchCard.tsx`（错误态/重试按钮/倒计时）。
- 既有测试：`tests/arxiv_mcp/test_cache_throttle_cooldown.py`、`test_client_and_service.py`、`tests/closeout/test_arxiv_worker_reliability.py`。

## What to build

1. **HTTP 层有界自动重试**：超时/网络类错误时，若剩余预算 ≥ 阈值（单一常量，默认 5s）自动重发 1 次；429 不重试（维持冷却语义）、4xx 不重试；投影 `attempt_count` 如实反映真实上游调用数；重试不绕过节流最小间隔。
2. **陈旧缓存兜底**：缓存条目过期后保留为 stale（保留窗口单一常量，默认 24h）；仅当本次上游调用失败（超时/429/网络）且存在同键 stale 条目时兜底返回，投影标记 `stale: true`，前端卡片标注「结果可能不是最新」；失败/空结果仍不缓存；账户隔离不变。
3. **worker 预热**：应用启动时预 spawn 常驻 worker 并完成握手（含 httpx 导入），首次搜索不再付冷启动；预热失败仅记日志、保留懒启动兜底；可配置开关关闭。
4. **阶段预算上调**：`EXTERNAL_TIMEOUT_SECONDS["arxiv_search"]` 15s→20s（`budget.py:40-45`），同步 ADR-0025 与相关测试；120s 整轮预算不变。
5. **独立回滚开关**：重试 / stale 兜底 / 预热各自独立开关（`limits.py` 既有模式）。

## 非目标

- 不引入镜像端点或更换提供方；不改变查询构造合同与卡片数据来源合同。
- 不改变节流 3s 间隔与冷却 20s 语义；不引入跨进程/持久化缓存。
- 不为临时并发 worker 做预热（只预热常驻主 worker）。

## Acceptance criteria

- [ ] 单次瞬时超时在预算允许时自动重试成功，用户无感知；`attempt_count=2` 且上游调用数一致；429/4xx 不重试。
- [ ] 上游失败且存在同键 stale 缓存时返回陈旧结果并标注；无 stale 缓存时维持原错误投影；stale 条目不超保留窗口。
- [ ] 应用启动后首次搜索无 worker 冷启动耗时（握手已在启动期完成）；预热失败时懒启动兜底正常。
- [ ] 阶段预算 20s 生效；冷却/节流既有测试不回归；三个开关各自可独立回滚。
- [ ] 「瞬时抖动→自动重试→成功」与「上游持续故障→stale 兜底→标注展示」两条链路端到端可验证。

## Test plan

1. 单测：重试预算阈值（不足不重试）、错误码选择性重试、attempt_count 计数；stale 缓存 TTL/保留窗口/仅失败兜底/账户隔离；预热开关与失败兜底。
2. `httpx.MockTransport` 纵向：首次超时→重试→成功；持续失败→stale 返回→标注。
3. 聊天级：重试成功与 stale 兜底的 SSE 终态投影；前端卡片 stale 标注测试。
4. 回归命令：

```powershell
python -m pytest tests/arxiv_mcp tests/chat/test_arxiv_search_chat.py tests/chat/test_natural_language_paper_route.py tests/closeout/test_arxiv_worker_reliability.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r8-issue04
```

## Observability & rollback

- 指标新增：`arxiv_retry_attempts`、`arxiv_retry_successes`、`arxiv_stale_serves`、预热成功/失败计数；接入既有 `metrics_snapshot()`（`service.py:511-527`）。
- 回滚：重试/stale/预热三开关独立可关；阶段预算回退 15s 需同步回退对应测试与 ADR-0025 注记。

## Blocked by

无。

## Comments

- 2026-08-15：第 7 轮 Issue 05 的「轮内不自动重试」非目标经本轮 grilling #2 决策推翻，以有界（预算阈值门控）单重试为限，避免重回「超时→链式 429」形态——重试受节流间隔与冷却约束。
- 2026-08-15（实现）：分支 `04-arxiv-retry-stale-cache-warmup` 经 worktree 实现并提交，验收自检全绿（见下）。实现要点：
  - 重试在服务层围绕 HTTP 调用实现（`service.py` 有界尝试循环）：剩余预算 ≥ `ARXIV_RETRY_MIN_BUDGET_SECONDS`（5s）才重发；错误码集合 `{arxiv_timeout, arxiv_offline}`（超时/网络/5xx 瞬时类）；429/4xx/parse 不重试；重发前再次经 `ArxivThrottle.wait_for_request_slot`（不绕过节流最小间隔）；`attempt_count` 如实反映真实上游调用数。
  - stale 兜底：`cache.py` 条目保留 `ARXIV_STALE_RETENTION_SECONDS`（24h）过期窗口，`get_stale` 只在条目已过期且未超保留窗口时返回；服务层仅在最终失败错误码 ∈ `{arxiv_timeout, arxiv_rate_limit, arxiv_offline}` 时查询；投影 `stale: true` 并计入审计与 `metrics_snapshot()`；失败/空结果仍不写缓存；账户隔离不变；冷却 20s 语义不变（失败后仍进入冷却）。
  - 预热：`ArxivMcpProcessClient.warmup()`（spawn + 握手，含 httpx 导入）失败仅记日志、进程句柄清理、懒启动兜底不变；`main.py` 装配点仅对非 test 环境、非 closeout 替身模式调用 `ArxivSearchService.warmup()`（test 环境的应用装配不产生子进程）。
  - 预算：`EXTERNAL_TIMEOUT_SECONDS["arxiv_search"]` 15s→20s，ADR-0025 注记与 `test_issue05_deadline_cancellation.py` 同步；120s 整轮预算不变。
  - 开关：`ARXIV_RETRY_ENABLED` / `ARXIV_STALE_FALLBACK_ENABLED` / `ARXIV_WARMUP_ENABLED` 独立可关（`limits.py`）。
  - 判定说明：`arxiv_offline` 同时覆盖 network 与 http_5xx，二者都进入重试与 stale 集合（5xx 属「上游持续故障」，与 US-04 对齐）；冷却拒绝路径（未打上游）不触发 stale 兜底，严格按「仅本次上游调用失败」执行。
  - 验证：`tests/arxiv_mcp/test_retry_stale_warmup.py`（重试阈值/错误码选择/attempt_count/节流间隔/stale 窗口与隔离/三开关）、`test_process.py` 与 `tests/closeout/test_arxiv_worker_reliability.py`（真实 worker 预热与失败兜底）、`tests/api/test_arxiv_warmup_wiring.py`（装配点）、`tests/chat/test_arxiv_retry_stale_chat.py`（SSE 终态投影）、`ArxivPaperSearchCard.test.tsx`（stale 标注）；回归命令 135 passed / 1 skipped，mypy 与 ruff 干净，前端 tsc + vitest 全绿。
- 2026-08-15（code-review 修复轮）：双轴审查（standards + spec）后收口——错误码策略收敛为单一 `_UPSTREAM_ERROR_POLICY` 映射（重试/stale/冷却共用，消除三处重复分类）；取消投影+审计抽为 `_finish_cancelled` 共用；预热开关单一来源（`process.py` 直接读 `ARXIV_WARMUP_ENABLED`，删除重复构造参数），且预热期任意异常都计入失败计数；预算阈值门控测试改用真实截止时间（4s 不重试 / 8s 重试，真正钉住 5s 阈值）；`retry_successes` 明确只统计 SUCCESS 终态（EMPTY 对用户可见，不计入「无感知重试成功」）。已知边界：`attempt_count` 反映服务层上游调用次数，worker 进程级崩溃重启内的重发不并入（属既有 issue 05 机制，日志已有 restarts 计数）。全量 `pytest tests` 在本机受两个既有问题阻断（重复 basename 收集冲突、`test_runtime_smoke.py` 因本机凭据库存有全局 Qwen Key 使 `start` 长驻挂起——未改动 main 同样复现），排除这两个文件后全量套件通过。

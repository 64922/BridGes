# Issue 03：修复 DDG deadline 交接、错误保真与有限重试

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-WEB-01、US-WEB-02、US-WEB-03、US-WEB-04

## 已验证现状与根因

- 已检查真实生产会话：联网搜索被触发，但最终投影为 `web_search_timeout`，`attempts`、查询记录和 `provider_attempts` 均为 0，`searched_at` 为空，总耗时约 54.8 秒；同一轮真实 Qwen 文本调用成功。这说明用户看到的是搜索编排的合成超时，而不是一份能回答“DDG 实际发生了什么”的审计记录。
- 已用确定性时序复现：`src/bridges/chat/turn.py` 的 `_parallel_search()` 与 `WebSearchService` 子任务共享同一个绝对 deadline。外层到点后设置停止信号并把仍不在 `done` 集合中的 future 标成 `_SEARCH_TIMEOUT`；future 如果在随后最多 500ms 的清理窗口内完成，结果只被回收却不会重新纳入 `done`/结果映射。因此真实投影会被稀疏的合成超时覆盖。
- `src/bridges/web_search/service.py::_run_queries()` 存在同形问题：到点时给未在 `done` 中的 future 写入新建的 `web_search_timeout`，随后清理阶段完成的真实错误/结果不再被消费。两层使用同一边界，没有为错误归类、投影持久化和线程回收预留交接时间。
- 外层遇到异常时还会统一改写成 `web_search_request`；遇到 `_SEARCH_TIMEOUT` 则重新构造只有 trigger/query 的超时投影。已有 DDG 错误码、HTTP 状态类别、查询次数、提供方尝试和真实耗时因此丢失。
- 当前环境后续健康检查曾在约 1.3 秒内就绪，同类实际搜索也曾在约 5.6 秒返回 5 条结果（其中 3 条验证）。这证明 DDG 并非持续不可达；历史故障的上游原因已经无法从稀疏投影中还原，不能把它简单归因于 DDG 永久不可用。
- 用户已明确决定：通用公网搜索只使用 DuckDuckGo，不申请或接入 Brave/Tavily/其他备用 Key；DDG 最终失败时，允许真实全局 Qwen 以模型知识回答，但必须显著标注“本轮未联网核实”、不得给出伪引用，也不得推进依赖联网证据的教学计划/课次进度。

### 上下文指针

- `src/bridges/chat/turn.py:2253-2379`：公开搜索调用构造、共享 deadline、结果投影和合成超时。
- `src/bridges/chat/turn.py:3599-3683`：`_parallel_search()` 的 wait/cleanup/result 交接。
- `src/bridges/web_search/service.py:763-845`：DDG 查询 future 的内部 wait/cleanup/result 交接。
- `src/bridges/web_search/client.py`：DDG HTTP 请求、超时/连接/权限/限流/解析分类与来源抓取。
- `tests/chat/test_issue06_latency_budget.py`、`tests/chat/test_issue05_deadline_cancellation.py`：聊天阶段预算和取消语义。
- `tests/web_search/test_duckduckgo_service.py`、`tests/chat/test_web_search_chat.py`：DDG 服务投影及聊天降级。

## What to build

1. 定义单一的公网搜索时间预算契约：`stage_deadline` 是从进入 PUBLIC_SEARCH 阶段起的 8 秒用户可见硬截止；`provider_deadline` 默认比它提前 750ms，用于 DDG 网络尝试。750ms 交接预留必须在 `chat/budget.py`（或等价单一预算模块）中只有一个常量来源，并允许测试按比例缩放，不能散落魔法数。
2. 重构两层 future 消费，使“截止边界前完成”有唯一判定。进入有界清理窗口后完成的 future 必须被再次消费：如果在 DDG 子 deadline 内已完成，保留其真实结果/错误；如果确实越过 deadline，则标记真实超时。不能仅根据某一轮 `wait()` 的旧 `done` 快照覆盖结果。
3. 将停止原因显式区分为用户取消、DDG provider deadline、PUBLIC_SEARCH stage deadline 和父级终止。用户取消始终优先投影为不可重试的 `web_search_cancelled`；DDG 在子预算内返回的超时保留 provider timeout；只有非合作任务直到 8 秒硬截止仍没有形成投影时，才使用独立的 `web_search_stage_timeout`。父级终止不能伪装成用户取消或 DDG 已确认超时。
4. 实现 **有限重试**：一轮联网搜索最多向 DuckDuckGo 发出 2 次搜索请求（首次 + 1 次重试或一次有界查询改写，两者不能同时追加成第三次）。只对连接重置、DNS/offline 暂态、明确可重试 5xx 执行一次重试；timeout 仅在剩余预算足够时重试。challenge、429/rate-limit、权限、取消、重定向策略、响应过大、不安全 URL和解析契约不得立即重试，避免放大封禁或浪费剩余预算。
5. 重试前固定使用 200ms 可取消、deadline-aware 的退避，并允许 fake clock 测试。剩余预算不足“200ms 退避 + 最小请求窗口 + 750ms 交接预留”时不得启动第二次。不得在失败后切换 Brave、Bing、Tavily、Serper、模型“搜索”或任何第二公网提供方。
6. 让每次真实尝试都进入投影/审计：提供方固定为 `duckduckgo`，包含尝试序号、脱敏查询摘要或指纹、开始/结束时间、耗时、结果码、HTTP 状态类别和是否计划重试。整轮投影必须保留 `attempts`、query history、`provider_attempts` 与最终 `searched_at`。
7. 保留最有信息量的最终错误。若两次均失败，终态按最后一次真实尝试与既有优先级归类，不得由外层再覆写成无证据的 `web_search_request`/稀疏 timeout；意外内部异常使用独立内部错误码。
8. 搜索失败后的聊天继续通过真实 Qwen 文本能力生成谨慎的模型知识回答，并在正文/状态中明确“本轮未联网核实”。该回答不包含本轮网络来源、伪 URL 或伪引用，且不推进依赖联网材料的教学计划、课次或证据进度；UI 保留可重试入口。

## 非目标

- 不接入 Brave、Tavily、Bing、Serper、SearXNG 或任何其他搜索提供方，也不新增搜索 Key 配置。
- 不把 Qwen 当作搜索引擎，不让模型声明它已浏览网页，不从模型输出伪造 `web_search` 成功投影。
- 不无限延长公网阶段、聊天整轮预算或线程清理时间；8 秒总上限不变。
- 不对每个拆分 query 各自重试两次；本轮 DDG HTTP 搜索请求总数硬上限为 2。
- 不改变 arXiv 的独立检索契约；并行执行时两者仍共享公开搜索阶段总预算，但终态分别保存。
- 不在本 issue 重做前端搜索卡片视觉样式或 DDG 健康发布探针（后者由 Issue 04 负责）。

## Acceptance criteria

- [ ] PUBLIC_SEARCH 从开始到返回调用方的墙钟耗时不超过 8 秒加 100ms 测量容差；`provider_deadline = stage_deadline - 750ms`，两者及交接预留来自同一预算模块并可在测试中缩放。
- [ ] future 在截止边界附近完成时，其真实成功投影或真实 `WebSearchError` 被消费一次且仅一次，不会被旧 `done` 快照替换成 `_SEARCH_TIMEOUT`。
- [ ] DDG 在 provider deadline 内形成的真实 timeout 保留提供方尝试元数据；非合作 future 直到 stage deadline 仍无投影时才使用 `web_search_stage_timeout`；用户取消投影为 `web_search_cancelled` 且 `can_retry=false`，三者在竞争条件下语义稳定。
- [ ] 成功首次请求时 DDG 调用数为 1；可重试暂态错误后成功时调用数为 2；连续失败或剩余预算不足时调用数不超过 2。
- [ ] challenge、429/rate-limit、权限、响应过大、不安全 URL、不可接受重定向、明确不可重试 4xx 与解析契约错误只调用 1 次。
- [ ] 第二次尝试不会超过总 deadline；固定 200ms 退避能被用户取消打断；剩余预算不足最小请求窗口和交接预留时不重试；执行器不会留下阻止测试/进程退出的非守护等待。
- [ ] 每次已启动尝试都保存 provider=`duckduckgo`、attempt number、耗时、结果码和脱敏状态类别；终态 `attempts` 与实际 HTTP 调用数一致，不再出现“真实请求已发生但 attempts=0”的稀疏记录。
- [ ] 外层聊天编排不再把服务返回的 timeout/connect/dns/rate-limit/provider/permission/parse 等错误覆盖成通用 `web_search_request`。
- [ ] DDG 成功时只展示真实返回并通过验证策略的来源；DDG 失败时 `sources=[]`，正文显著包含“本轮未联网核实”，且不出现“已联网”“已查到”或可点击伪引用。
- [ ] 学习模式的 DDG 失败轮不创建/推进 teaching plan、lesson progress 或联网证据；用户点击重试后能重新执行 DDG，并且新旧尝试记录可区分。
- [ ] 普通聊天、学习模式、强制联网意图以及 arXiv+DDG 并行场景均保持终态可重载，SSE 只发出一个最终搜索终态。
- [ ] 生产组合中通用搜索提供方列表只有 DuckDuckGo；缺少任何备用 Key 不影响启动，也没有静默 provider fallback。

## Test plan

1. 为 `_parallel_search()` 添加确定性边界测试：用受控 event/barrier 让 future 分别在子 deadline 前、恰在交接窗口、子 deadline 后和用户取消后完成，断言结果、调用次数和墙钟上限。
2. 为 `WebSearchService._run_queries()` 添加同样的边界测试，特别验证清理窗口完成的 future 会被重新消费，而非丢弃真实错误/结果。
3. 参数化测试错误矩阵与 retryable 标志；用 fake clock/可注入退避避免真实 sleep，断言 connect/DNS/offline/5xx 可重试，timeout 受剩余预算约束，challenge/429/permission 不立即重试，以及 200ms 退避可被取消。
4. 扩展 `tests/web_search/test_duckduckgo_service.py`：验证 `attempts`、query history、`provider_attempts`、`searched_at`、HTTP 状态类别及错误优先级。
5. 扩展 `tests/chat/test_web_search_chat.py`：真实搜索成功、两次失败、取消、重试成功、意外异常以及模型知识降级；使用模型 spy 断言失败后仅产生真实文本调用，未产生来源。
6. 在学习模式测试中断言 DDG 失败轮正文标签、引用为空、教学计划/课次不推进；重试成功后才允许基于真实来源继续。
7. 增加 production-composition 测试，断言通用搜索客户端仅为 DuckDuckGo，所有 fallback client/key 均未注册。

建议回归命令：

```powershell
python -m pytest tests/web_search tests/chat/test_web_search_chat.py tests/chat/test_issue05_deadline_cancellation.py tests/chat/test_issue06_latency_budget.py tests/chat/test_teaching_chat.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-issue03
```

## Observability & rollback

- 记录整轮 search run ID、provider、尝试序号、阶段/网络/收尾预算、实际耗时、结果数、错误码、HTTP 状态类别、是否重试及终止原因；只保留脱敏 query 指纹/摘要，不记录页面正文、Cookie、Authorization 或 Qwen Key。
- 增加不变量指标：`actual_http_calls != projection.attempts`、`completed_future_overwritten_as_timeout`、`public_search_budget_overrun`、`provider != duckduckgo` 任一出现都应进入发布告警。
- 将 Qwen 模型知识降级作为独立阶段审计：明确 search 失败与 model 调用是两件事，不能把模型成功计作 DDG 成功。
- 上线可分两步：先启用新观测字段并对照旧行为，再启用新 deadline/重试。若出现回归，可回滚一次重试为单次 DDG，但应保留 deadline 交接和错误保真修复。
- 紧急止损可临时关闭联网搜索并直接进入带标签的模型知识模式；不得恢复无限等待、添加未批准备用提供方或把失败记录改为成功。

## Blocked by

无。

## Comments

- 2026-08-13：用户最终确认只使用 DuckDuckGo + 真实 Qwen 模型知识模式，不申请任何备用搜索 Key。
- 2026-08-13：每轮最多 2 次 DDG 请求、总公网阶段 8 秒、失败后无引用且不推进教学进度，均为本 issue 的硬边界。
- 2026-08-13：真实生产故障的原始 DDG 原因因当前错误覆写已不可恢复；实现不得假设历史故障一定来自 DDG 上游。

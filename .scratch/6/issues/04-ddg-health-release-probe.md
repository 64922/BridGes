# Issue 04：将 DDG 健康探针接入降级状态与发布门

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-WEB-OPS-01、US-WEB-OPS-02、US-WEB-OPS-03

## 已验证现状与根因

- `DuckDuckGoClient.health_check()` 已经会向固定 DDG HTML 端点发送不含用户内容的探针查询，并把 DNS、连接、权限、限流和上游错误映射为 `WebSearchHealth`；`WebSearchService.health_check()` 也能聚合已登记提供方。
- 但应用级 `/health`、发布门和用户可见的联网降级状态目前没有把这份 DDG 健康证据闭环使用。数据库/进程健康可以是绿色，而唯一通用搜索源事实上不可访问；上线前也不会因为 DDG 契约漂移、HTML 解析失败、网络策略阻断而失败。
- 用户已确定不使用备用搜索源。对当前产品而言，“至少一个 provider READY”不再是多源容错语义，而应精确等价于“DuckDuckGo READY”；出现 Brave/Tavily 等 provider 反而是配置漂移。
- 健康检查必须与用户查询隔离：不得使用真实用户问题、账户上下文或 Qwen Key；也不能在每个 `/health` 请求中无缓存地访问 DDG，导致自身限流或拖慢基础存活探针。
- Issue 03 会修复真实搜索的 deadline、错误保真和有限重试。本 issue 不能在旧错误投影不可信时先宣称发布门完整，因此实现依赖 Issue 03。

### 上下文指针

- `src/bridges/web_search/client.py:207-234`：DDG 固定查询健康检查与错误映射。
- `src/bridges/web_search/service.py:406-450`：提供方健康聚合；当前仍保留 fallback client 结构。
- `src/bridges/web_search/contracts.py`：`WebSearchHealthStatus`、provider health 与 summary 契约。
- `src/bridges/api/main.py`：应用 health 组合入口。
- `tests/integration/test_health_api.py`、`tests/contracts/test_health_contract_roundtrip.py`：健康 API 与契约回归。
- `tests/web_search/test_duckduckgo_service.py`、`tests/web_search/test_public_search_fallback.py`：现有客户端/聚合健康测试。
- `scripts/release_gate.py`、`tests/closeout/test_release_gate.py`：发布证据和非零退出契约。

## What to build

1. 将公网搜索健康定义收敛为唯一提供方 DuckDuckGo。生产组合、健康摘要和发布报告只能出现 provider=`duckduckgo`；检测到其他通用搜索提供方时以配置漂移失败。
2. 建立分层探针：基础进程 liveness 不访问外网；readiness 返回最近一次 DDG 健康快照及新鲜度；显式 release probe 必须发起一次真实、低成本、不含用户数据的 DDG 查询并验证响应可解析。
3. 为运行期健康快照设置短期缓存与过期规则，避免每个健康请求击穿 DDG。并发刷新应合并，刷新失败保留“最后一次成功时间”和当前失败，不得把旧成功无限期伪装成 READY。
4. 将 DNS、connect/offline、timeout、permission、rate_limit、provider、parse、response-too-large/redirect 等状态映射到稳定、脱敏的 readiness/release 字段；不回显响应正文、IP、代理凭据或本地网络细节。
5. 把健康状态接入运行期降级：DDG 明确非 READY 时，新的联网请求仍可按 Issue 03 做一次受控真实尝试（避免陈旧探针永久熔断），但 UI/消息要立即显示“联网服务异常，正在尝试/可重试”，失败后进入带标签的模型知识模式。健康恢复后无需重启即可恢复真实搜索。
6. 将真实 DDG probe 接入显式发布门。release 模式中只有 DDG 返回可解析结果且 provider/version/错误语义符合契约才通过；网络未授权或外部服务暂时不可达可报告 `inconclusive`，但必须导致发布命令非零退出，等待人工判定，不能由 mock/fixture 替代。
7. 提供运维诊断命令或现有 release gate 子参数，输出机器可读 JSON 和简洁中文摘要；同一次运行中记录探针时间、状态、耗时、provider version、结果数量与稳定错误码。

## 非目标

- 不接入或探测 Brave、Tavily、Bing、Serper、SearXNG 等备用源，不新增搜索 Key。
- 不让应用启动硬依赖公网；DDG 故障时基础聊天、本地功能和带标签的 Qwen 模型知识模式仍应可用。
- 不在每次 liveness/readiness HTTP 请求中同步访问 DDG，也不建立高频后台爬取任务。
- 不使用用户搜索词作为健康查询，不保存 DDG 响应正文，不对外暴露敏感网络配置。
- 不重新实现 Issue 03 的重试、deadline、错误保真或教学降级规则。

## Acceptance criteria

- [ ] 基础 liveness 在 DDG 断网时仍快速返回进程存活；readiness 独立暴露 web search 状态，不把数据库健康等同于 DDG 健康。
- [ ] 生产公网搜索健康摘要只包含一个 provider=`duckduckgo`；出现任何备用 provider 或 fallback key 注册时发布门失败并给出稳定配置错误码。
- [ ] 首次 readiness 可触发/读取受控刷新，缓存有效期内的并发请求不会重复访问 DDG；过期后至多一个刷新执行，其余请求读取有新鲜度标识的快照。
- [ ] 快照包含 status、checked_at、age、latency、provider version、last_success_at 与脱敏 error_code，不包含健康查询正文、响应正文、Cookie、Authorization、代理 URL 或 Qwen Key。
- [ ] READY 只在固定 DDG 端点返回可接受状态且最小结果可被真实解析时产生；仅 DNS 可达、HTTP 200 或非空 HTML 不足以判定 READY。
- [ ] 连续失败后状态不会继续显示旧 READY；恢复成功后无需进程重启即可回到 READY，并更新 `last_success_at`。
- [ ] DDG 非 READY 时，新联网轮次清楚显示搜索降级状态，最终失败走 Issue 03 的“本轮未联网核实”Qwen 回答；本地/普通模型功能不被 health probe 阻断。
- [ ] `release_gate --real-probes`（或等价正式入口）在真实 DDG probe 失败、inconclusive、provider 漂移或解析契约漂移时非零退出；mock/fixture/cassette 无法让真实发布门变绿。
- [ ] 发布报告能区分 DNS、连接、超时、权限、限流、上游状态和解析失败，且可供 CI/人工复核。
- [ ] 探针有严格超时、响应大小与重定向约束，失败不会阻塞应用退出或积累后台线程。

## Test plan

1. 为健康快照缓存添加 fake-clock 单元测试：首次刷新、缓存命中、过期、并发合并、连续失败、旧成功过期和恢复。
2. 用 `httpx.MockTransport` 覆盖 DDG 200 可解析、200 契约漂移、DNS、connect、timeout、403、429、5xx、重定向和超大响应，断言稳定健康状态。
3. 扩展 `tests/integration/test_health_api.py`，验证 liveness 与 readiness 分离、脱敏、延迟上限以及 DDG 故障不拖垮基础应用。
4. 扩展 production-composition 测试，断言健康 providers 恰为 `duckduckgo`；注入备用 client/key 时必须失败。
5. 扩展 `tests/closeout/test_release_gate.py`：真实探针成功、失败、inconclusive、fixture 冒充和报告字段；断言退出码。
6. 在显式允许联网的环境运行真实 DDG release probe，一次成功后再用受控网络失败验证诊断分类和模型知识降级。

建议回归命令：

```powershell
python -m pytest tests/web_search tests/integration/test_health_api.py tests/contracts/test_health_contract_roundtrip.py tests/closeout/test_release_gate.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-issue04
python scripts/release_gate.py --real-probes
```

## Observability & rollback

- 输出 `ddg_health_status`、`probe_latency_ms`、`snapshot_age_ms`、`last_success_at`、`error_code`、`provider_version` 与刷新合并次数；禁止记录查询/响应正文和凭据。
- 对 `stale_ready`、`probe_timeout`、`parse_contract_drift`、`unexpected_search_provider` 建立明确告警；告警本身不得自动切换提供方。
- 先以只读报告模式接入 readiness，再启用发布阻断；启用前保存一份真实基线，避免把环境网络未授权误判为代码回归。
- 如探针导致健康端点负载异常，可回滚 readiness 的自动刷新，改由显式诊断命令刷新；不能删除真实 release probe 或把最后一次成功永久当作当前 READY。

## Blocked by

- [Issue 03：修复 DDG deadline 交接、错误保真与有限重试](./03-ddg-deadline-error-retry.md)

## Comments

- 2026-08-13：用户确认 DuckDuckGo 是唯一通用联网提供方；健康门不保留备用源扩展位作为当前生产行为。
- 2026-08-13：运行时 DDG 故障不得阻止应用启动，但正式发布不能在真实探针未通过时被 fixture 宣告成功。

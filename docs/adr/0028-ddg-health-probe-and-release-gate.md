# DDG 健康证据闭环：唯一提供方、分层探针与发布门

Issue 04 起，公网搜索健康证据形成闭环：`DuckDuckGoClient.health_check()` 的
真实探测结果经运行期快照监视器（`WebSearchHealthMonitor`）缓存、过期、合并
刷新后，进入应用级 readiness/degraded 投影、聊天降级提示与真实发布门。本
ADR 变更的是健康与发布语义，不改变 Issue 03 的搜索 deadline、错误保真与
有界重试合同。

## 唯一提供方语义（替代“至少一个 provider READY”）

当前产品唯一通用联网提供方是 DuckDuckGo。健康聚合不再使用多源容错语义：
出现 Brave、Tavily、Bing、Serper、SearXNG 等任何其他通用搜索提供方（备用
客户端、备用 Key、fallback 开关）都视为配置漂移，组合期以稳定错误码
`unexpected_search_provider` 失败关闭，健康摘要与发布报告也只允许出现
`provider=duckduckgo`（arXiv 是独立论文检索，不属于通用网页搜索提供方）。
ADR 0025 中备用提供方的预算保留语义随之作废，只保留历史审计含义。

## 分层探针

- **liveness**：只回答进程存活，不访问外网、不携带依赖。
- **readiness**：返回最近一次 DDG 健康快照与新鲜度（`web_search` 依赖 +
  脱敏 extensions：`ddg_health_status`、`ddg_provider_version`、
  `ddg_probe_latency_ms`、`ddg_snapshot_age_ms`、`ddg_last_success_at`、
  `ddg_error_code`、`ddg_stale_ready`、`ddg_refresh_count`）。DDG 不是必需
  依赖：故障只置 `degraded=fail`，基础聊天与本地功能继续服务。
- **release probe / 运维诊断**：显式入口（`release_gate.py --real-probes`
  与 `--web-health`）发起一次真实、低成本、不含用户数据的 DDG 查询，只有
  结果可解析、语义匹配且 provider/version 与合同一致才通过；mock、fixture、
  cassette 无法使其变绿。网络未授权或外部服务暂时不可达报告
  `inconclusive` 并令发布命令非零退出，等待人工判定。

## 快照缓存与新鲜度

TTL 内并发读取只读内存快照，不访问 DDG；过期后至多一个后台刷新执行，其余
请求读取带 `stale_ready` 新鲜度标识的快照。刷新失败保留 `last_success_at`
与当前失败状态，绝不用旧成功无限期伪装 READY——旧成功超过上限后以
`web_search_stale_ready` 失败关闭。探测有严格超时（默认 5s）与合并刷新，
守护线程不阻塞进程退出、不积累后台线程。健康恢复无需重启进程，下一次真实
探测成功即回到 READY 并更新 `last_success_at`。

## 运行期降级与消息

DDG 明确非 READY 时，新联网轮次仍按 Issue 03 做一次受控真实尝试（避免
陈旧探针永久熔断），但 loading 投影立即显示“联网服务异常，正在尝试联网；
失败将进入模型知识降级”，最终失败走 Issue 03 的“本轮未联网核实” Qwen
模型知识降级；本地/普通模型功能不被健康探针阻断。

## 脱敏边界

健康快照、健康投影与发布报告只包含稳定状态、时间戳、耗时、版本与错误码，
绝不包含健康查询正文、响应正文、Cookie、Authorization、代理 URL、IP 或
任何凭据；对 `stale_ready`、`probe_timeout`、`parse_contract_drift`、
`unexpected_search_provider` 建立明确告警，告警本身不得自动切换提供方。

"""arXiv 上游可靠性常量：结果缓存、请求节流与限流冷却的单一配置来源。

本模块不依赖任何其他 bridges 包，供缓存、节流、冷却与服务层共同读取，
避免循环依赖；所有数值与回滚开关集中在此，改一处即可全局生效。
"""

#: 相同规范化查询的结果缓存 TTL（秒）：10 分钟。
ARXIV_CACHE_TTL_SECONDS = 600

#: 上游请求最小间隔（秒）：进程级串行化调度，覆盖全部 worker
#: （含临时并发 worker）。arXiv 官方建议请求间隔 ≥3 秒。
ARXIV_MIN_REQUEST_INTERVAL_SECONDS = 3.0

#: 429/超时终态后的上游冷却（秒）：冷却期内的重试不打上游。
ARXIV_COOLDOWN_SECONDS = 20

#: 独立回滚开关：缓存、节流、冷却各自可关，关闭即回到旧行为。
ARXIV_CACHE_ENABLED = True
ARXIV_THROTTLE_ENABLED = True
ARXIV_COOLDOWN_ENABLED = True

#: Issue 04（第八轮）可靠性组合包：HTTP 层有界重试、陈旧缓存兜底与
#: worker 预热各自独立开关，关闭即回到上一轮行为。
ARXIV_RETRY_ENABLED = True
ARXIV_STALE_FALLBACK_ENABLED = True
ARXIV_WARMUP_ENABLED = True

#: 自动重试的最小剩余预算阈值（秒）：剩余阶段预算低于该值时不再重发，
#: 避免重试把整轮预算吃光（单一常量，默认 5 秒）。
ARXIV_RETRY_MIN_BUDGET_SECONDS = 5.0

#: 单次搜索的实际上游请求次数上限（首次 + 至多 1 次自动重发）。
ARXIV_MAX_UPSTREAM_ATTEMPTS = 2

#: stale 保留窗口（秒）：缓存条目过期后仍保留 24 小时，仅当本次上游
#: 调用失败（超时/429/网络类）且存在同键条目时作为陈旧结果兜底。
ARXIV_STALE_RETENTION_SECONDS = 24 * 60 * 60

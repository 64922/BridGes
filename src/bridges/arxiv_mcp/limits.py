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

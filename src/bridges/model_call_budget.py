"""结构化模型调用的共享预算常量与截断/重试门槛函数（Issue 06 第七轮）。

该模块不依赖聊天编排包，供 ``bridges.ai`` 网关与 ``bridges.chat.budget``
共同读取，避免提供方层导入 ``bridges.chat`` 时形成循环依赖（与
``bridges.public_search_budget`` 同一约定）。截断与重试门槛的全部算术
集中在本模块与 ``RunBudget`` 方法中，不散落魔法数。

语义（冻结决策 #6）：

- 单次非流式调用的 httpx 超时取 ``min(默认 60s, 剩余预算 − 交接预留)``，
  任何单次调用都不可能吃光整轮预算；剩余不足时使用一个正的最小超时
  下限（仍真实发起一次最小调用，让上游超时形成真实错误，而不是在
  网关层伪造 budget_exceeded）。
- 重试前检查剩余预算是否放得下「退避 + 一次最小调用窗口 + 交接预留」，
  放不下时直接以真实错误终态收尾（重试次数为 0），不等待退避。
"""

#: 非流式模型调用默认超时（秒）；与 QwenApiClient 的默认客户端超时一致，
#: 作为单一常量来源被网关截断算术引用。
MODEL_CALL_DEFAULT_TIMEOUT_SECONDS = 60.0

#: 交接预留（秒）：一次调用结束后留给终态收尾（投影/审计/事件）的时间。
#: 截断后的调用超时不得超过「剩余预算 − 交接预留」。
MODEL_CALL_HANDOFF_RESERVE_SECONDS = 1.0

#: 一次最小调用窗口（秒）：重试门要求剩余预算至少能承担「退避 + 一次
#: 最小调用窗口 + 交接预留」，否则不再发起重试。
MODEL_CALL_MIN_WINDOW_SECONDS = 5.0

#: 截断后超时的正下限（秒）：剩余预算放不下「最小调用 + 交接预留」时
#: 仍以该下限真实发起一次调用，让上游超时/错误如实透传；下限必须为正
#: （httpx 拒绝非正超时值）。
MODEL_CALL_MIN_TIMEOUT_SECONDS = 0.05


def model_call_timeout_ms(
    remaining_ms: int,
    *,
    default_ms: int = int(MODEL_CALL_DEFAULT_TIMEOUT_SECONDS * 1000),
    handoff_reserve_ms: int = int(MODEL_CALL_HANDOFF_RESERVE_SECONDS * 1000),
    min_timeout_ms: int = int(MODEL_CALL_MIN_TIMEOUT_SECONDS * 1000),
) -> int:
    """按剩余预算截断单次模型调用的超时（毫秒）。

    返回 ``max(最小下限, min(默认超时, 剩余预算 − 交接预留))``：预算充足
    时保持默认 60 秒；预算不足时截断到剩余预算内，绝不让单次调用吃光
    整轮预算；剩余连交接预留都不够时仍保留一个正的最小下限。
    """
    remaining_ms = max(0, remaining_ms)
    truncated = min(default_ms, remaining_ms - handoff_reserve_ms)
    return max(min_timeout_ms, truncated)


def model_call_can_retry(
    remaining_ms: int,
    *,
    backoff_ms: int = 0,
    min_window_ms: int = int(MODEL_CALL_MIN_WINDOW_SECONDS * 1000),
    handoff_reserve_ms: int = int(MODEL_CALL_HANDOFF_RESERVE_SECONDS * 1000),
) -> bool:
    """重试门：剩余预算是否放得下「退避 + 一次最小调用窗口 + 交接预留」。

    放不下时网关直接以真实错误终态收尾（不等待退避、不发起重试）。
    """
    needed = max(0, backoff_ms) + min_window_ms + handoff_reserve_ms
    return max(0, remaining_ms) >= needed
